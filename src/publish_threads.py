#!/usr/bin/env python3
"""
Threads 社群媒體自動發布腳本 — Taiwan Gray Zone Monitor
從 data.json 讀取最新監測數據，產生摘要圖表，並發布到 Threads。

環境變數:
  THREADS_USER_ID       — Threads 用戶 ID
  THREADS_ACCESS_TOKEN  — Threads API 存取權杖
  THREADS_APP_SECRET    — Threads App Secret
  GITHUB_TOKEN          — GitHub API token（圖片上傳用）

Usage: python publish_threads.py [--dry-run] [--mode daily|weekly]
"""
import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
SRC_DIR = BASE_DIR / "src"

REPO_OWNER = "s0914712"
REPO_NAME = "taiwan-grayzone-monitor"
CHART_DIR = "data/charts"
CHART_FILENAME = "threads_summary.png"
CHART_REPO_PATH = f"{CHART_DIR}/{CHART_FILENAME}"

TW_TZ = timezone(timedelta(hours=8))


def generate_summary(mode="daily"):
    """Run generate_summary.py and return the summary dict."""
    # Import the summary module directly
    sys.path.insert(0, str(SRC_DIR))
    from generate_summary import load_data, compute_daily_summary, compute_weekly_summary

    data = load_data()
    if mode == "weekly":
        return compute_weekly_summary(data), data
    return compute_daily_summary(data), data


def generate_chart(summary, output_path):
    """Generate a simple summary chart image for the Threads post."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("⚠️ matplotlib not available, skipping chart generation")
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor('#0a1628')
    ax.set_facecolor('#0a1628')

    date_str = datetime.now(TW_TZ).strftime("%Y/%m/%d")
    is_weekly = summary.get("type") == "weekly"
    title = f"Taiwan Gray Zone {'Weekly' if is_weekly else 'Daily'} Report — {date_str}"

    ax.text(0.5, 0.92, title, transform=ax.transAxes,
            fontsize=16, fontweight='bold', color='#00f5ff',
            ha='center', va='top')

    # Key metrics as large text blocks
    metrics = [
        ("AIS Vessels", f"{summary.get('ais_total', 0):,}", "#00f5ff"),
        ("Dark Vessels", f"{summary.get('dark_vessels_total', 0)}", "#ff6b6b"),
        ("Suspicious", f"{summary.get('suspicious_count', 0)}", "#ffd93d"),
        ("ID Changes", f"{summary.get('identity_changes_24h', 0)}", "#ff8800"),
    ]

    for i, (label, value, color) in enumerate(metrics):
        x = 0.125 + i * 0.25
        # Value
        ax.text(x, 0.55, value, transform=ax.transAxes,
                fontsize=28, fontweight='bold', color=color,
                ha='center', va='center')
        # Label
        ax.text(x, 0.35, label, transform=ax.transAxes,
                fontsize=10, color='#8899aa',
                ha='center', va='center')

    # FOC info
    foc = summary.get("foc_vessels", 0)
    total = max(summary.get("ais_total", 1), 1)
    pct = round(foc / total * 100, 1)
    ax.text(0.5, 0.12, f"FOC Vessels: {foc} ({pct}%)  |  Cable Faults: {summary.get('cable_faults', 0)}",
            transform=ax.transAxes, fontsize=10, color='#667788', ha='center')

    ax.text(0.5, 0.03, "Taiwan Gray Zone Monitor — s0914712.github.io/taiwan-grayzone-monitor",
            transform=ax.transAxes, fontsize=8, color='#445566', ha='center')

    ax.axis('off')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='#0a1628')
    plt.close(fig)
    print(f"✅ Chart saved: {output_path}")
    return output_path


def upload_chart_to_github(local_path, github_token):
    """Upload chart image to GitHub repo, return raw URL."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{CHART_REPO_PATH}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()

    # Check if file already exists (need sha to update)
    sha = None
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload = {
        "message": f"Update Threads chart — {datetime.now(TW_TZ).strftime('%Y-%m-%d')}",
        "content": content_b64,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201):
        print(f"❌ GitHub upload failed: {resp.status_code} {resp.text}")
        return None

    raw_url = (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}"
        f"/main/{CHART_REPO_PATH}?t={int(datetime.now().timestamp())}"
    )
    print(f"✅ Chart uploaded: {raw_url}")
    return raw_url


def compose_post_text(summary):
    """Compose Threads post text from summary data."""
    from generate_summary import format_text_report
    text = format_text_report(summary)
    # Add project URL
    text += "\n\nhttps://s0914712.github.io/taiwan-grayzone-monitor/"
    return text


def publish_to_threads(text, image_url, user_id, access_token, app_secret):
    """Publish to Threads via Graph API (two-step: create container → publish)."""
    base_url = "https://graph.threads.net/v1.0"

    # Step 1: Create media container
    create_params = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
        "access_token": access_token,
    }
    if image_url:
        create_params["image_url"] = image_url

    resp = requests.post(f"{base_url}/{user_id}/threads", data=create_params)
    if resp.status_code != 200:
        print(f"❌ Create container failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    container_id = resp.json().get("id")
    print(f"✅ Media container created: {container_id}")

    # Wait for server processing
    wait_sec = 30 if image_url else 5
    print(f"⏳ Waiting {wait_sec}s for Threads to process...")
    time.sleep(wait_sec)

    # Step 2: Publish
    publish_params = {
        "creation_id": container_id,
        "access_token": access_token,
    }
    resp = requests.post(f"{base_url}/{user_id}/threads_publish", data=publish_params)
    if resp.status_code != 200:
        print(f"❌ Publish failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    result = resp.json()
    print(f"✅ Published! Post ID: {result.get('id')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Publish Taiwan Gray Zone Monitor summary to Threads")
    parser.add_argument("--dry-run", action="store_true", help="Preview content without publishing")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily", help="Report type")
    parser.add_argument("--chart-dir", default=str(BASE_DIR / CHART_DIR), help="Chart output directory")
    args = parser.parse_args()

    # 1. Generate summary
    print("📊 Generating summary...")
    summary, data = generate_summary(args.mode)
    print(f"  AIS: {summary['ais_total']} | Dark: {summary.get('dark_vessels_total', 0)} | "
          f"Suspicious: {summary.get('suspicious_count', 0)}")

    # 2. Generate chart
    chart_path = os.path.join(args.chart_dir, CHART_FILENAME)
    print("🎨 Generating chart...")
    chart_result = generate_chart(summary, chart_path)

    # 3. Compose post text
    post_text = compose_post_text(summary)
    print("\n📝 Post content:")
    print("─" * 40)
    print(post_text)
    print("─" * 40)

    if args.dry_run:
        print("\n🏁 Dry-run mode — not publishing")
        return

    # 4. Upload chart to GitHub
    image_url = None
    github_token = os.environ.get("GITHUB_TOKEN")
    if chart_result and github_token:
        print("📤 Uploading chart to GitHub...")
        image_url = upload_chart_to_github(chart_path, github_token)
    elif not github_token:
        print("⚠️ GITHUB_TOKEN not set, skipping chart upload")

    # 5. Publish to Threads
    user_id = os.environ.get("THREADS_USER_ID")
    access_token = os.environ.get("THREADS_ACCESS_TOKEN")
    app_secret = os.environ.get("THREADS_APP_SECRET")

    if not all([user_id, access_token, app_secret]):
        print("❌ Missing Threads API env vars (THREADS_USER_ID, THREADS_ACCESS_TOKEN, THREADS_APP_SECRET)")
        sys.exit(1)

    print("📤 Publishing to Threads...")
    publish_to_threads(post_text, image_url, user_id, access_token, app_secret)
    print("\n🎉 Done!")


if __name__ == "__main__":
    main()

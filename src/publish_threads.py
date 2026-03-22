#!/usr/bin/env python3
"""
Threads 社群媒體自動發布腳本 — Taiwan Gray Zone Monitor
從 data.json 讀取最新監測數據，產生摘要圖表，並發布到 Threads。

環境變數:
  THREADS_USER_ID       — Threads 用戶 ID
  THREADS_ACCESS_TOKEN  — Threads API 存取權杖
  THREADS_APP_SECRET    — Threads App Secret
  GITHUB_TOKEN          — GitHub API token（圖片上傳用）
  ANTHROPIC_API_KEY     — Claude API key（LLM 產生貼文用，選填）

Usage: python publish_threads.py [--dry-run] [--mode daily|weekly]
"""
import argparse
import base64
import hashlib
import hmac
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
        ("LNG/Gas", f"{summary.get('lng_vessels', 0)}", "#f0e130"),
        ("ID Changes", f"{summary.get('identity_changes_24h', 0)}", "#ff8800"),
    ]

    n = len(metrics)
    for i, (label, value, color) in enumerate(metrics):
        x = (i + 0.5) / n
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


def collect_5day_briefing(data):
    """Collect recent 5-day statistics from ais_history.json for LLM context."""
    history_path = DOCS_DIR / "ais_history.json"
    if not history_path.exists():
        return None

    with open(history_path, encoding="utf-8") as f:
        history = json.load(f)

    now = datetime.now(TW_TZ)
    cutoff = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    recent = [e for e in history if e.get("date", "") >= cutoff]
    if not recent:
        return None

    # Aggregate per-day stats
    from collections import defaultdict
    daily = defaultdict(lambda: {"counts": [], "fishing": [], "suspicious": [], "by_type": defaultdict(list)})
    for e in recent:
        day = e["date"][:10]
        s = e.get("stats", {})
        daily[day]["counts"].append(s.get("total_vessels", 0))
        daily[day]["fishing"].append(s.get("fishing_vessels", 0))
        daily[day]["suspicious"].append(s.get("suspicious_count", 0))
        for t, c in s.get("by_type", {}).items():
            daily[day]["by_type"][t].append(c)

    days_summary = []
    for day in sorted(daily.keys()):
        d = daily[day]
        avg_total = round(sum(d["counts"]) / len(d["counts"]))
        avg_fishing = round(sum(d["fishing"]) / len(d["fishing"]))
        max_susp = max(d["suspicious"]) if d["suspicious"] else 0
        types_avg = {t: round(sum(v) / len(v)) for t, v in d["by_type"].items()}
        days_summary.append({
            "date": day,
            "avg_vessels": avg_total,
            "avg_fishing": avg_fishing,
            "max_suspicious": max_susp,
            "types": types_avg,
        })

    # Current snapshot extras
    susp_analysis = data.get("suspicious_analysis", {})
    dark = data.get("dark_vessels", {})
    identity = data.get("identity_events", {})

    # LNG vessels from current snapshot
    vessels = data.get("ais_snapshot", {}).get("vessels", [])
    lng_count = sum(1 for v in vessels if v.get("is_lng"))

    return {
        "days": days_summary,
        "current_suspicious_count": susp_analysis.get("summary", {}).get("suspicious_count", 0),
        "dark_vessels_total": dark.get("overall", {}).get("dark_vessels", 0),
        "identity_changes_24h": identity.get("summary", {}).get("count_24h", 0),
        "identity_changes_7d": identity.get("summary", {}).get("count_7d", 0),
        "lng_vessels": lng_count,
    }


def generate_llm_post(summary, data):
    """Use Claude API to generate a witty, informative 5-day briefing for Threads."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("⚠️ ANTHROPIC_API_KEY not set, falling back to template text")
        return None

    briefing = collect_5day_briefing(data)
    if not briefing:
        print("⚠️ No 5-day history available, falling back to template text")
        return None

    # Build context for LLM
    days_text = []
    for d in briefing["days"]:
        types_str = ", ".join(f"{t}: {c}" for t, c in sorted(d["types"].items(), key=lambda x: -x[1])[:5])
        days_text.append(
            f"  {d['date']}: 平均 {d['avg_vessels']} 艘 (漁船 {d['avg_fishing']}, "
            f"可疑 {d['max_suspicious']}) [{types_str}]"
        )

    context = f"""近 5 日台灣周邊灰色地帶海域監測數據：
{chr(10).join(days_text)}

今日摘要：
- AIS 船隻總數: {summary.get('ais_total', 0)}
- SAR 暗船: {briefing['dark_vessels_total']}
- 可疑船隻 (CSIS 方法): {briefing['current_suspicious_count']}
- LNG/天然氣船: {summary.get('lng_vessels', 0)}
- 24h 內 AIS 身份變更: {briefing['identity_changes_24h']}
- 7日內身份變更: {briefing['identity_changes_7d']}
- 權宜船 (FOC): {summary.get('foc_vessels', 0)}"""

    prompt = f"""你是一條養在 GitHub 上的蝦子，每天都被主人虐待找資料。
請用這個角色設定，根據以下近 5 天的數據，用**中文**撰寫一則 Threads 週報貼文。

要求：
1. 開頭用「我是一條養在 GitHub 上的蝦子，每天都被主人虐待找資料」起手
2. 語氣：詼諧幽默、知性、帶點嘲諷但不失專業，用蝦子的視角吐槽海上那些船
3. 長度：150~280 字（不含 hashtag）
4. 用數據說故事，點出本週趨勢變化（增減、異常）
5. 可以用 1-2 個 emoji 點綴，但不要太多
6. 結尾加上這些 hashtag: #TaiwanSecurity #GrayZone #OSINT #MaritimeSecurity
7. 最後一行加上: https://s0914712.github.io/taiwan-grayzone-monitor/
8. 不要用 markdown 格式，純文字即可

{context}

直接輸出貼文內容，不要加任何前言或解釋。"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"⚠️ Claude API error: {resp.status_code} {resp.text[:200]}")
            return None

        result = resp.json()
        text = result["content"][0]["text"].strip()
        print(f"✅ LLM generated post ({len(text)} chars)")
        return text

    except Exception as e:
        print(f"⚠️ Claude API call failed: {e}")
        return None


def compose_post_text(summary, data=None):
    """Compose Threads post text — try LLM first, fall back to template."""
    # Try LLM-powered witty post
    if data:
        llm_text = generate_llm_post(summary, data)
        if llm_text:
            return llm_text

    # Fallback: deterministic template
    from generate_summary import format_text_report
    text = format_text_report(summary)
    text += "\n\nhttps://s0914712.github.io/taiwan-grayzone-monitor/"
    return text


def _make_appsecret_proof(access_token, app_secret):
    """Generate appsecret_proof = HMAC-SHA256(access_token, app_secret)."""
    return hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def publish_to_threads(text, image_url, user_id, access_token, app_secret):
    """Publish to Threads via Graph API (two-step: create container → publish)."""
    base_url = "https://graph.threads.net/v1.0"

    # appsecret_proof prevents "Failed to decrypt" error (code 190)
    proof = _make_appsecret_proof(access_token, app_secret) if app_secret else None

    # Step 1: Create media container
    create_params = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
        "access_token": access_token,
    }
    if proof:
        create_params["appsecret_proof"] = proof
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
    if proof:
        publish_params["appsecret_proof"] = proof
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
          f"Suspicious: {summary.get('suspicious_count', 0)} | LNG: {summary.get('lng_vessels', 0)}")

    # 2. Generate chart
    chart_path = os.path.join(args.chart_dir, CHART_FILENAME)
    print("🎨 Generating chart...")
    chart_result = generate_chart(summary, chart_path)

    # 3. Compose post text (LLM-powered if ANTHROPIC_API_KEY is set)
    post_text = compose_post_text(summary, data)
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

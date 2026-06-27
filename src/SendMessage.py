#!/usr/bin/env python3
"""
LINE Bot 每日報告推送 — Taiwan Gray Zone Monitor

從 docs/data.json 讀取最新監測數據，產生一則中文每日簡報
（可用 Gemini LLM 潤飾，否則退回固定模板），並以 LINE Push Message API
推送給指定使用者。報告聚焦兩件事：

  1. 中國公務／關注船（海警／海巡／海救／科研·情報船）的出沒與灰色地帶「入侵」意涵
  2. 本週商船危險係數最高者（cargo/tanker/lng，依海纜旁低速滯留時數排序）
     —— 並附上該船的航跡圖

環境變數:
  LINE_CHANNEL_ACCESS_TOKEN  — LINE Messaging API 頻道存取權杖（必填，推送授權用）
                               （亦接受 LINECHANNELACCESSTOKEN）
  LINE_USER_ID               — 推送目標使用者 ID（必填）（亦接受 USERID）
  GEMINI_API_KEY             — Google Gemini API key（LLM 產文用，選填）
  GITHUB_TOKEN               — 上傳航跡圖到 repo 以取得公開圖片 URL（選填）
  LLM_MODEL                  — 覆寫 LLM 模型名稱（選填，預設 gemini-2.0-flash）

Usage:
  python src/SendMessage.py            # 產生報告（含圖片）並推送
  python src/SendMessage.py --dry-run  # 只印出報告內容、產生圖片，不推送
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
DOCS_DIR = BASE_DIR / "docs"
sys.path.insert(0, str(SRC_DIR))

from generate_summary import load_data, compute_daily_summary  # noqa: E402
from publish_threads import (  # noqa: E402
    _collect_gov_vessels_context,
    generate_track_map,
    upload_charts_to_github,
)

TW_TZ = timezone(timedelta(hours=8))
SITE_URL = "https://s0914712.github.io/taiwan-grayzone-monitor/"

# LLM 產文模型（Google Generativelanguage API；預設 Gemini，可用 LLM_MODEL 覆寫）
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.0-flash")

CARGO_TYPES = {"cargo", "tanker", "lng"}
MIN_TRACK_POINTS = 15
CHART_DIR = "data/charts"

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_MAX_CHARS = 4900  # LINE 單則文字訊息上限 5000 字，預留緩衝


def _get_env(*names):
    """Return the first non-empty value among the given env var names."""
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def select_top_commercial_vessel(data):
    """從 data.json 的 suspicious_analysis 挑出本週危險係數最高的商船。

    優先：cargo/tanker/lng，依海纜旁低速滯留時數（loiter_slow_hours）排序，
    其次比 risk_score。若沒有商船，退回整體分數最高的可疑船隻。
    """
    sa = data.get("suspicious_analysis", {})
    vessels = sa.get("suspicious_vessels", []) or []

    def _loiter(v):
        return v.get("cable_details", {}).get("loiter_slow_hours", 0) or 0

    def _score(v):
        return v.get("risk_score", 0) or 0

    cargo = [v for v in vessels if v.get("vessel_type") in CARGO_TYPES]
    cargo.sort(key=lambda v: (_loiter(v), _score(v)), reverse=True)
    if cargo:
        return cargo[0]

    if vessels:
        return max(vessels, key=_score)
    return None


def load_vessel_track(mmsi):
    """讀取某艘船的航跡（docs/vessel_routes/{mmsi}.json）。點數不足回 None。"""
    route_file = DOCS_DIR / "vessel_routes" / f"{mmsi}.json"
    if not route_file.exists():
        return None
    try:
        with open(route_file, encoding="utf-8") as f:
            route = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    track = route.get("track", [])
    return track if len(track) >= MIN_TRACK_POINTS else None


def _vessel_brief_line(v):
    """單行描述某艘可疑船隻（給模板 / LLM context 用）。"""
    name_raw = (v.get("names") or ["Unknown"])[0]
    name = name_raw.split("--")[0]
    cable_det = v.get("cable_details", {})
    loiter_h = round(cable_det.get("loiter_slow_hours", 0) or 0)
    loiter_days = round(loiter_h / 24, 1)
    loiter_str = f"{loiter_days} 天（{loiter_h} 小時）" if loiter_h >= 24 else f"{loiter_h} 小時"
    cables = cable_det.get("cables_nearby", []) or []
    vtype = v.get("vessel_type", "unknown")
    return (
        f"- MMSI {v.get('mmsi', '?')}｜船型 {vtype}｜名稱 {name}\n"
        f"  海纜旁低速滯留：{loiter_str}\n"
        f"  靠近海纜：{', '.join(cables[:3]) if cables else 'N/A'}\n"
        f"  風險等級 {v.get('risk_level', '?')}｜分數 {v.get('risk_score', '?')}"
    )


def generate_llm_report(summary, data, top_vessel=None):
    """以 Gemini 產生一則中文每日報告。失敗回傳 None。"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY 未設定，改用固定模板")
        return None

    context = f"""今日威脅概況：
- 可疑船隻 (CSIS 方法): {summary.get('suspicious_count', 0)}
- 權宜船 (FOC): {summary.get('foc_vessels', 0)}
- 海纜異常/中斷: {summary.get('cable_faults', 0)}"""

    vessel_context = ""
    if top_vessel:
        vessel_context = "\n\n本週海纜附近危險係數最高的商船：\n" + _vessel_brief_line(top_vessel)

    gov_context = _collect_gov_vessels_context(data)

    prompt = f"""你是台灣周邊海域灰色地帶監測系統的每日簡報員。
請根據以下數據，用**繁體中文**撰寫一則 LINE 每日推送報告。

報告聚焦兩件事，其餘數字不要展開：
1. 中國公務／關注船：若資料中出現海警、海巡、海救、科研／情報船，務必用一段話點名說明其「入侵」意涵：
   - 海警船：以執法為名在台灣周邊海域常態化巡弋、施壓，是典型灰色地帶脅迫手段
   - 科研／情報船（如「同濟號」「向陽紅18號」「東方紅3號」）：名為海洋科研，
     實則曾涉嫌違法投放儀器、闖入台灣限制水域進行水文與海底地形測繪，具軍事偵察用途
   只描述資料中實際出現的船種，沒出現的就別硬掰；若完全沒有，就簡短說今日未偵測到中國公務船。
2. 本週商船危險係數最高者：報告船名、船型、在海纜附近低速滯留多久、風險等級與分數，
   並用一句話解讀其威脅意涵（例如疑似錨拖海纜或偵察海底設施）。文中提到「下方附上其航跡圖」。

其他要求：
- 開頭一句點出今天的整體態勢即可，不要逐條列出 AIS 船隻總數、暗船、LNG 等數字。
- 語氣：知性、專業、精簡，像一份每日國安情資簡報。
- 長度：200~350 字。
- 純文字即可，不要用 markdown 格式（不要 # * ` 等符號）。
- 最後一行加上網址：{SITE_URL}

{context}{vessel_context}{gov_context}

直接輸出報告內容，不要加任何前言或解釋。"""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{LLM_MODEL}:generateContent?key={api_key}",
            headers={"content-type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 800, "temperature": 0.8},
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"⚠️ LLM API error ({LLM_MODEL}): {resp.status_code} {resp.text[:200]}")
            return None
        result = resp.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"✅ LLM 產生報告（{len(text)} 字）")
        return text
    except Exception as e:
        print(f"⚠️ LLM API 呼叫失敗 ({LLM_MODEL}): {e}")
        return None


def build_template_report(summary, top_vessel=None, data=None):
    """LLM 不可用時的固定模板報告。"""
    date_str = datetime.now(TW_TZ).strftime("%Y/%m/%d")
    lines = [f"🌊 台灣灰色地帶海域每日簡報 — {date_str}", ""]

    if data is not None:
        gov = _collect_gov_vessels_context(data).strip()
        if gov:
            lines.append(gov)
        else:
            lines.append("今日監測海域未偵測到中國公務／關注船。")
        lines.append("")

    if top_vessel:
        lines.append("本週商船危險係數最高者：")
        lines.append(_vessel_brief_line(top_vessel))
        lines.append("（下方附上其航跡圖）")
        lines.append("")

    lines.append(SITE_URL)
    return "\n".join(lines)


def compose_report(summary, data, top_vessel=None):
    """先試 LLM，失敗退回模板。確保末行有網址。"""
    text = generate_llm_report(summary, data, top_vessel=top_vessel)
    if not text:
        text = build_template_report(summary, top_vessel=top_vessel, data=data)

    if SITE_URL not in text:
        text = text.rstrip() + "\n" + SITE_URL
    if len(text) > LINE_MAX_CHARS:
        text = text[: LINE_MAX_CHARS - 1].rstrip() + "…"
    return text


def push_to_line(messages, token, user_id):
    """以 LINE Push Message API 推送一組訊息（最多 5 則）。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"to": user_id, "messages": messages}
    resp = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        print(f"✅ LINE 訊息推送成功！（{len(messages)} 則）")
        return True
    print(f"❌ LINE 推送失敗：{resp.status_code}")
    print(resp.text)
    return False


def main():
    parser = argparse.ArgumentParser(description="推送 Taiwan Gray Zone Monitor 每日報告到 LINE")
    parser.add_argument("--dry-run", action="store_true", help="只印出報告內容、產生圖片，不實際推送")
    args = parser.parse_args()

    print("📊 產生每日摘要...")
    data = load_data()
    summary = compute_daily_summary(data)
    print(f"  Suspicious: {summary.get('suspicious_count', 0)} | FOC: {summary.get('foc_vessels', 0)} "
          f"| Cable faults: {summary.get('cable_faults', 0)}")

    print("🔍 挑選本週危險係數最高的商船...")
    top_vessel = select_top_commercial_vessel(data)
    image_local = None
    if top_vessel:
        name = (top_vessel.get("names") or ["?"])[0].split("--")[0]
        loiter_h = top_vessel.get("cable_details", {}).get("loiter_slow_hours", 0)
        print(f"  → {name} (MMSI: {top_vessel.get('mmsi')}, loiter: {loiter_h}h)")

        track = load_vessel_track(top_vessel["mmsi"])
        if track:
            top_vessel = {**top_vessel, "_track": track}
            out_path = str(BASE_DIR / CHART_DIR / f"line_track_{top_vessel['mmsi']}.png")
            print(f"🗺️  產生航跡圖 {top_vessel['mmsi']}...")
            image_local = generate_track_map(top_vessel, out_path)
        else:
            print("  ⚠️ 航跡點數不足，略過圖片")

    report = compose_report(summary, data, top_vessel=top_vessel)
    print("\n📝 報告內容：")
    print("─" * 40)
    print(report)
    print(f"  [{len(report)} 字]")
    print("─" * 40)

    if args.dry_run:
        if image_local:
            print(f"🖼️  航跡圖已產生：{image_local}")
        print("\n🏁 Dry-run 模式 — 不推送")
        return

    # 上傳航跡圖取得公開 URL（給 LINE 圖片訊息用）
    image_url = None
    github_token = _get_env("GITHUB_TOKEN")
    if image_local and github_token:
        repo_path = f"{CHART_DIR}/line_track_{top_vessel['mmsi']}.png"
        print("📤 上傳航跡圖到 GitHub...")
        urls = upload_charts_to_github([(image_local, repo_path)], github_token)
        image_url = urls[0] if urls else None
    elif image_local:
        print("⚠️ GITHUB_TOKEN 未設定，略過圖片上傳，改傳純文字")

    token = _get_env("LINE_CHANNEL_ACCESS_TOKEN", "LINECHANNELACCESSTOKEN")
    user_id = _get_env("LINE_USER_ID", "USERID")
    if not token or not user_id:
        print("❌ 缺少 LINE 環境變數（LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID）")
        sys.exit(1)

    messages = [{"type": "text", "text": report}]
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        })

    print("📤 推送到 LINE...")
    if not push_to_line(messages, token, user_id):
        sys.exit(1)
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()

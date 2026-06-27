#!/usr/bin/env python3
"""
LINE Bot 每日報告推送 — Taiwan Gray Zone Monitor

從 docs/data.json 讀取最新監測數據，產生一則中文「昨日活動摘要」報告
（可用 Gemini LLM 潤飾，否則退回固定模板），並以 LINE Push Message API
推送給指定使用者。

報告內容：
  1. 昨日／近日台灣周邊灰色地帶海域活動摘要
  2. 本週商船危險係數最高者（cargo/tanker/lng，依海纜旁低速滯留時數排序）
  3. 若監測海域出現中國公務／關注船（海警／海巡／海救／科研·情報船），
     點名說明其灰色地帶「入侵」意涵
  4. 末行附上儀表板網址

環境變數:
  LINE_CHANNEL_ACCESS_TOKEN  — LINE Messaging API 頻道存取權杖（必填，推送授權用）
                               （亦接受 LINECHANNELACCESSTOKEN）
  LINE_USER_ID               — 推送目標使用者 ID（必填）（亦接受 USERID）
  GEMINI_API_KEY             — Google Gemini API key（LLM 產文用，選填）
  LLM_MODEL                  — 覆寫 LLM 模型名稱（選填，預設 gemini-2.0-flash）

Usage:
  python src/SendMessage.py            # 產生報告並推送
  python src/SendMessage.py --dry-run  # 只印出報告內容，不推送
"""
import argparse
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
    collect_5day_briefing,
    _collect_gov_vessels_context,
)

TW_TZ = timezone(timedelta(hours=8))
SITE_URL = "https://s0914712.github.io/taiwan-grayzone-monitor/"

# LLM 產文模型（Google Generativelanguage API；預設 Gemini，可用 LLM_MODEL 覆寫）
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.0-flash")

CARGO_TYPES = {"cargo", "tanker", "lng"}

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

    briefing = collect_5day_briefing(data)

    days_text = []
    if briefing:
        for d in briefing["days"]:
            types_str = ", ".join(
                f"{t}: {c}" for t, c in sorted(d["types"].items(), key=lambda x: -x[1])[:5]
            )
            days_text.append(
                f"  {d['date']}: 平均 {d['avg_vessels']} 艘 (漁船 {d['avg_fishing']}, "
                f"可疑 {d['max_suspicious']}) [{types_str}]"
            )

    context = f"""近日台灣周邊灰色地帶海域監測數據：
{chr(10).join(days_text) if days_text else '  （近日歷史資料暫缺）'}

今日摘要：
- AIS 船隻總數: {summary.get('ais_total', 0)}
- SAR 暗船: {summary.get('dark_vessels_total', 0)}
- 可疑船隻 (CSIS 方法): {summary.get('suspicious_count', 0)}
- LNG/天然氣船: {summary.get('lng_vessels', 0)}
- 權宜船 (FOC): {summary.get('foc_vessels', 0)}
- 24h 內 AIS 身份變更: {summary.get('identity_changes_24h', 0)}
- 海纜異常/中斷: {summary.get('cable_faults', 0)}"""

    vessel_context = ""
    if top_vessel:
        vessel_context = "\n\n本週海纜附近危險係數最高的商船：\n" + _vessel_brief_line(top_vessel)

    gov_context = _collect_gov_vessels_context(data)

    prompt = f"""你是台灣周邊海域灰色地帶監測系統的每日簡報員。
請根據以下數據，用**繁體中文**撰寫一則 LINE 每日推送報告。

要求：
1. 開頭一句點出今天的整體態勢（昨日／近日活動摘要）。
2. 用條列方式簡潔報告關鍵數字：AIS 船隻、SAR 暗船、可疑船隻、權宜船（FOC）等。
3. 重點報告「本週商船危險係數最高者」：船名、船型、在海纜附近低速滯留多久、風險等級，
   並用一句話解讀其威脅意涵（例如疑似錨拖海纜或偵察海底設施）。
4. 若資料中出現中國公務／關注船（海警、海巡、海救、科研／情報船），務必用一段話點名說明其「入侵」意涵：
   - 海警船：以執法為名在台灣周邊海域常態化巡弋、施壓，是典型灰色地帶脅迫手段
   - 科研／情報船（如「同濟號」「向陽紅18號」「東方紅3號」）：名為海洋科研，
     實則曾涉嫌違法投放儀器、闖入台灣限制水域進行水文與海底地形測繪，具軍事偵察用途
   只描述資料中實際出現的船種，沒出現的就別硬掰。
5. 語氣：知性、專業、精簡，像一份每日國安情資簡報。
6. 長度：250~400 字。
7. 純文字即可，不要用 markdown 格式（不要 # * ` 等符號）。
8. 最後一行加上網址：{SITE_URL}

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
    lines.append("昨日／近日活動摘要：")
    lines.append(f"・AIS 船隻總數：{summary.get('ais_total', 0):,}")
    lines.append(f"・SAR 暗船：{summary.get('dark_vessels_total', 0)}")
    lines.append(f"・可疑船隻 (CSIS)：{summary.get('suspicious_count', 0)}")
    lines.append(f"・LNG/天然氣船：{summary.get('lng_vessels', 0)}")
    lines.append(f"・權宜船 (FOC)：{summary.get('foc_vessels', 0)}")
    if summary.get("identity_changes_24h"):
        lines.append(f"・24h AIS 身份變更：{summary['identity_changes_24h']}")
    if summary.get("cable_faults"):
        lines.append(f"・海纜異常／中斷：{summary['cable_faults']}")

    if top_vessel:
        lines.append("")
        lines.append("本週商船危險係數最高者：")
        lines.append(_vessel_brief_line(top_vessel))

    if data is not None:
        gov = _collect_gov_vessels_context(data).strip()
        if gov:
            lines.append("")
            lines.append(gov)

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


def push_to_line(text, token, user_id):
    """以 LINE Push Message API 推送單則文字訊息。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    resp = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        print("✅ LINE 訊息推送成功！")
        return True
    print(f"❌ LINE 推送失敗：{resp.status_code}")
    print(resp.text)
    return False


def main():
    parser = argparse.ArgumentParser(description="推送 Taiwan Gray Zone Monitor 每日報告到 LINE")
    parser.add_argument("--dry-run", action="store_true", help="只印出報告內容，不實際推送")
    args = parser.parse_args()

    print("📊 產生每日摘要...")
    data = load_data()
    summary = compute_daily_summary(data)
    print(
        f"  AIS: {summary.get('ais_total', 0)} | Dark: {summary.get('dark_vessels_total', 0)} | "
        f"Suspicious: {summary.get('suspicious_count', 0)} | LNG: {summary.get('lng_vessels', 0)}"
    )

    print("🔍 挑選本週危險係數最高的商船...")
    top_vessel = select_top_commercial_vessel(data)
    if top_vessel:
        name = (top_vessel.get("names") or ["?"])[0].split("--")[0]
        loiter_h = top_vessel.get("cable_details", {}).get("loiter_slow_hours", 0)
        print(f"  → {name} (MMSI: {top_vessel.get('mmsi')}, loiter: {loiter_h}h)")

    report = compose_report(summary, data, top_vessel=top_vessel)
    print("\n📝 報告內容：")
    print("─" * 40)
    print(report)
    print(f"  [{len(report)} 字]")
    print("─" * 40)

    if args.dry_run:
        print("\n🏁 Dry-run 模式 — 不推送")
        return

    token = _get_env("LINE_CHANNEL_ACCESS_TOKEN", "LINECHANNELACCESSTOKEN")
    user_id = _get_env("LINE_USER_ID", "USERID")
    if not token or not user_id:
        print("❌ 缺少 LINE 環境變數（LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID）")
        sys.exit(1)

    print("📤 推送到 LINE...")
    if not push_to_line(report, token, user_id):
        sys.exit(1)
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()

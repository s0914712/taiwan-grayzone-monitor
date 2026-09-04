#!/usr/bin/env python3
"""
LINE Bot 每日報告推送 — Taiwan Gray Zone Monitor

從 docs/data.json 讀取最新監測數據，產生一則中文每日簡報
（可用 Gemini LLM 潤飾，否則退回固定模板），並以 LINE Push Message API
推送給指定使用者。報告聚焦兩件事：

  1. 昨日（台灣時間日曆日）中國海警船**海上**動態 —— 逐船點名出現時段、位置、
     速度、日內移動距離與最近距台灣基線的法域，其餘公務船（海巡／海救／科研·
     情報）只報艘數；並附上昨日海警船航跡圖。
     靠港停泊（廈門、福州等港灣）不算海上活動，一律不列入報告與地圖
  2. 本週商船危險係數最高者（cargo/tanker/lng，依海纜旁低速滯留時數排序）
     —— 並附上該船的航跡圖

推送內容為 1 則文字 + 最多 2 張圖（海警動態圖、風險最高商船航跡圖）。

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
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(SRC_DIR))

import supabase_store  # noqa: E402
from generate_summary import load_data, compute_daily_summary  # noqa: E402
from publish_threads import (  # noqa: E402
    generate_track_map,
    upload_charts_to_github,
)
from gov_daily_activity import (  # noqa: E402
    annotate_zones,
    at_sea_only,
    build_daily_gov_map,
    category_counts,
    collect_daily_gov_activity,
    load_tier1_entries,
    summarize_activity,
    tw_day_window,
)

TW_TZ = timezone(timedelta(hours=8))
SITE_URL = "https://s0914712.github.io/taiwan-grayzone-monitor/"

# LLM 產文模型（Google Generativelanguage API；預設 Gemini，可用 LLM_MODEL 覆寫）
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.0-flash")

CARGO_TYPES = {"cargo", "tanker", "lng"}
MIN_TRACK_POINTS = 15
CHART_DIR = "data/charts"
# 昨日海警船動態圖檔名（固定名稱：每天覆寫同一路徑，不累積歷史圖檔）
GOV_MAP_NAME = "line_gov_daily.png"
# 週/月報推送用的圖檔（固定檔名，每次覆寫；歷史版本在 docs/reports/ 內）
HOTSPOT_MAP_NAME = "line_{mode}_hotspots.png"
BREAKDOWN_NAME = "line_{mode}_breakdown.png"
WEEKLY_PAGE_URL = SITE_URL + "weekly-report.html"
PERIOD_DIRS = {"weekly": "weekly", "monthly": "monthly"}

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_MAX_CHARS = 4900  # LINE 單則文字訊息上限 5000 字，預留緩衝
LINE_MAX_IMAGES = 4    # 單次 push 最多 5 則訊息（1 則文字 + 最多 4 張圖）


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
    """讀取某艘船的航跡。本地檔優先，缺檔時改點查 Supabase。點數不足回 None。"""
    route = None
    route_file = DATA_DIR / "vessel_routes" / f"{mmsi}.json"
    if route_file.exists():
        try:
            with open(route_file, encoding="utf-8") as f:
                route = json.load(f)
        except (json.JSONDecodeError, IOError):
            route = None
    if route is None:
        route = supabase_store.fetch_route(mmsi)
    if route is None:
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


def generate_llm_report(summary, data, top_vessel=None, gov_context=""):
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

    gov_block = f"\n\n{gov_context}" if gov_context else ""

    prompt = f"""你是台灣周邊海域灰色地帶監測系統的每日簡報員。
請根據以下數據，用**繁體中文**撰寫一則 LINE 每日推送報告。

報告聚焦兩件事，其餘數字不要展開：
1. 昨日中國海警船「海上」動態（重點）：依「昨日中國公務船海上動態」資料，點名說明昨天有哪幾艘
   海警船在海上、在什麼時段出現、位置在哪、跑了多遠或是否定點停留、最近距台灣基線多少浬
   （落在領海／鄰接區／EEZ 哪一層）。並用一到兩句解讀其「入侵」意涵：
   - 海警船：以執法為名在台灣周邊海域常態化巡弋、施壓，是典型灰色地帶脅迫手段；
     越靠近基線（領海、鄰接區）代表壓迫強度越高
   - 若同時出現科研／情報船（如「同濟號」「向陽紅18號」「東方紅3號」「實驗2號」），可補一句：
     名為海洋科研，實則曾涉嫌違法投放儀器、闖入台灣限制水域進行水文與海底地形測繪，具軍事偵察用途
   靠港停泊不是活動，資料已排除，不要提到港內的船。
   只描述資料中實際出現的船種與船名，沒出現的別硬掰；若昨日沒有海警船在海上，就直說未偵測到，
   並改述其他在海上的公務船（海巡／海救／科研）艘數。文中提到「下方附上昨日海警船動態圖」。
2. 本週商船危險係數最高者：報告船名、船型、在海纜附近低速滯留多久、風險等級與分數，
   並用一句話解讀其威脅意涵（例如疑似錨拖海纜或偵察海底設施）。文中提到「下方附上其航跡圖」。

其他要求：
- 開頭一句點出整體態勢即可，不要逐條列出 AIS 船隻總數、暗船、LNG 等數字。
- 分段：第一段海警／公務船動態，第二段最高風險商船。
- 語氣：知性、專業、精簡，像一份每日國安情資簡報。
- 長度：300~450 字。
- 純文字即可，不要用 markdown 格式（不要 # * ` 等符號）。
- 最後一行加上網址：{SITE_URL}

{context}{gov_block}{vessel_context}

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


def build_template_report(summary, top_vessel=None, data=None, gov_context=""):
    """LLM 不可用時的固定模板報告。"""
    date_str = datetime.now(TW_TZ).strftime("%Y/%m/%d")
    lines = [f"🌊 台灣灰色地帶海域每日簡報 — {date_str}", ""]

    if gov_context:
        lines.append(gov_context.strip())
        lines.append("（下方附上昨日海警船動態圖）")
        lines.append("")

    if top_vessel:
        lines.append("本週商船危險係數最高者：")
        lines.append(_vessel_brief_line(top_vessel))
        lines.append("（下方附上其航跡圖）")
        lines.append("")

    lines.append(SITE_URL)
    return "\n".join(lines)


def compose_report(summary, data, top_vessel=None, gov_context=""):
    """先試 LLM，失敗退回模板。確保末行有網址。"""
    text = generate_llm_report(summary, data, top_vessel=top_vessel,
                               gov_context=gov_context)
    if not text:
        text = build_template_report(summary, top_vessel=top_vessel, data=data,
                                     gov_context=gov_context)

    if SITE_URL not in text:
        text = text.rstrip() + "\n" + SITE_URL
    if len(text) > LINE_MAX_CHARS:
        text = text[: LINE_MAX_CHARS - 1].rstrip() + "…"
    return text


# ══════════════════════════════════════════════════════════════════
# 週報 / 月報（--mode weekly|monthly）
# ══════════════════════════════════════════════════════════════════

def resolve_period_label(mode, today=None):
    """回傳該模式要推送的期別標籤（上一個完整週/月）。"""
    from aggregate_highrisk import previous_iso_week, previous_month
    day = today or datetime.now(timezone.utc).date()
    if mode == "weekly":
        return previous_iso_week(day)[0]
    return previous_month(day)[0]


def load_period_report(mode, today=None, base_dir=None):
    """讀 docs/reports/{weekly,monthly}/<label>.json；不存在回 (label, None)。

    aggregate_highrisk 用相對路徑，這裡一律以 BASE_DIR 為準（LINEBot 由
    workflow 於 repo 根目錄執行，但 SendMessage 自身以絕對路徑取檔較保險）。
    """
    label = resolve_period_label(mode, today)
    root = Path(base_dir) if base_dir else DOCS_DIR
    path = root / "reports" / PERIOD_DIRS[mode] / f"{label}.json"
    if not path.exists():
        return label, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return label, json.load(f)
    except (OSError, ValueError) as e:
        print(f"⚠️ 讀取 {path} 失敗: {e}")
        return label, None


def _fmt_speed(v):
    """均速可能是 None（該格沒有可用 SOG）—— 顯示破折號，絕不顯示 0。"""
    return "—" if v is None else f"{v:g} kn"


def build_period_template(report, mode):
    """週/月報的固定模板訊息（純函式）。"""
    summary = (report or {}).get("summary") or {}
    label = report.get("week") or report.get("month") or ""
    kind = "週報" if mode == "weekly" else "月報"
    lines = [f"📋 台灣灰色地帶 高風險船舶{kind} — {label}",
             f"期間（UTC）：{report.get('start', '?')} ~ {report.get('end', '?')}",
             ""]

    lines.append(
        f"高風險船 {summary.get('unique_highrisk', 0)} 艘"
        f"（critical {summary.get('critical', 0)}、high {summary.get('high', 0)}）")
    lines.append(
        f"海纜旁低速徘徊 {summary.get('cable_loiter_vessels', 0)} 艘，"
        f"合計 {summary.get('cable_loiter_hours_total', 0):g} 小時")
    if summary.get("offshore_loiter_vessels"):
        lines.append(f"離岸長期徘徊商船 {summary['offshore_loiter_vessels']} 艘")
    lines.append("")

    hotspots = (report or {}).get("hotspots") or []
    if hotspots:
        lines.append("🔥 徘徊熱區 TOP3：")
        for i, h in enumerate(hotspots[:3], 1):
            lines.append(
                f"{i}. {h['lat']:.1f}N {h['lon']:.1f}E — "
                f"{h['loiter_hours']:g}h／{h['vessels']} 艘／均速 "
                f"{_fmt_speed(h.get('avg_speed_kn'))}")
        lines.append("")

    vessels = (report or {}).get("vessels") or []
    if vessels:
        lines.append("⚠️ 高風險船 TOP3：")
        for i, v in enumerate(vessels[:3], 1):
            name = v.get("name") or f"MMSI {v.get('mmsi')}"
            loiter = v.get("cable_loiter_hours") or 0
            tail = (f"，海纜滯留 {loiter:g}h／均速 "
                    f"{_fmt_speed(v.get('cable_loiter_avg_speed_kn'))}"
                    if loiter else "")
            lines.append(
                f"{i}. {name}（{v.get('mmsi')}）{v.get('vessel_type', '?')}／"
                f"{v.get('flag_zh', '未知')} 分數 {v.get('max_risk_score', 0)}{tail}")
        lines.append("")

    expected = 7 if mode == "weekly" else 28
    covered = report.get("days_covered", 0)
    if covered < expected:
        lines.append(f"※ 本期僅累積 {covered} 天資料，數字尚不完整。")
    cap = report.get("daily_cap")
    if cap and summary.get("unique_highrisk", 0) >= cap:
        lines.append(f"※ 逐船明細每日取前 {cap} 艘（critical 優先），非全量。")

    lines.append("")
    lines.append(WEEKLY_PAGE_URL)
    return "\n".join(lines)


def compose_period_report(report, mode):
    """先試 LLM 潤稿，失敗退回模板；確保末行有連結並截斷至 LINE 上限。"""
    text = generate_llm_period_report(report, mode)
    if not text:
        text = build_period_template(report, mode)
    if WEEKLY_PAGE_URL not in text:
        text = text.rstrip() + "\n" + WEEKLY_PAGE_URL
    if len(text) > LINE_MAX_CHARS:
        text = text[: LINE_MAX_CHARS - 1].rstrip() + "…"
    return text


def generate_llm_period_report(report, mode):
    """用 Gemini 把週/月報摘要潤成中文簡報；任何失敗回 None（改用模板）。"""
    api_key = _get_env("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY 未設定，改用固定模板")
        return None

    facts = build_period_template(report, mode)
    kind = "週報" if mode == "weekly" else "月報"
    prompt = (
        f"你是台灣海域安全的 OSINT 分析師。以下是本期{kind}的統計事實，"
        "請改寫成一則 LINE 推播（繁體中文，350 字內，條列可用）。"
        "只能使用下列數字，不得杜撰或外推；語氣客觀，"
        "並提醒風險評分是線索排序而非違法認定。"
        f"最後一行必須是網址 {WEEKLY_PAGE_URL}。\n\n{facts}")
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{LLM_MODEL}:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": 800,
                                       "temperature": 0.6}},
            timeout=30)
        if resp.status_code != 200:
            print(f"⚠️ Gemini 回應 {resp.status_code}，改用固定模板")
            return None
        parts = (resp.json().get("candidates") or [{}])[0] \
            .get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception as e:
        print(f"⚠️ Gemini 失敗（{e}），改用固定模板")
        return None


def render_period_images(report, mode):
    """產生熱區圖與統計圖，回傳 [(local_path, repo_path)]。

    matplotlib 匯入失敗（本機沒裝）時只警告不中斷 —— 改推純文字。
    """
    pending = []
    try:
        import report_charts
    except Exception as e:
        print(f"⚠️ 無法載入繪圖模組，改推純文字: {e}")
        return pending

    hot_name = HOTSPOT_MAP_NAME.format(mode=mode)
    hot_path = str(BASE_DIR / CHART_DIR / hot_name)
    try:
        if report_charts.render_hotspot_map(report, hot_path):
            pending.append((hot_path, f"{CHART_DIR}/{hot_name}"))
        else:
            print("ℹ️ 本期無徘徊熱區，略過熱區圖")
    except Exception as e:
        print(f"⚠️ 熱區圖產生失敗: {e}")

    brk_name = BREAKDOWN_NAME.format(mode=mode)
    brk_path = str(BASE_DIR / CHART_DIR / brk_name)
    try:
        if report_charts.render_breakdown_chart(report, brk_path):
            pending.append((brk_path, f"{CHART_DIR}/{brk_name}"))
    except Exception as e:
        print(f"⚠️ 統計圖產生失敗: {e}")
    return pending


def run_period_push(mode, dry_run=False):
    """週/月報推送流程。回傳 process exit code。"""
    kind = "週報" if mode == "weekly" else "月報"
    label, report = load_period_report(mode)
    if not report:
        # 冷啟動或該期尚未產生：不推空訊息，等下一輪
        print(f"⏭️ 找不到{kind} {label} 的報表檔，略過推送"
              f"（資料管線產出後才會有）")
        return 0

    print(f"📋 載入{kind} {label}："
          f"{report.get('summary', {}).get('unique_highrisk', 0)} 艘高風險船／"
          f"熱區 {len(report.get('hotspots') or [])} 格")

    report_text = compose_period_report(report, mode)
    print("\n" + "=" * 50)
    print(report_text)
    print("=" * 50 + f"\n（{len(report_text)} 字）\n")

    print("🗺️  產生熱區圖與統計圖...")
    pending = render_period_images(report, mode)
    for local, _ in pending:
        print(f"  → {local}")

    if dry_run:
        print("🔍 --dry-run：不推送")
        return 0

    image_urls = []
    github_token = _get_env("GITHUB_TOKEN")
    if pending and github_token:
        print(f"📤 上傳 {len(pending)} 張圖到 GitHub...")
        image_urls = upload_charts_to_github(pending, github_token)
    elif pending:
        print("⚠️ GITHUB_TOKEN 未設定，略過圖片上傳，改傳純文字")

    token = _get_env("LINE_CHANNEL_ACCESS_TOKEN", "LINECHANNELACCESSTOKEN")
    user_id = _get_env("LINE_USER_ID", "USERID")
    if not token or not user_id:
        print("❌ 缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID")
        return 1

    messages = [{"type": "text", "text": report_text}]
    for url in image_urls[:LINE_MAX_IMAGES]:
        messages.append({"type": "image", "originalContentUrl": url,
                         "previewImageUrl": url})
    print("📤 推送到 LINE...")
    return 0 if push_to_line(messages, token, user_id) else 1


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
    parser = argparse.ArgumentParser(description="推送 Taiwan Gray Zone Monitor 報告到 LINE")
    parser.add_argument("--dry-run", action="store_true", help="只印出報告內容、產生圖片，不實際推送")
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly"],
                        default="daily",
                        help="daily=每日簡報（預設）；weekly/monthly=高風險船週/月報（含熱區圖）")
    args = parser.parse_args()

    # 週/月報走獨立流程：讀 docs/reports/ 的彙整結果，附熱區圖與統計圖
    if args.mode in ("weekly", "monthly"):
        sys.exit(run_period_push(args.mode, dry_run=args.dry_run))

    print("📊 產生每日摘要...")
    data = load_data()
    summary = compute_daily_summary(data)
    print(f"  Suspicious: {summary.get('suspicious_count', 0)} | FOC: {summary.get('foc_vessels', 0)} "
          f"| Cable faults: {summary.get('cable_faults', 0)}")

    print("🛡️  彙整昨日中國海警／公務船動態...")
    start, end, day_label = tw_day_window(days_back=1)
    gov_activity = annotate_zones(
        collect_daily_gov_activity(load_tier1_entries(), start, end))
    gov_context = summarize_activity(gov_activity, day_label)
    gov_at_sea = at_sea_only(gov_activity)
    print(f"  → {day_label} 公務船 {len(gov_activity)} 艘（海上 {len(gov_at_sea)}／"
          f"整日在港 {len(gov_activity) - len(gov_at_sea)}）{category_counts(gov_at_sea)}")
    gov_image_local = None
    if gov_at_sea:
        gov_out = str(BASE_DIR / CHART_DIR / GOV_MAP_NAME)
        print("🗺️  產生昨日海警船動態圖...")
        gov_image_local = build_daily_gov_map(gov_activity, gov_out, day_label)

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

    report = compose_report(summary, data, top_vessel=top_vessel,
                            gov_context=gov_context)
    print("\n📝 報告內容：")
    print("─" * 40)
    print(report)
    print(f"  [{len(report)} 字]")
    print("─" * 40)

    if args.dry_run:
        for label, path in (("海警船動態圖", gov_image_local), ("商船航跡圖", image_local)):
            if path:
                print(f"🖼️  {label}已產生：{path}")
        print("\n🏁 Dry-run 模式 — 不推送")
        return

    # 上傳圖片取得公開 URL（給 LINE 圖片訊息用）；順序＝報告敘事順序
    pending = []
    if gov_image_local:
        pending.append((gov_image_local, f"{CHART_DIR}/{GOV_MAP_NAME}"))
    if image_local:
        pending.append((image_local, f"{CHART_DIR}/line_track_{top_vessel['mmsi']}.png"))

    image_urls = []
    github_token = _get_env("GITHUB_TOKEN")
    if pending and github_token:
        print(f"📤 上傳 {len(pending)} 張圖到 GitHub...")
        image_urls = upload_charts_to_github(pending, github_token)
    elif pending:
        print("⚠️ GITHUB_TOKEN 未設定，略過圖片上傳，改傳純文字")

    token = _get_env("LINE_CHANNEL_ACCESS_TOKEN", "LINECHANNELACCESSTOKEN")
    user_id = _get_env("LINE_USER_ID", "USERID")
    if not token or not user_id:
        print("❌ 缺少 LINE 環境變數（LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID）")
        sys.exit(1)

    messages = [{"type": "text", "text": report}]
    for url in image_urls[:LINE_MAX_IMAGES]:
        messages.append({
            "type": "image",
            "originalContentUrl": url,
            "previewImageUrl": url,
        })

    print("📤 推送到 LINE...")
    if not push_to_line(messages, token, user_id):
        sys.exit(1)
    print("\n🎉 完成！")


if __name__ == "__main__":
    main()

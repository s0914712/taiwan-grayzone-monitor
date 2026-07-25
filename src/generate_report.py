#!/usr/bin/env python3
"""每日情報報告頁產生器 — Daily intelligence report pages.

讀取已彙整的 docs/data.json、docs/ship_transfers.json、docs/sar_ais_matches.json
與 chips/results.json，輸出當日的：
  * docs/reports/<YYYY-MM-DD>.json — 機器可讀摘要（供 API / 引用）
  * docs/reports/<YYYY-MM-DD>.html — 雙語、可分享、含 OG/JSON-LD 的報告頁
  * docs/reports/index.html        — 所有報告的索引頁
  * docs/reports/chips/*.jpg       — 精選 SAR 切片縮圖（原圖太大不進 Pages 產物）

設計為在 generate_dashboard.py 之後執行；資料缺漏時各欄位安全降級。
除了縮圖用的 Pillow（可選，缺了就直接連 GitHub 原圖）之外只依賴 stdlib。
"""
import json
import html
import time
from datetime import datetime, timezone
from pathlib import Path

from darkship_batch import verdict_key

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
REPORTS = DOCS / "reports"
CHIPS = REPO / "chips"                 # 取證切片原圖（在 repo 根，不在 Pages 產物內）
THUMBS = REPORTS / "chips"             # 報告頁用的縮圖（進 Pages 產物）
BASE = "https://s0914712.github.io/taiwan-grayzone-monitor/"
RAW = "https://raw.githubusercontent.com/s0914712/taiwan-grayzone-monitor/main/"

FEATURED_MAX = 6        # 每日報告最多精選幾張切片
# SAR 斑點雜訊很吃 JPEG（原圖 1.5MB PNG），520px/q72 約 90KB，
# 卡片寬度 240px 下仍有 2× DPI 餘裕；6 張 × 14 天 ≈ 7MB 上限。
THUMB_PX = 520
THUMB_QUALITY = 72
THUMB_KEEP_DAYS = 14    # 縮圖保留天數（依 mtime，見 prune_thumbnails）

VERDICT_BADGES = {
    # key: (emoji, 中文, English, CSS 顏色變數)
    "confirmed": ("✅", "確認實體目標", "Confirmed target", "var(--accent-green)"),
    "weak": ("🟡", "弱目標", "Weak target", "var(--accent-yellow)"),
    "land": ("⚠️", "疑似陸地/固定結構", "Land / fixed structure", "var(--accent-orange)"),
    "none": ("⚪", "無目標", "No target", "var(--text-secondary)"),
    "error": ("❌", "取證失敗", "Failed", "var(--accent-red)"),
}


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def collect_sar_matching():
    """SAR×AIS 重比對成效（docs/sar_ais_matches.json）。缺檔回 None。"""
    m = _load(DOCS / "sar_ais_matches.json")
    s = m.get("summary") if isinstance(m, dict) else None
    if not s:
        return None
    cov = s.get("ais_coverage") or {}
    return {
        "dark_total": s.get("dark_total", 0),
        "infrastructure_filtered": s.get("infrastructure_filtered", 0),
        "rematched_local": s.get("rematched_local", 0),
        "residual_dark": s.get("residual_dark", 0),
        "in_ais_coverage": s.get("in_ais_coverage", 0),
        "no_ais_coverage": s.get("no_ais_coverage", 0),
        "rematch_rate_of_coverage_pct": s.get("rematch_rate_of_coverage_pct", 0),
        "false_dark_removed_pct": s.get("false_dark_removed_pct", 0),
        "ais_vessels_indexed": s.get("ais_vessels_indexed", 0),
        "s1_real_pass_dates": s.get("s1_real_pass_dates", 0),
        "ais_coverage_start": cov.get("start"),
        "ais_coverage_end": cov.get("end"),
        "updated_at": m.get("updated_at"),
    }


def _chip_row(r):
    """chips/results.json 的一筆 → 報告用的精簡紀錄。"""
    png = str(r.get("png") or "")
    name = png.split("/")[-1]
    return {
        "date": r.get("date"),
        "lat": r.get("lat"),
        "lon": r.get("lon"),
        "time": (str(r.get("time"))[:5] if r.get("time") else None),
        "zone": r.get("zone"),
        "jurisdiction": r.get("jurisdiction"),
        "verdict": verdict_key(r),
        "length_m": r.get("length_m"),
        "peak_ratio": r.get("peak_ratio"),
        "n_pixels": r.get("n_pixels"),
        "product": r.get("product"),
        "png": png or None,
        "chip_url": (RAW + png) if png else None,
        "thumb": None,          # make_thumbnails() 填入
    }


def collect_forensics():
    """暗船取證批次成效 + 精選切片（chips/results.json）。缺檔回 None。

    注意：切片檔名/`date` 是**成像日期**（GFW 30 天視窗造成比 ran_at 早約一個月），
    判斷新舊只能看 `ran_at`。
    """
    results = _load(CHIPS / "results.json")
    if not isinstance(results, list) or not results:
        return None

    counts_total = {}
    for r in results:
        k = verdict_key(r)
        counts_total[k] = counts_total.get(k, 0) + 1

    runs = [str(r.get("ran_at") or "")[:10] for r in results]
    latest_run = max((d for d in runs if d), default=None)
    run_rows = [r for r in results if str(r.get("ran_at") or "")[:10] == latest_run]

    counts_run = {}
    for r in run_rows:
        k = verdict_key(r)
        counts_run[k] = counts_run.get(k, 0) + 1

    # 精選：本輪有命中（✅+🟡）的，峰值比高者優先；本輪全無命中時，
    # 退回歷來最新的已確認目標，讓報告仍有實質影像證據。
    hits = [r for r in run_rows if verdict_key(r) in ("confirmed", "weak")]
    featured_source = "latest_run"
    if not hits:
        hits = sorted((r for r in results if verdict_key(r) == "confirmed"),
                      key=lambda r: str(r.get("ran_at") or ""), reverse=True)
        featured_source = "recent_confirmed" if hits else "none"
    hits = sorted(hits, key=lambda r: (r.get("peak_ratio") or 0), reverse=True)[:FEATURED_MAX]

    checked = sum(v for k, v in counts_total.items() if k != "error")
    return {
        "latest_run": latest_run,
        "run_count": len(run_rows),
        "total_count": len(results),
        "verdict_counts_run": counts_run,
        "verdict_counts_total": counts_total,
        "confirmed_rate_pct": (round(counts_total.get("confirmed", 0) / checked * 100, 1)
                               if checked else 0),
        "featured_source": featured_source,
        "featured": [_chip_row(r) for r in hits],
        "run_targets": [_chip_row(r) for r in run_rows],
        "report_md_url": (f"{RAW}reports/darkship_report_{latest_run}.md"
                          if latest_run else None),
    }


def make_thumbnails(featured):
    """把精選切片縮成 docs/reports/chips/*.jpg，回傳成功張數。

    Pillow 不在時（或原圖不在這次 checkout 裡）靜默跳過 —— 頁面的 <img onerror>
    會自動退回 raw.githubusercontent 上的原圖。
    """
    if not featured:
        return 0
    try:
        from PIL import Image
    except ImportError:
        print("ℹ️ 未安裝 Pillow，報告頁直接引用 GitHub 原圖")
        return 0

    THUMBS.mkdir(parents=True, exist_ok=True)
    made = 0
    for f in featured:
        if not f.get("png"):
            continue
        src = REPO / f["png"]
        if not src.exists():
            continue
        out = THUMBS / (src.stem + ".jpg")
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((THUMB_PX, THUMB_PX))
                im.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True)
        except OSError:
            continue
        f["thumb"] = f"chips/{out.name}"      # 相對 docs/reports/
        made += 1
    return made


def prune_thumbnails():
    """刪除超過 THUMB_KEEP_DAYS 的縮圖，避免 Pages 產物無限膨脹。

    依 **mtime** 而非檔名日期 —— 檔名帶的是成像日期，會比產生時間早一個月，
    用它剪枝會把剛做好的縮圖立刻刪掉。
    """
    if not THUMBS.is_dir():
        return 0
    cutoff = time.time() - THUMB_KEEP_DAYS * 86400
    removed = 0
    for p in THUMBS.glob("*.jpg"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def collect_stats():
    data = _load(DOCS / "data.json")
    sts = _load(DOCS / "ship_transfers.json")

    updated = data.get("updated_at") or datetime.now(timezone.utc).isoformat()
    date = updated[:10] if len(updated) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    ais = data.get("ais_snapshot") or {}
    ais_vessels = len(ais.get("vessels") or []) or ais.get("vessel_count") or 0

    dark = (data.get("dark_vessels") or {}).get("overall") or {}
    sa = data.get("suspicious_analysis") or {}
    summary = sa.get("summary") or {}
    risk = summary.get("risk_distribution") or {}

    top = []
    for v in (sa.get("suspicious_vessels") or [])[:5]:
        names = v.get("names") or []
        gf = v.get("geofence") or {}
        top.append({
            "mmsi": v.get("mmsi"),
            "name": names[0] if names else v.get("mmsi"),
            "risk_score": v.get("risk_score"),
            "risk_level": v.get("risk_level"),
            "vessel_type": v.get("vessel_type"),
            "zone": gf.get("zone"),
            "cable_band": gf.get("cable_band"),
        })

    sts_sum = sts.get("summary") or {}
    # 海纜鄰近：優先用新版 geofence 緊貼帶，否則退回 5km 粗網計數
    cable_near = summary.get("cable_buffer_1km")
    if cable_near is None:
        cable_near = summary.get("cable_proximity_triggered", 0)

    return {
        "date": date,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_updated_at": updated,
        "ais_vessels": ais_vessels,
        "dark_vessels": dark.get("dark_vessels", 0),
        "dark_ratio": dark.get("dark_ratio", 0),
        "suspicious_count": summary.get("suspicious_count", 0),
        "critical_count": risk.get("critical", 0),
        "high_count": risk.get("high", 0),
        "cable_near_count": cable_near,
        "sts_active": sts_sum.get("active_count", 0),
        "sts_suspicious": sts_sum.get("suspicious_count", 0),
        "top_suspicious": top,
        "sar_ais_matching": collect_sar_matching(),
        "darkship_forensics": collect_forensics(),
    }


# ── HTML 輸出 ────────────────────────────────────────────────────────────────
def _stat_card(value, zh, en):
    return (f'<div class="rp-card"><div class="rp-val">{value}</div>'
            f'<div class="rp-lbl"><span class="lang-zh-only">{zh}</span>'
            f'<span class="lang-en-only">{en}</span></div></div>')


def _top_rows(top):
    if not top:
        return ('<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">'
                '<span class="lang-zh-only">今日無可疑船隻</span>'
                '<span class="lang-en-only">No suspicious vessels today</span></td></tr>')
    rows = []
    for v in top:
        nm = html.escape(str(v["name"]))
        mmsi = html.escape(str(v["mmsi"]))
        zone = html.escape(str(v.get("zone") or "—"))
        band = v.get("cable_band")
        band_tag = ('<span class="rp-cable">≤1km</span>' if band == "within_1km" else
                    (html.escape(band) if band and band != "beyond_10km" else "—"))
        rows.append(
            f'<tr><td><a href="../index.html?mmsi={mmsi}">{nm}</a><br>'
            f'<span style="font-size:10px;color:var(--text-secondary)">{mmsi}</span></td>'
            f'<td>{html.escape(str(v.get("vessel_type") or "—"))}</td>'
            f'<td class="rp-risk rp-{html.escape(str(v.get("risk_level") or ""))}">{v.get("risk_score")}</td>'
            f'<td>{zone}</td><td>{band_tag}</td></tr>')
    return "".join(rows)


def _bi(zh, en):
    """雙語 span（頁面用 body.lang-en 切換，不需要 i18n.js 的鍵）。"""
    return (f'<span class="lang-zh-only">{zh}</span>'
            f'<span class="lang-en-only">{en}</span>')


def _verdict_badge(key):
    emoji, zh, en, color = VERDICT_BADGES.get(key, VERDICT_BADGES["none"])
    return (f'<span class="rp-badge" style="color:{color}">{emoji} '
            f'{_bi(zh, html.escape(en))}</span>')


def render_matching_section(m):
    """暗船比對成效：統計卡 + 漏斗 + 一句白話說明。"""
    if not m:
        return ""
    cards = "".join([
        _stat_card(f'{m["dark_total"]:,}', "SAR 暗偵測", "SAR dark detections"),
        _stat_card(f'{m["infrastructure_filtered"]:,}', "固定設施剔除", "Infra filtered"),
        _stat_card(f'{m["rematched_local"]:,}', "本地 AIS 對上", "Re-matched"),
        _stat_card(f'{m["residual_dark"]:,}', "殘餘暗船", "Residual dark"),
        _stat_card(f'{m["false_dark_removed_pct"]}%', "假暗船剔除率", "False-dark removed"),
        _stat_card(f'{m["no_ais_coverage"]:,}', "AIS 覆蓋外", "Outside coverage"),
    ])
    # 漏斗：三段寬度按筆數比例（總量為 0 時整段省略）
    total = max(m["dark_total"], 1)
    seg = [
        (m["infrastructure_filtered"], "var(--accent-yellow)",
         "固定設施", "Infrastructure"),
        (m["rematched_local"], "var(--accent-green)", "本地對上", "Re-matched"),
        (m["residual_dark"], "var(--accent-red)", "殘餘暗船", "Residual"),
    ]
    bars = "".join(
        f'<div class="rp-fseg" style="flex:{max(n, 0)} 0 auto;background:{c}" '
        f'title="{n}">{n if n / total > 0.08 else ""}</div>'
        for n, c, _zh, _en in seg if n > 0)
    legend = " · ".join(
        f'<span style="color:{c}">■</span> {_bi(zh, en)} {n:,}'
        for n, c, zh, en in seg)
    return f"""
  <h2>{_bi("暗船比對成效", "Dark-vessel verification")}</h2>
  <div class="rp-grid">{cards}</div>
  <div class="rp-funnel">{bars}</div>
  <p class="rp-meta" style="line-height:1.9">{legend}</p>
  <p class="rp-note" style="background:rgba(0,245,255,.04);border-color:rgba(0,245,255,.15)">
    {_bi(f"GFW 判定為「未匹配 AIS」的 {m['dark_total']:,} 筆 SAR 偵測，先剔除 "
         f"{m['infrastructure_filtered']:,} 筆風機／平台等固定設施，再以港務局本地 AIS"
         f"（{m['ais_vessels_indexed']:,} 艘、推算至 Sentinel-1 過境時刻）重新比對，"
         f"對上 {m['rematched_local']:,} 筆假暗船（覆蓋範圍內重比對率 "
         f"{m['rematch_rate_of_coverage_pct']}%），剩下 {m['residual_dark']:,} 筆為殘餘暗船；"
         f"其中 {m['no_ais_coverage']:,} 筆落在本地 AIS 保留期之外，無法驗證。",
         f"Of {m['dark_total']:,} SAR detections GFW flagged as AIS-unmatched, "
         f"{m['infrastructure_filtered']:,} were removed as fixed infrastructure "
         f"(wind farms / platforms), then re-matched against local Port Bureau AIS "
         f"({m['ais_vessels_indexed']:,} vessels, dead-reckoned to the Sentinel-1 "
         f"overpass instant): {m['rematched_local']:,} turned out to be false dark ships "
         f"({m['rematch_rate_of_coverage_pct']}% re-match rate within coverage), leaving "
         f"{m['residual_dark']:,} residual dark detections — of which "
         f"{m['no_ais_coverage']:,} fall outside local AIS retention and cannot be verified.")}
  </p>"""


def _chip_card(c):
    """單張精選切片：縮圖（缺失時退回 GitHub 原圖）+ 判定 caption。"""
    url = html.escape(c.get("chip_url") or "")
    thumb = html.escape(c.get("thumb") or "")
    pos = f'{c.get("lat")}, {c.get("lon")}'
    meta = [f'📅 {html.escape(str(c.get("date") or "—"))}', f'📍 {html.escape(pos)}']
    if c.get("time"):
        meta.append(f'🛰️ {html.escape(c["time"])} UTC')
    if c.get("jurisdiction"):
        meta.append(html.escape(str(c["jurisdiction"])))
    detail = []
    if c.get("length_m"):
        detail.append(f'~{c["length_m"]} m')
    if c.get("peak_ratio"):
        detail.append(f'{c["peak_ratio"]}×'
                      + (f' ({c["n_pixels"]} px)' if c.get("n_pixels") else ""))
    # onerror：縮圖被 14 天汰換規則刪掉後（舊報告頁）自動改抓原圖
    img_src = thumb or url
    onerror = (f' onerror="this.onerror=null;this.src=\'{url}\'"' if thumb and url else "")
    img = (f'<img src="{img_src}" alt="SAR chip {html.escape(pos)}" '
           f'loading="lazy" decoding="async"{onerror}>') if img_src else ""
    link_open = f'<a href="{url}" target="_blank" rel="noopener">' if url else "<div>"
    link_close = "</a>" if url else "</div>"
    return (f'<figure class="rp-chip">{link_open}{img}{link_close}'
            f'<figcaption>{_verdict_badge(c.get("verdict"))}'
            f'<br>{" · ".join(meta)}'
            + (f'<br>{" · ".join(detail)}' if detail else "")
            + f'</figcaption></figure>')


def _forensics_rows(targets):
    rows = []
    for c in targets:
        length = f'~{c["length_m"]} m' if c.get("length_m") else "—"
        peak = f'{c["peak_ratio"]}×' if c.get("peak_ratio") else "—"
        rows.append(
            f'<tr><td>{html.escape(str(c.get("date") or "—"))}</td>'
            f'<td>{c.get("lat")}, {c.get("lon")}</td>'
            f'<td>{html.escape(str(c.get("zone") or "—"))}</td>'
            f'<td>{html.escape(str(c.get("jurisdiction") or "—"))}</td>'
            f'<td>{html.escape(str(c.get("time") or "—"))}</td>'
            f'<td>{_verdict_badge(c.get("verdict"))}</td>'
            f'<td>{length}</td><td>{peak}</td></tr>')
    return "".join(rows)


def render_forensics_section(f):
    """SAR 取證影像：精選切片牆 + 本輪判定表 + 累積統計。"""
    if not f:
        return ""
    if f["featured"]:
        gallery = f'<div class="rp-chips">{"".join(_chip_card(c) for c in f["featured"])}</div>'
        if f["featured_source"] == "recent_confirmed":
            gallery += (f'<p class="rp-meta">'
                        + _bi("本輪未命中亮目標，以下為近期已確認的實體目標。",
                              "No hits in the latest run — showing recently confirmed "
                              "physical targets instead.") + "</p>")
    else:
        gallery = ('<p class="rp-meta">'
                   + _bi("本輪取證尚無命中目標。", "No targets hit in the latest run.")
                   + "</p>")

    ct = f["verdict_counts_total"]
    tally = " · ".join(
        f'{VERDICT_BADGES[k][0]} {ct[k]}' for k in
        ("confirmed", "weak", "land", "none", "error") if ct.get(k))
    md = (f' · <a href="{html.escape(f["report_md_url"])}" target="_blank" rel="noopener">'
          + _bi("本輪取證報告", "Run report") + "</a>") if f.get("report_md_url") else ""

    table = ""
    if f["run_targets"]:
        table = f"""
  <div class="rp-table-wrap"><table class="rp-table">
    <thead><tr>
      <th>{_bi("日期", "Date")}</th><th>{_bi("座標", "Position")}</th>
      <th>{_bi("海域", "Area")}</th><th>{_bi("法域", "Zone")}</th>
      <th>{_bi("成像時刻", "Acquired")}</th><th>{_bi("判定", "Verdict")}</th>
      <th>{_bi("估計長度", "Length")}</th><th>{_bi("峰值比", "Peak")}</th>
    </tr></thead>
    <tbody>{_forensics_rows(f["run_targets"])}</tbody>
  </table></div>"""

    run_label = html.escape(str(f.get("latest_run") or "—"))
    return f"""
  <h2>{_bi("SAR 取證影像", "SAR chip forensics")}</h2>
  <p class="rp-meta">{_bi(f"最新取證批次 {run_label}：{f['run_count']} 筆 · 累積 "
                          f"{f['total_count']} 筆 · 確認率 {f['confirmed_rate_pct']}%",
                          f"Latest run {run_label}: {f['run_count']} targets · "
                          f"{f['total_count']} cumulative · "
                          f"{f['confirmed_rate_pct']}% confirmed")} · {tally}{md}</p>
  {gallery}{table}
  <p class="rp-meta">{_bi("切片由 Sentinel-1 GRD 影像即時裁切（VV 極化、dB 尺度），"
                          "判定為自動亮目標偵測結果，長度為粗估。點圖看全解析度原圖。",
                          "Chips are cut from Sentinel-1 GRD imagery (VV, dB scale); "
                          "verdicts come from automated bright-target detection and "
                          "lengths are rough estimates. Click for the full-resolution image.")}</p>"""


def render_html(s):
    d = s["date"]
    url = f"{BASE}reports/{d}.html"
    dataset_url = f"{BASE}reports/{d}.json"
    forensics = s.get("darkship_forensics")
    featured = (forensics or {}).get("featured") or []
    # 分享卡片優先用當日精選的衛星切片（純數字卡片不如一張 SAR 影像吸引點閱）
    og_image = BASE + "cn_gov_vessel_tracks.png"
    if featured and featured[0].get("thumb"):
        og_image = f"{BASE}reports/{featured[0]['thumb']}"
    elif featured and featured[0].get("chip_url"):
        og_image = featured[0]["chip_url"]
    # The report is an Article-style Report backed by a machine-readable
    # Dataset (the day's JSON), which is in turn derived from data.json.
    ld = {
        "@context": "https://schema.org", "@type": ["Report", "Article"],
        "name": f"Taiwan Gray Zone Maritime Daily Report — {d}",
        "headline": f"Taiwan Gray Zone Maritime Daily Report — {d}",
        "datePublished": d, "dateModified": s["generated_at"], "url": url,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "inLanguage": ["zh-TW", "en"], "isAccessibleForFree": True,
        "license": "https://opensource.org/licenses/MIT",
        "author": {"@id": BASE + "#org"},
        "publisher": {"@id": BASE + "#org"},
        "isPartOf": {"@id": BASE + "#website"},
        "about": [
            {"@type": "Thing", "name": "Taiwan maritime gray-zone activity"},
            {"@type": "Thing", "name": "Submarine cable security"},
            {"@type": "Thing", "name": "Dark vessel detection"},
        ],
        "isBasedOn": {"@id": dataset_url},
        "mentions": [
            {"@type": "WebSite", "@id": BASE + "#website",
             "url": BASE, "name": "Taiwan Gray Zone Monitor"},
            {"@type": "Article",
             "@id": BASE + "blog-methodology.html",
             "url": BASE + "blog-methodology.html",
             "name": "How vessels are scored — methodology"},
        ],
    }
    if featured:
        ld["image"] = [
            {"@type": "ImageObject",
             "contentUrl": c["chip_url"],
             "caption": (f"Sentinel-1 SAR chip of a residual dark detection at "
                         f"{c['lat']}, {c['lon']} on {c['date']} "
                         f"({VERDICT_BADGES.get(c['verdict'], VERDICT_BADGES['none'])[2]})")}
            for c in featured if c.get("chip_url")
        ]
    dataset = {
        "@context": "https://schema.org", "@type": "Dataset",
        "@id": dataset_url,
        "name": f"Taiwan Gray Zone Maritime Daily Report Data — {d}",
        "description": ("Machine-readable daily summary of maritime "
                        "gray-zone activity around Taiwan: AIS vessel count, "
                        "SAR dark vessels, suspicious and critical vessels, "
                        "near-cable and suspicious ship-to-ship counts, "
                        "the day's top suspicious vessels, SAR×AIS re-match "
                        "verification metrics (false-dark removal), and "
                        "Sentinel-1 chip forensics verdicts for residual dark "
                        "detections."),
        "datePublished": d, "dateModified": s["generated_at"],
        "url": url, "contentUrl": dataset_url,
        "encodingFormat": "application/json", "inLanguage": "en",
        "isAccessibleForFree": True,
        "license": "https://opensource.org/licenses/MIT",
        "creator": {"@id": BASE + "#org"},
        "publisher": {"@id": BASE + "#org"},
        "isBasedOn": [f"{BASE}data.json", f"{BASE}sar_ais_matches.json"],
    }
    breadcrumb = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "首頁", "item": BASE},
            {"@type": "ListItem", "position": 2, "name": "每日報告", "item": BASE + "reports/"},
            {"@type": "ListItem", "position": 3, "name": d, "item": url},
        ],
    }
    cards = "".join([
        _stat_card(f'{s["ais_vessels"]:,}', "AIS 船舶", "AIS vessels"),
        _stat_card(f'{s["dark_vessels"]:,}', "暗船 (SAR)", "Dark vessels"),
        _stat_card(f'{s["suspicious_count"]:,}', "可疑船隻", "Suspicious"),
        _stat_card(f'{s["critical_count"]:,}', "極高風險", "Critical"),
        _stat_card(f'{s["cable_near_count"]:,}', "緊貼海纜", "Near cable"),
        _stat_card(f'{s["sts_suspicious"]:,}', "可疑旁靠", "Suspicious STS"),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>台灣海域灰色地帶每日報告 {d} | Taiwan Gray Zone Daily Report</title>
<meta name="description" content="台灣周邊海域灰色地帶活動每日摘要（{d}）：AIS 船舶、暗船、可疑船隻、旁靠與海纜鄰近事件。Daily summary of maritime gray-zone activity around Taiwan.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article">
<meta property="og:title" content="台灣海域灰色地帶每日報告 {d}">
<meta property="og:description" content="AIS {s['ais_vessels']:,} 艘 · 暗船 {s['dark_vessels']:,} · 可疑 {s['suspicious_count']:,} · 可疑旁靠 {s['sts_suspicious']:,}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);}})(window,document,'script','dataLayer','GTM-M82X8QBL');</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" onload="this.onload=null;this.rel='stylesheet'">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap"></noscript>
<link rel="stylesheet" href="../css/main.css">
<meta name="theme-color" content="#0a0f1c">
<style>
.rp-wrap{{max-width:960px;margin:0 auto;padding:20px 16px}}
.rp-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:18px 0}}
.rp-card{{text-align:center;padding:18px 12px;background:var(--bg-card);border:1px solid var(--border-glow);border-radius:10px}}
.rp-val{{font-size:30px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--accent-cyan);line-height:1.1}}
.rp-lbl{{font-size:11px;color:var(--text-secondary);margin-top:6px}}
.rp-table{{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0}}
.rp-table th{{text-align:left;padding:8px 10px;color:var(--accent-cyan);border-bottom:1px solid var(--border-glow);font-size:11px;font-family:'JetBrains Mono',monospace}}
.rp-table td{{padding:8px 10px;border-bottom:1px solid rgba(0,245,255,.06);color:var(--text-secondary)}}
.rp-table a{{color:var(--text-primary);text-decoration:none}}.rp-table a:hover{{color:var(--accent-cyan)}}
.rp-risk{{font-family:'JetBrains Mono',monospace;font-weight:700}}
.rp-critical{{color:#ff3366}}.rp-high{{color:#ff9500}}
.rp-cable{{background:rgba(255,51,102,.15);color:#ff5e8a;padding:1px 7px;border-radius:8px;font-size:10px;font-family:'JetBrains Mono',monospace}}
.rp-note{{background:rgba(255,77,77,.06);border:1px solid rgba(255,77,77,.2);border-radius:6px;padding:12px 16px;margin:16px 0;font-size:12px;line-height:1.8;color:var(--text-secondary)}}
.rp-meta{{font-size:11px;color:var(--text-secondary);font-family:'JetBrains Mono',monospace}}
.rp-table-wrap{{overflow-x:auto}}
.rp-funnel{{display:flex;gap:2px;height:16px;border-radius:8px;overflow:hidden;margin:10px 0 4px}}
.rp-fseg{{display:flex;align-items:center;justify-content:center;font-size:10px;font-family:'JetBrains Mono',monospace;color:#0a0f1c;font-weight:700;min-width:3px}}
.rp-chips{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:12px 0}}
.rp-chip{{margin:0;background:var(--bg-card);border:1px solid var(--border-glow);border-radius:10px;overflow:hidden}}
.rp-chip img{{display:block;width:100%;height:auto;background:#05080f}}
.rp-chip figcaption{{padding:8px 10px;font-size:11px;line-height:1.7;color:var(--text-secondary);font-family:'JetBrains Mono',monospace}}
.rp-badge{{font-weight:700;font-size:11px}}
h1{{font-size:22px;margin:4px 0}}h2{{font-size:15px;color:var(--accent-cyan);margin:22px 0 6px}}
body.lang-en .lang-zh-only{{display:none!important}}body:not(.lang-en) .lang-en-only{{display:none!important}}
</style>
<script type="application/ld+json">
{json.dumps(ld, ensure_ascii=False)}
</script>
<script type="application/ld+json">
{json.dumps(dataset, ensure_ascii=False)}
</script>
<script type="application/ld+json">
{json.dumps(breadcrumb, ensure_ascii=False)}
</script>
</head>
<body>
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-M82X8QBL" height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
<header class="header" style="position:sticky;top:0;z-index:100">
  <h1 data-i18n="title.index" style="font-size:14px">🛰️ 台灣灰色地帶與海底電纜監測</h1>
  <div class="header-info">
    <a href="../index.html" style="font-size:9px;color:var(--text-secondary);text-decoration:none;padding:3px 8px;border:1px solid var(--border-glow);border-radius:4px">← 監測</a>
    <a href="index.html" style="font-size:9px;color:var(--accent-cyan);text-decoration:none;padding:3px 8px;border:1px solid var(--accent-cyan);border-radius:4px;background:rgba(0,245,255,0.1)">報告</a>
    <button id="langToggle" onclick="i18n.toggle()" style="font-size:9px;color:var(--accent-cyan);background:var(--bg-card);border:1px solid var(--accent-cyan);border-radius:4px;padding:3px 8px;cursor:pointer;font-family:'JetBrains Mono',monospace;font-weight:600">EN</button>
  </div>
</header>
<div class="rp-wrap">
  <h1><span class="lang-zh-only">每日海域態勢報告</span><span class="lang-en-only">Daily Maritime Situation Report</span></h1>
  <p class="rp-meta">📅 {d} &nbsp;·&nbsp; <span class="lang-zh-only">資料更新</span><span class="lang-en-only">data updated</span> {html.escape(s['data_updated_at'][:19])}Z</p>

  <h2><span class="lang-zh-only">今日重點</span><span class="lang-en-only">Today at a Glance</span></h2>
  <div class="rp-grid">{cards}</div>
{render_matching_section(s.get('sar_ais_matching'))}
{render_forensics_section(forensics)}

  <h2><span class="lang-zh-only">可疑船隻 Top 5</span><span class="lang-en-only">Top 5 Suspicious Vessels</span></h2>
  <div class="rp-table-wrap"><table class="rp-table">
    <thead><tr>
      <th><span class="lang-zh-only">船舶</span><span class="lang-en-only">Vessel</span></th>
      <th><span class="lang-zh-only">類型</span><span class="lang-en-only">Type</span></th>
      <th><span class="lang-zh-only">分數</span><span class="lang-en-only">Score</span></th>
      <th><span class="lang-zh-only">法域</span><span class="lang-en-only">Zone</span></th>
      <th><span class="lang-zh-only">海纜</span><span class="lang-en-only">Cable</span></th>
    </tr></thead>
    <tbody>{_top_rows(s['top_suspicious'])}</tbody>
  </table></div>

  <div class="rp-note">
    <span class="lang-zh-only"><strong>資料來源與限制：</strong>AIS 可能因轉發延遲或船舶走暗而不完整；SAR 偵測代表雷達可見目標，不必然代表違法；可疑分數為風險排序，並非法律認定。本報告由公開資料自動產生。</span>
    <span class="lang-en-only"><strong>Sources &amp; limits:</strong> AIS may be incomplete due to relay delay or vessels going dark; a SAR detection is a radar-visible target, not proof of illegality; the suspicious score is a risk ranking, not a legal finding. This report is auto-generated from public data.</span>
  </div>

  <p class="rp-meta"><a href="{d}.json" style="color:var(--accent-cyan)">⬇ JSON</a> &nbsp;·&nbsp; <a href="index.html" style="color:var(--accent-cyan)"><span class="lang-zh-only">所有報告</span><span class="lang-en-only">All reports</span></a></p>
</div>
<footer class="site-footer"><p>Contact: <a href="mailto:s0914712@gmail.com">s0914712@gmail.com</a></p></footer>
<script defer src="../js/i18n.js"></script>
<script defer src="../js/mobile-nav.js"></script>
</body>
</html>
"""


def render_index(dates):
    items = "".join(
        f'<li><a href="{d}.html">{d}</a> '
        f'<a href="{d}.json" style="font-size:11px;color:var(--text-secondary)">JSON</a></li>'
        for d in dates)
    ld = {
        "@context": "https://schema.org", "@type": "CollectionPage",
        "name": "Taiwan Gray Zone Maritime Daily Reports",
        "url": BASE + "reports/", "inLanguage": ["zh-TW", "en"],
        "isPartOf": {"@id": BASE + "#website"},
    }
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>每日海域報告 | Daily Maritime Reports — Taiwan Gray Zone Monitor</title>
<meta name="description" content="台灣周邊海域灰色地帶活動的每日自動報告彙整：AIS、暗船、可疑船隻、旁靠與海纜鄰近事件。">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{BASE}reports/index.html">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" onload="this.onload=null;this.rel='stylesheet'">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap"></noscript>
<link rel="stylesheet" href="../css/main.css">
<meta name="theme-color" content="#0a0f1c">
<style>
.rp-wrap{{max-width:760px;margin:0 auto;padding:24px 16px}}
.rp-list{{list-style:none;padding:0}}
.rp-list li{{padding:12px 14px;margin-bottom:8px;background:var(--bg-card);border:1px solid var(--border-glow);border-radius:8px;display:flex;justify-content:space-between;align-items:center}}
.rp-list a{{color:var(--accent-cyan);text-decoration:none;font-family:'JetBrains Mono',monospace}}
</style>
<script type="application/ld+json">
{json.dumps(ld, ensure_ascii=False)}
</script>
</head>
<body>
<header class="header" style="position:sticky;top:0;z-index:100">
  <h1 style="font-size:14px">🛰️ 台灣灰色地帶與海底電纜監測</h1>
  <div class="header-info"><a href="../index.html" style="font-size:9px;color:var(--text-secondary);text-decoration:none;padding:3px 8px;border:1px solid var(--border-glow);border-radius:4px">← 監測</a></div>
</header>
<div class="rp-wrap">
  <h1 style="font-size:22px">📑 每日海域報告 <span style="font-size:13px;color:var(--text-secondary)">Daily Maritime Reports</span></h1>
  <p style="font-size:12px;color:var(--text-secondary);line-height:1.8">由公開資料每日自動產生的台灣周邊海域灰色地帶態勢摘要。Auto-generated daily summaries of maritime gray-zone activity around Taiwan.</p>
  <ul class="rp-list">{items}</ul>
</div>
<footer class="site-footer"><p>Contact: <a href="mailto:s0914712@gmail.com">s0914712@gmail.com</a></p></footer>
</body>
</html>
"""


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    stats = collect_stats()
    d = stats["date"]
    # 縮圖要先做，render_html / JSON 才拿得到 thumb 路徑
    forensics = stats.get("darkship_forensics") or {}
    made = make_thumbnails(forensics.get("featured"))
    pruned = prune_thumbnails()
    if made or pruned:
        print(f"🖼️ 切片縮圖: 新增 {made} 張, 汰換 {pruned} 張")
    (REPORTS / f"{d}.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / f"{d}.html").write_text(render_html(stats), encoding="utf-8")
    dates = sorted({p.stem for p in REPORTS.glob("*.json")}, reverse=True)
    (REPORTS / "index.html").write_text(render_index(dates), encoding="utf-8")
    print(f"📑 報告已產生: reports/{d}.html (+ .json), 索引含 {len(dates)} 份報告")


if __name__ == "__main__":
    main()

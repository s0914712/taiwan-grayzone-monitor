#!/usr/bin/env python3
"""每日情報報告頁產生器 — Daily intelligence report pages.

讀取已彙整的 docs/data.json 與 docs/ship_transfers.json，輸出當日的：
  * docs/reports/<YYYY-MM-DD>.json — 機器可讀摘要（供 API / 引用）
  * docs/reports/<YYYY-MM-DD>.html — 雙語、可分享、含 OG/JSON-LD 的報告頁
  * docs/reports/index.html        — 所有報告的索引頁

設計為在 generate_dashboard.py 之後執行；資料缺漏時各欄位安全降級。
只依賴 stdlib。
"""
import json
import html
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
REPORTS = DOCS / "reports"
BASE = "https://s0914712.github.io/taiwan-grayzone-monitor/"


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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


def render_html(s):
    d = s["date"]
    url = f"{BASE}reports/{d}.html"
    dataset_url = f"{BASE}reports/{d}.json"
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
    dataset = {
        "@context": "https://schema.org", "@type": "Dataset",
        "@id": dataset_url,
        "name": f"Taiwan Gray Zone Maritime Daily Report Data — {d}",
        "description": ("Machine-readable daily summary of maritime "
                        "gray-zone activity around Taiwan: AIS vessel count, "
                        "SAR dark vessels, suspicious and critical vessels, "
                        "near-cable and suspicious ship-to-ship counts, and "
                        "the day's top suspicious vessels."),
        "datePublished": d, "dateModified": s["generated_at"],
        "url": url, "contentUrl": dataset_url,
        "encodingFormat": "application/json", "inLanguage": "en",
        "isAccessibleForFree": True,
        "license": "https://opensource.org/licenses/MIT",
        "creator": {"@id": BASE + "#org"},
        "publisher": {"@id": BASE + "#org"},
        "isBasedOn": f"{BASE}data.json",
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
<meta property="og:image" content="{BASE}cn_gov_vessel_tracks.png">
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

  <h2><span class="lang-zh-only">可疑船隻 Top 5</span><span class="lang-en-only">Top 5 Suspicious Vessels</span></h2>
  <table class="rp-table">
    <thead><tr>
      <th><span class="lang-zh-only">船舶</span><span class="lang-en-only">Vessel</span></th>
      <th><span class="lang-zh-only">類型</span><span class="lang-en-only">Type</span></th>
      <th><span class="lang-zh-only">分數</span><span class="lang-en-only">Score</span></th>
      <th><span class="lang-zh-only">法域</span><span class="lang-en-only">Zone</span></th>
      <th><span class="lang-zh-only">海纜</span><span class="lang-en-only">Cable</span></th>
    </tr></thead>
    <tbody>{_top_rows(s['top_suspicious'])}</tbody>
  </table>

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
    (REPORTS / f"{d}.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / f"{d}.html").write_text(render_html(stats), encoding="utf-8")
    dates = sorted({p.stem for p in REPORTS.glob("*.json")}, reverse=True)
    (REPORTS / "index.html").write_text(render_index(dates), encoding="utf-8")
    print(f"📑 報告已產生: reports/{d}.html (+ .json), 索引含 {len(dates)} 份報告")


if __name__ == "__main__":
    main()

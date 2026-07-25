"""Tests for src/generate_report.py — daily report generation."""
import json
import re

import generate_report as gr


SAMPLE = {
    "date": "2026-06-18",
    "generated_at": "2026-06-18T09:00:00Z",
    "data_updated_at": "2026-06-18T08:54:40Z",
    "ais_vessels": 6110, "dark_vessels": 3505, "dark_ratio": 29.1,
    "suspicious_count": 3738, "critical_count": 351, "high_count": 168,
    "cable_near_count": 183, "sts_active": 53, "sts_suspicious": 175,
    "top_suspicious": [
        {"mmsi": "413324020", "name": "YUAN XIANG YOU 27", "risk_score": 26,
         "risk_level": "critical", "vessel_type": "cargo",
         "zone": "territorial_sea", "cable_band": "within_1km"},
    ],
    "sar_ais_matching": {
        "dark_total": 547, "infrastructure_filtered": 33, "rematched_local": 70,
        "residual_dark": 444, "in_ais_coverage": 458, "no_ais_coverage": 56,
        "rematch_rate_of_coverage_pct": 15.3, "false_dark_removed_pct": 18.8,
        "ais_vessels_indexed": 16939, "s1_real_pass_dates": 4,
        "ais_coverage_start": "2026-06-26T18:52:03Z",
        "ais_coverage_end": "2026-07-25T13:11:23Z",
        "updated_at": "2026-07-25T13:11:57Z",
    },
    "darkship_forensics": {
        "latest_run": "2026-07-23", "run_count": 2, "total_count": 144,
        "verdict_counts_run": {"confirmed": 1, "none": 1},
        "verdict_counts_total": {"confirmed": 6, "weak": 31, "none": 107},
        "confirmed_rate_pct": 4.2, "featured_source": "latest_run",
        "featured": [
            {"date": "2026-06-23", "lat": 21.34, "lon": 119.48, "time": "21:53",
             "zone": "southwest", "jurisdiction": "eez", "verdict": "confirmed",
             "length_m": 471.3, "peak_ratio": 37.7, "n_pixels": 259,
             "product": "S1A_IW_GRDH_1SDV_20260623T215304.SAFE",
             "png": "chips/2026-06-23_21.34_119.48.png",
             "chip_url": gr.RAW + "chips/2026-06-23_21.34_119.48.png",
             "thumb": "chips/2026-06-23_21.34_119.48.jpg"},
        ],
        "run_targets": [
            {"date": "2026-06-23", "lat": 21.34, "lon": 119.48, "time": "21:53",
             "zone": "southwest", "jurisdiction": "eez", "verdict": "confirmed",
             "length_m": 471.3, "peak_ratio": 37.7, "n_pixels": 259,
             "product": None, "png": None, "chip_url": None, "thumb": None},
            {"date": "2026-06-23", "lat": 22.63, "lon": 119.56, "time": "21:53",
             "zone": "southwest", "jurisdiction": "contiguous_zone",
             "verdict": "none", "length_m": None, "peak_ratio": None,
             "n_pixels": None, "product": None, "png": None,
             "chip_url": None, "thumb": None},
        ],
        "report_md_url": gr.RAW + "reports/darkship_report_2026-07-23.md",
    },
}


def _ld_blocks(html):
    return re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)


def test_render_html_valid_and_contains_key_facts():
    out = gr.render_html(SAMPLE)
    assert isinstance(out, str) and "<!DOCTYPE html>" in out
    assert "2026-06-18" in out
    assert "6,110" in out and "3,505" in out          # formatted stat cards
    assert "YUAN XIANG YOU 27" in out
    assert 'href="../index.html?mmsi=413324020"' in out  # vessel permalink
    blocks = _ld_blocks(out)
    assert len(blocks) >= 3
    types = []
    for b in blocks:
        t = json.loads(b).get("@type")
        types += t if isinstance(t, list) else [t]
    for expected in ("Report", "Article", "Dataset", "BreadcrumbList"):
        assert expected in types, expected
    # the Report is backed by the day's Dataset, which derives from data.json
    report = next(json.loads(b) for b in blocks
                  if "Report" in (json.loads(b).get("@type") or []))
    assert report["isBasedOn"]["@id"].endswith("/reports/2026-06-18.json")
    assert any(m.get("@id", "").endswith("blog-methodology.html")
               for m in report["mentions"])
    dataset = next(json.loads(b) for b in blocks
                   if json.loads(b).get("@type") == "Dataset")
    assert any(u.endswith("/data.json") for u in dataset["isBasedOn"])
    assert any(u.endswith("/sar_ais_matches.json") for u in dataset["isBasedOn"])


def test_render_html_escapes_and_handles_empty_top():
    s = dict(SAMPLE, top_suspicious=[])
    out = gr.render_html(s)
    assert "No suspicious vessels today" in out
    for b in _ld_blocks(out):
        json.loads(b)  # still valid


def test_render_html_escapes_vessel_name():
    s = dict(SAMPLE, top_suspicious=[
        {"mmsi": "1", "name": "EVIL <script>", "risk_score": 9,
         "risk_level": "high", "vessel_type": "tanker", "zone": None, "cable_band": None}])
    out = gr.render_html(s)
    assert "<script>" not in out.split("</head>")[1] or "EVIL &lt;script&gt;" in out


def test_render_index_lists_dates():
    out = gr.render_index(["2026-06-18", "2026-06-17"])
    assert "2026-06-18.html" in out and "2026-06-17.html" in out
    assert json.loads(_ld_blocks(out)[0])["@type"] == "CollectionPage"


def test_collect_stats_shape():
    # uses the repo's real docs/data.json; assert keys + types, not values
    s = gr.collect_stats()
    for k in ("date", "ais_vessels", "dark_vessels", "suspicious_count",
              "cable_near_count", "sts_suspicious", "top_suspicious"):
        assert k in s
    assert isinstance(s["ais_vessels"], int)
    assert isinstance(s["top_suspicious"], list)
    assert re.match(r"\d{4}-\d{2}-\d{2}", s["date"])


def test_render_html_shows_matching_effectiveness():
    out = gr.render_html(SAMPLE)
    # funnel numbers + rate, and the plain-language explanation
    for token in ("547", "444", "18.8%", "16,939", "15.3%",
                  "Dark-vessel verification", "暗船比對成效", "rp-funnel"):
        assert token in out, token


def test_render_html_shows_chip_gallery_and_verdicts():
    out = gr.render_html(SAMPLE)
    assert 'class="rp-chips"' in out
    # thumbnail is used, with the raw.githubusercontent original as fallback
    assert 'src="chips/2026-06-23_21.34_119.48.jpg"' in out
    assert gr.RAW + "chips/2026-06-23_21.34_119.48.png" in out
    assert "onerror=" in out and 'loading="lazy"' in out
    assert "確認實體目標" in out and "Confirmed target" in out
    assert "~471.3 m" in out and "37.7×" in out
    # the run's non-hit target only shows up in the verdict table
    assert "contiguous_zone" in out
    assert "144" in out and "4.2%" in out
    # og:image + ImageObject point at the featured chip
    assert 'og:image" content="' + gr.BASE + "reports/chips/" in out
    report = next(json.loads(b) for b in _ld_blocks(out)
                  if "Report" in (json.loads(b).get("@type") or []))
    assert report["image"][0]["contentUrl"].endswith(".png")


def test_render_html_without_forensics_or_matching_degrades():
    s = dict(SAMPLE, sar_ais_matching=None, darkship_forensics=None)
    out = gr.render_html(s)
    assert "暗船比對成效" not in out and "SAR chip forensics" not in out
    assert 'class="rp-chips"' not in out
    assert "og:image" in out and "cn_gov_vessel_tracks.png" in out  # falls back
    assert "YUAN XIANG YOU 27" in out                              # rest intact
    for b in _ld_blocks(out):
        json.loads(b)


def test_render_html_forensics_without_hits_shows_notice():
    f = dict(SAMPLE["darkship_forensics"], featured=[], featured_source="none")
    out = gr.render_html(dict(SAMPLE, darkship_forensics=f))
    assert "本輪取證尚無命中目標" in out
    assert "No targets hit in the latest run" in out
    assert 'class="rp-chips"' not in out
    assert "contiguous_zone" in out  # verdict table still rendered


def test_render_html_forensics_fallback_to_recent_confirmed_is_labelled():
    f = dict(SAMPLE["darkship_forensics"], featured_source="recent_confirmed")
    out = gr.render_html(dict(SAMPLE, darkship_forensics=f))
    assert "本輪未命中亮目標" in out


def test_collect_forensics_and_matching_shapes():
    # runs against the repo's real sar_ais_matches.json + chips/results.json
    m = gr.collect_sar_matching()
    if m is not None:
        for k in ("dark_total", "residual_dark", "false_dark_removed_pct"):
            assert k in m
    f = gr.collect_forensics()
    if f is not None:
        assert f["total_count"] >= f["run_count"] >= 0
        assert len(f["featured"]) <= gr.FEATURED_MAX
        assert set(f["verdict_counts_total"]) <= set(gr.VERDICT_BADGES)
        for c in f["featured"]:
            assert c["chip_url"].startswith(gr.RAW)
            assert c["verdict"] in ("confirmed", "weak")


def test_wide_tables_scroll_in_their_own_container():
    out = gr.render_html(SAMPLE)
    # every rp-table must sit inside an overflow-x container so the page
    # itself never scrolls horizontally on mobile
    assert out.count('<table class="rp-table">') == out.count(
        '<div class="rp-table-wrap"><table class="rp-table">')

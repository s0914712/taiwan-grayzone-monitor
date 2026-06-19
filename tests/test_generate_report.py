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
    assert dataset["isBasedOn"].endswith("/data.json")


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

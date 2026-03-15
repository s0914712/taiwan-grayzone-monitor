#!/usr/bin/env python3
"""
Generate daily/weekly summary reports for Taiwan Gray Zone Monitor.
Reads data.json + ais_track_history.json and outputs a structured summary.
Usage: python generate_summary.py [--mode daily|weekly] [--output PATH]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DATA_JSON = DOCS_DIR / "data.json"
HISTORY_JSON = DOCS_DIR / "ais_track_history.json"
TW_TZ = timezone(timedelta(hours=8))


def load_data():
    """Load current data.json snapshot."""
    with open(DATA_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_history(days=7):
    """Load ais_track_history.json and filter to recent N days."""
    if not HISTORY_JSON.exists():
        return []
    with open(HISTORY_JSON, encoding="utf-8") as f:
        data = json.load(f)
    cutoff = (datetime.now(TW_TZ) - timedelta(days=days)).isoformat()
    return [e for e in data if (e.get("timestamp", "") >= cutoff)]


def compute_daily_summary(data):
    """Generate a daily summary from current data.json."""
    now = datetime.now(TW_TZ)
    summary = {
        "type": "daily",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
    }

    # AIS vessels
    ais = data.get("ais_snapshot", {})
    vessels = ais.get("vessels", [])
    summary["ais_total"] = len(vessels)

    # Count by type
    type_counts = {}
    flag_counts = {}
    for v in vessels:
        t = v.get("type_name", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        f = v.get("flag", "unknown")
        flag_counts[f] = flag_counts.get(f, 0) + 1
    summary["ais_by_type"] = dict(sorted(type_counts.items(), key=lambda x: -x[1]))
    summary["ais_top_flags"] = dict(sorted(flag_counts.items(), key=lambda x: -x[1])[:10])

    # FOC vessels
    foc_flags = {"Panama", "Liberia", "Marshall Islands", "Bahamas", "Malta",
                 "Hong Kong", "Singapore", "Cyprus", "Madeira", "Bermuda",
                 "Antigua and Barbuda", "Cayman Islands", "Comoros", "Cameroon",
                 "Tanzania", "Togo", "Palau", "Sierra Leone", "Belize"}
    foc_count = sum(1 for v in vessels if v.get("flag", "") in foc_flags)
    summary["foc_vessels"] = foc_count

    # Dark vessels (SAR)
    dark = data.get("dark_vessels", {})
    summary["dark_vessels_total"] = dark.get("overall", {}).get("dark_vessels", 0)
    regions = dark.get("regions", {})
    summary["dark_by_region"] = {
        k: v.get("dark_vessel_count", 0) for k, v in regions.items()
    }

    # Suspicious vessels (CSIS)
    susp = data.get("suspicious_analysis", {})
    summary["suspicious_count"] = susp.get("summary", {}).get("suspicious_count", 0)
    summary["suspicious_vessels"] = []
    for sv in susp.get("suspicious_vessels", [])[:5]:
        summary["suspicious_vessels"].append({
            "mmsi": sv.get("mmsi"),
            "names": sv.get("names", []),
            "risk_level": sv.get("risk_level"),
            "flags": sv.get("flags_used", []),
        })

    # Identity change events
    id_events = data.get("identity_events", {})
    summary["identity_changes_24h"] = id_events.get("summary", {}).get("count_24h", 0)
    summary["identity_changes_7d"] = id_events.get("summary", {}).get("count_7d", 0)

    # Cable faults
    try:
        cable_path = DOCS_DIR / "cable_status.json"
        if cable_path.exists():
            with open(cable_path, encoding="utf-8") as f:
                cable_data = json.load(f)
            faults = [f for f in cable_data.get("faults", []) if f.get("status") == "fault"]
            summary["cable_faults"] = len(faults)
        else:
            summary["cable_faults"] = 0
    except Exception:
        summary["cable_faults"] = 0

    return summary


def compute_weekly_summary(data):
    """Generate a weekly summary with trend data from history."""
    daily = compute_daily_summary(data)
    daily["type"] = "weekly"

    # Load 7-day history for trends
    history = load_history(days=7)
    if history:
        daily_counts = []
        for entry in history:
            vessels = entry.get("vessels", [])
            daily_counts.append({
                "timestamp": entry.get("timestamp", ""),
                "vessel_count": len(vessels),
            })
        daily["history_snapshots"] = len(history)
        if daily_counts:
            counts = [d["vessel_count"] for d in daily_counts]
            daily["ais_avg_7d"] = round(sum(counts) / len(counts))
            daily["ais_max_7d"] = max(counts)
            daily["ais_min_7d"] = min(counts)

    return daily


def format_text_report(summary):
    """Format summary as human-readable text for social media."""
    now = datetime.now(TW_TZ)
    is_weekly = summary["type"] == "weekly"
    date_str = now.strftime("%Y/%m/%d")

    lines = []
    if is_weekly:
        lines.append(f"Taiwan Gray Zone Weekly Report — {date_str}")
    else:
        lines.append(f"Taiwan Gray Zone Daily Brief — {date_str}")
    lines.append("")

    # AIS overview
    lines.append(f"AIS Vessels: {summary['ais_total']:,}")
    if summary.get("foc_vessels"):
        pct = round(summary["foc_vessels"] / max(summary["ais_total"], 1) * 100, 1)
        lines.append(f"  FOC Vessels: {summary['foc_vessels']} ({pct}%)")

    # Top types
    top_types = list(summary.get("ais_by_type", {}).items())[:4]
    if top_types:
        type_str = " | ".join(f"{t}: {c}" for t, c in top_types)
        lines.append(f"  Types: {type_str}")
    lines.append("")

    # Dark vessels
    dark = summary.get("dark_vessels_total", 0)
    if dark > 0:
        lines.append(f"SAR Dark Vessels: {dark}")
        by_region = summary.get("dark_by_region", {})
        if by_region:
            region_parts = [f"{k}: {v}" for k, v in by_region.items() if v > 0]
            if region_parts:
                lines.append(f"  Regions: {', '.join(region_parts)}")
        lines.append("")

    # Suspicious
    susp = summary.get("suspicious_count", 0)
    if susp > 0:
        lines.append(f"Suspicious Vessels (CSIS): {susp}")
        for sv in summary.get("suspicious_vessels", [])[:3]:
            name = sv["names"][0] if sv.get("names") else sv.get("mmsi", "?")
            lines.append(f"  - {name} [{sv.get('risk_level', '?')}]")
        lines.append("")

    # Identity changes
    id24 = summary.get("identity_changes_24h", 0)
    if id24 > 0:
        lines.append(f"AIS Identity Changes (24h): {id24}")

    # Cable faults
    cf = summary.get("cable_faults", 0)
    if cf > 0:
        lines.append(f"Submarine Cable Faults: {cf}")

    # Weekly trend
    if is_weekly and summary.get("ais_avg_7d"):
        lines.append("")
        lines.append(f"7-Day AIS Avg: {summary['ais_avg_7d']} (min {summary.get('ais_min_7d', '?')} / max {summary.get('ais_max_7d', '?')})")

    lines.append("")
    lines.append("#TaiwanSecurity #GrayZone #OSINT #MaritimeSecurity")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate Taiwan Gray Zone Monitor summary report")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily", help="Report type")
    parser.add_argument("--output", default=None, help="Output file path (default: stdout)")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    args = parser.parse_args()

    if not DATA_JSON.exists():
        print(f"Error: {DATA_JSON} not found", file=sys.stderr)
        sys.exit(1)

    data = load_data()

    if args.mode == "weekly":
        summary = compute_weekly_summary(data)
    else:
        summary = compute_daily_summary(data)

    if args.format == "json":
        output = json.dumps(summary, ensure_ascii=False, indent=2)
    else:
        output = format_text_report(summary)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Report saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

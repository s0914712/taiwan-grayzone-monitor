#!/usr/bin/env python3
"""
fetch_cable_status.py — 海纜障礙狀態爬蟲
Scrapes submarine cable fault status from MODA (數位發展部).
Source: https://moda.gov.tw/major-policies/subseacable/fault/1749

Outputs: docs/cable_status.json (consumed by frontend sidebar + map layer)
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Paths ──
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
OUTPUT_FILE = DOCS_DIR / "cable_status.json"

# ── Source ──
MODA_URL = "https://moda.gov.tw/major-policies/subseacable/fault/1749"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Cable Name → GeoJSON Slug Mapping ──
# Maps Chinese/English cable names from MODA table to taiwan_cables.json slug.
# Multiple aliases per cable to handle variations in MODA naming.
CABLE_NAME_TO_SLUG = {
    # EAC / C2C family
    "EAC1": "eac-c2c",
    "EAC2": "eac-c2c",
    "C2C": "eac-c2c",
    "東亞交匯一號": "eac-c2c",
    "東亞交匯二號": "eac-c2c",
    "市通市": "eac-c2c",
    # APCN-2
    "APCN2": "apcn-2",
    "亞太網路二號": "apcn-2",
    # APG
    "APG": "asia-pacific-gateway-apg",
    "亞太閘道": "asia-pacific-gateway-apg",
    "亞太直達海纜": "asia-pacific-gateway-apg",
    # SJC / SJC2
    "SJC2": "southeast-asia-japan-cable-2-sjc2",
    "東南亞日本二號": "southeast-asia-japan-cable-2-sjc2",
    "SJC": "southeast-asia-japan-cable-sjc",
    # PLCN
    "PLCN": "pacific-light-cable-network-plcn",
    "太平洋光纜": "pacific-light-cable-network-plcn",
    # TPE
    "TPE": "trans-pacific-express-tpe-cable-system",
    "跨太平洋快線": "trans-pacific-express-tpe-cable-system",
    # FNAL / RNAL
    "FNAL": "flag-north-asia-loopreach-north-asia-loop",
    "RNAL": "flag-north-asia-loopreach-north-asia-loop",
    "北亞光纜": "flag-north-asia-loopreach-north-asia-loop",
    "北亞海纜": "flag-north-asia-loopreach-north-asia-loop",
    # FEA
    "FEA": "flag-europe-asia-fea",
    # TSE-1
    "TSE-1": "taiwan-strait-express-1-tse-1",
    "TSE1": "taiwan-strait-express-1-tse-1",
    "海峽光纜一號": "taiwan-strait-express-1-tse-1",
    "海峽光纜": "taiwan-strait-express-1-tse-1",
    # NCP
    "NCP": "new-cross-pacific-ncp-cable-system",
    # FASTER
    "FASTER": "faster",
    # ADC
    "ADC": "asia-direct-cable-adc",
    # SEA-ME-WE 3
    "SMW3": "seamewe-3",
    "SEAMEWE3": "seamewe-3",
    "SEA-ME-WE 3": "seamewe-3",
    # H2 Cable
    "H2": "h2-cable",
    # HKA
    "HKA": "hong-kong-americas-hka",
    # TGN-IA
    "TGN-IA": "tata-tgn-intra-asia-tgn-ia",
    # Cross-Strait
    "海峽": "cross-straits-cable-network",
    # CAP-1
    "CAP-1": "cap-1",
    "CAP1": "cap-1",
    # Apricot
    "Apricot": "new-cross-pacific-ncp-cable-system",
    "杏子海纜": "new-cross-pacific-ncp-cable-system",
    "杏子": "new-cross-pacific-ncp-cable-system",
    # TDM (台馬海纜 — not in GeoJSON, use cross-straits as closest)
    "TDM2": "cross-straits-cable-network",
    "TDM3": "cross-straits-cable-network",
    "TM3": "cross-straits-cable-network",
    "臺馬二號": "cross-straits-cable-network",
    "臺馬三號": "cross-straits-cable-network",
    "台馬二號": "cross-straits-cable-network",
    "台馬三號": "cross-straits-cable-network",
}


def _resolve_slug(name_zh: str, name_en_hint: str) -> str:
    """Resolve cable name to GeoJSON slug."""
    # Try English abbreviation first (most reliable)
    for key, slug in CABLE_NAME_TO_SLUG.items():
        if key in name_en_hint:
            return slug
    # Try Chinese name
    for key, slug in CABLE_NAME_TO_SLUG.items():
        if key in name_zh:
            return slug
    return ""


def _extract_segment(name_zh: str) -> str:
    """Extract segment abbreviation from cable name.
    e.g. '東亞交匯一號海纜系統( EAC1 )' → 'EAC1'
    """
    # Try parenthesized abbreviation: ( EAC1 ) or （EAC1）
    m = re.search(r'[（(]\s*([A-Za-z0-9\-/ ]+?)\s*[）)]', name_zh)
    if m:
        return m.group(1).strip()
    # Try trailing English
    m = re.search(r'([A-Z][A-Z0-9\-]+)', name_zh)
    if m:
        return m.group(1)
    return name_zh.strip()


def _extract_name_en(name_zh: str, segment: str) -> str:
    """Generate English name from segment abbreviation."""
    en_names = {
        "EAC1": "EAC1 Cable",
        "EAC2": "EAC2 Cable",
        "C2C": "C2C Cable",
        "APCN2": "APCN-2 Cable",
        "APG": "APG Cable",
        "SJC2": "SJC2 Cable",
        "SJC": "SJC Cable",
        "PLCN": "PLCN Cable",
        "TPE": "TPE Cable",
        "FNAL/RNAL": "FNAL/RNAL Cable",
        "FEA": "FEA Cable",
        "TSE-1": "TSE-1 Cable",
        "NCP": "NCP Cable",
        "TDM2": "TDM2 Cable",
        "TDM3": "TDM3 Cable",
        "TM3": "TM3 Cable",
        "Apricot": "Apricot Cable",
    }
    return en_names.get(segment, segment + " Cable")


def _roc_to_iso(date_str: str) -> str:
    """Convert ROC date (114/8/22) to ISO date (2025-08-22).
    Returns empty string on failure.
    """
    try:
        parts = date_str.strip().split("/")
        if len(parts) == 3:
            year = int(parts[0]) + 1911
            month = int(parts[1])
            day = int(parts[2])
            return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        pass
    return ""


def _determine_status(fault_desc: str, repair_date_str: str) -> str:
    """Determine if fault is active or repaired."""
    if "已修復" in fault_desc or "修復完成" in fault_desc:
        return "repaired"
    # If repair date is in the past, it might be repaired
    if repair_date_str:
        try:
            repair = datetime.strptime(repair_date_str, "%Y-%m-%d")
            if repair.date() < datetime.now().date():
                return "repaired"
        except ValueError:
            pass
    return "fault"


def scrape_moda() -> list:
    """Scrape MODA submarine cable fault table."""
    print(f"🌐 正在請求 MODA 海纜障礙頁面: {MODA_URL}")
    resp = requests.get(MODA_URL, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        print("⚠️ 找不到表格資料")
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        print("⚠️ 表格行數不足")
        return []

    faults = []
    current_entry = None

    for row in rows[1:]:  # Skip header row
        cols = row.find_all(["th", "td"])
        cells = [col.get_text(strip=True) for col in cols]

        if not cells or all(c == "" for c in cells):
            continue

        # Rows with 序號 (first column is a number) start a new entry
        # Multi-row entries (e.g., TDM2 has 2 fault rows) have fewer columns
        if len(cells) >= 5 and re.match(r"^\d+$", cells[0]):
            # New entry: 序號 | 海纜名稱 | 障礙發生日期 | 障礙情形 | 替代路由 | 預計修復日期
            seq = cells[0]
            cable_name = cells[1]
            fault_date_roc = cells[2]
            fault_desc = cells[3]
            alt_route = cells[4] if len(cells) > 4 else ""
            est_repair_roc = cells[5] if len(cells) > 5 else ""

            segment = _extract_segment(cable_name)
            name_en = _extract_name_en(cable_name, segment)
            fault_date = _roc_to_iso(fault_date_roc)
            est_repair = _roc_to_iso(est_repair_roc)
            slug = _resolve_slug(cable_name, segment)

            # Clean cable name (remove English abbreviation in parens)
            name_zh = re.sub(r'[（(][A-Za-z0-9\-/ ]+[）)]', '', cable_name).strip()
            # Remove trailing "海纜系統" etc to keep it short
            name_zh = re.sub(r'海纜系統$', '海纜', name_zh)

            status = _determine_status(fault_desc, est_repair)

            entry = {
                "slug": slug,
                "segment": segment,
                "name_zh": name_zh,
                "name_en": name_en,
                "status": status,
                "fault_date": fault_date,
                "repair_date": est_repair if status == "repaired" else None,
                "estimated_repair": est_repair if status == "fault" else None,
                "location_zh": fault_desc,
                "description_zh": fault_desc,
                "description_en": "",
                "alt_route": alt_route,
            }
            faults.append(entry)
            current_entry = entry

        elif len(cells) >= 3 and current_entry:
            # Continuation row for same cable (e.g., TDM2 second fault)
            # Typically: 障礙日期 | 障礙情形 | 替代路由 [| 預計修復]
            fault_date_roc = cells[0]
            fault_desc = cells[1]
            alt_route = cells[2] if len(cells) > 2 else ""
            est_repair_roc = cells[3] if len(cells) > 3 else ""

            fault_date = _roc_to_iso(fault_date_roc)
            est_repair = _roc_to_iso(est_repair_roc)
            status = _determine_status(fault_desc, est_repair)

            entry = {
                "slug": current_entry["slug"],
                "segment": current_entry["segment"],
                "name_zh": current_entry["name_zh"],
                "name_en": current_entry["name_en"],
                "status": status,
                "fault_date": fault_date,
                "repair_date": est_repair if status == "repaired" else None,
                "estimated_repair": est_repair if status == "fault" else None,
                "location_zh": fault_desc,
                "description_zh": fault_desc,
                "description_en": "",
                "alt_route": alt_route,
            }
            faults.append(entry)

    return faults


def main():
    try:
        faults = scrape_moda()
    except requests.RequestException as e:
        print(f"❌ 請求失敗: {e}")
        sys.exit(1)

    if not faults:
        print("⚠️ 未取得任何障礙資料，保留原有 cable_status.json")
        sys.exit(0)

    active = [f for f in faults if f["status"] == "fault"]
    repaired = [f for f in faults if f["status"] == "repaired"]

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "數位發展部 海纜障礙狀況",
        "source_url": MODA_URL,
        "faults": faults,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 已更新 {OUTPUT_FILE}")
    print(f"   障礙中: {len(active)}")
    print(f"   已修復: {len(repaired)}")
    for fault in faults:
        status_icon = "🔴" if fault["status"] == "fault" else "🟢"
        print(f"   {status_icon} {fault['segment']}: {fault['description_zh'][:40]}")


if __name__ == "__main__":
    main()

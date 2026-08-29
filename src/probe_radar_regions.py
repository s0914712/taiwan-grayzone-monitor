#!/usr/bin/env python3
"""
Cloudflare Radar 縣市（ADM1）粒度能力探測 — Taiwan Gray Zone Monitor

`fetch_cloudflare_radar.py` 裡有一段結論：「Radar 沒有縣市粒度，`location` 只吃
alpha-2 國碼，TW-LIE／TW-KIN 回 400，別再加回來」。那是在 Cloudflare 於
**2025-09-29** 推出 Regional Data 之前測的：現在 HTTP 與 NetFlows 的
summary / timeseries_groups 都多了 `adm1` 維度與 **`geoId` 篩選**（GeoNames ID），
`/radar/geolocations` 也能列出 ADM1。台灣的 ADM1 就是 22 個縣市。

**實測結論（run 33216931232 + 33239842250）**：

1. 速度類端點**全部吃 `geoId`**（IQI 頻寬／延遲／summary、Speed Test
   summary＋histogram、HTTP、NetFlows 都 200 有值），對照組 `location=TW-LIE`
   仍是 400。
2. 但 **Radar 的台灣 ADM1 只有 4 個分區，不是 22 個縣市**：
   `7280290 Taipei`／`7280289 Takao`（高雄舊名）／`7280288 Fukien`（金門＋馬祖）／
   `7280291 Taiwan`（其餘 18 縣市）。entity **沒有 ISO 3166-2 欄位**，`code` 是
   GeoNames 分區碼（台北 `03`），名稱是舊省制分區名——所以拿縣市英文名去比對
   本來就只可能對到臺北一個。
   注意「Taiwan」在同一份回應裡出現兩次（COUNTRY 1668284 與臺灣省 ADM1
   7280291），比對時**必須看 `type`**。

這支腳本仍留著，作為「Radar 哪天把粒度做細」時的重新驗證工具。它不寫任何管線
資料，只產生一張「哪個端點吃 geoId」的能力矩陣與完整的 entity 清單。

沙箱環境連不到 api.cloudflare.com，因此這支的執行方式是
`.github/workflows/radar-region-probe.yml`（手動觸發，用 repo 既有的 token）。

用法:
  python3 src/probe_radar_regions.py                  # 完整探測
  python3 src/probe_radar_regions.py --dump-raw       # 附上原始回應片段
  python3 src/probe_radar_regions.py --counties 5     # 多測幾個縣市

輸出: data/radar_region_probe.json（＋ Markdown 矩陣寫入 $GITHUB_STEP_SUMMARY）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fetch_cloudflare_radar import (  # noqa: E402
    CF_API_BASE, REQUEST_TIMEOUT, TOKEN_ENV_NAMES, _get_env,
    parse_timeseries_payload,
)

# 探測用的時間視窗：短一點，只是要看端點收不收參數，不是要資料
PROBE_RANGE = "7d"

# 先確認縣市清單怎麼拿。Radar 的 geolocations 端點參數官方文件寫得不完整，
# 因此把幾種合理寫法都試一遍，第一個回得出 ADM1 清單的就採用。
# 第一輪實測（run 33216931232）的結果已經回填在這裡：
#   `?location=TW&limit=100`  → 200，但只回 6 個 entity，其中 1 個是台灣 ADM1（台北）
#   `?countryAlpha2=TW`       → 200，參數被忽略，回 183 筆全球清單
#   `summary_v2`（http/netflows）→ 400 `code 7000 No route for that URI`（路徑不存在）
# 所以這一輪換成「維度端點」`summary/{dimension}` 與
# `timeseries_groups/{dimension}`，並把 limit 開到 500、加上分頁參數試探。
GEO_LIST_ATTEMPTS = [
    {"id": "geolocations_location", "path": "radar/geolocations",
     "params": {"location": "TW", "limit": 500}},
    {"id": "geolocations_type_adm1", "path": "radar/geolocations",
     "params": {"location": "TW", "type": "ADM1", "limit": 500}},
    {"id": "geolocations_adm1_only", "path": "radar/geolocations",
     "params": {"type": "ADM1", "limit": 500}},
    {"id": "geolocations_offset", "path": "radar/geolocations",
     "params": {"location": "TW", "limit": 500, "offset": 0}},
    {"id": "entities_locations_tw", "path": "radar/entities/locations",
     "params": {"location": "TW", "limit": 500}},
    # 維度端點：有資料的 ADM1 會直接出現在回應裡（附 geoId），是備援解析路徑
    {"id": "http_summary_adm1", "path": "radar/http/summary/adm1",
     "params": {"location": "TW", "dateRange": PROBE_RANGE}},
    {"id": "http_timeseries_groups_adm1",
     "path": "radar/http/timeseries_groups/adm1",
     "params": {"location": "TW", "dateRange": PROBE_RANGE, "aggInterval": "1d"}},
    {"id": "netflows_summary_adm1", "path": "radar/netflows/summary/adm1",
     "params": {"location": "TW", "dateRange": PROBE_RANGE}},
    {"id": "netflows_timeseries_groups_adm1",
     "path": "radar/netflows/timeseries_groups/adm1",
     "params": {"location": "TW", "dateRange": PROBE_RANGE, "aggInterval": "1d"}},
]

# 第一輪唯一解析到的台灣 ADM1（臺北市）。用它當探針：印出它在每個 listing 回應
# 裡的完整物件，才知道 name／iso 欄位到底叫什麼、為什麼其他 21 個縣市對不上。
KNOWN_TW_ADM1_GEOID = "7280290"

# 每個縣市要試的端點。`kind` 決定怎麼判定「有沒有拿到值」。
ENDPOINT_MATRIX = [
    {"id": "iqi_bandwidth_geoid", "kind": "timeseries",
     "path": "radar/quality/iqi/timeseries_groups",
     "params": {"metric": "bandwidth", "aggInterval": "1h"},
     "note": "縣市級頻寬（＝網速）時間序列"},
    {"id": "iqi_latency_geoid", "kind": "timeseries",
     "path": "radar/quality/iqi/timeseries_groups",
     "params": {"metric": "latency", "aggInterval": "1h"},
     "note": "縣市級延遲時間序列"},
    {"id": "iqi_summary_geoid", "kind": "any",
     "path": "radar/quality/iqi/summary",
     "params": {"metric": "bandwidth"},
     "note": "縣市級頻寬摘要"},
    {"id": "speed_summary_geoid", "kind": "any",
     "path": "radar/quality/speed/summary", "params": {},
     "note": "縣市級 Speed Test 摘要（頻寬／延遲／jitter）"},
    {"id": "speed_histogram_geoid", "kind": "any",
     "path": "radar/quality/speed/histogram",
     "params": {"metricGroup": "bandwidth"},
     "note": "縣市級 Speed Test 分布"},
    {"id": "http_timeseries_geoid", "kind": "timeseries",
     "path": "radar/http/timeseries_groups/device_type",
     "params": {"aggInterval": "1h"},
     "note": "縣市級 HTTP 請求分布（流量指數的來源）"},
    {"id": "netflows_timeseries_geoid", "kind": "timeseries",
     "path": "radar/netflows/timeseries", "params": {"aggInterval": "1h"},
     "note": "縣市級 NetFlows 流量時間序列"},
]

# 對照組：舊寫法（ISO 3166-2 塞進 location）現在還是不是 400
LEGACY_ATTEMPTS = [
    {"id": "legacy_location_iso2", "path": "radar/netflows/timeseries",
     "params": {"location": "TW-LIE", "aggInterval": "1h"}},
]

# 完整矩陣先只測這幾個縣市（避免打太多請求）：直轄市、離島、本島小縣各一
PREFERRED_SAMPLE_ISO = ["TW-TPE", "TW-LIE", "TW-PEN", "TW-KIN", "TW-HUA"]


def probe(session, token, path, params, dump=False):
    """打一個端點，回傳結構化結果（不丟例外）。"""
    url = f"{CF_API_BASE}/{path}"
    query = {"format": "json", **params}
    query.setdefault("dateRange", PROBE_RANGE)
    out = {"path": path, "params": {k: v for k, v in query.items()
                                    if k != "format"}}
    try:
        resp = session.get(url, params=query,
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        out.update({"status": None, "ok": False, "error": f"request failed: {e}"})
        return out

    out["status"] = resp.status_code
    text = resp.text or ""
    try:
        payload = resp.json()
    except ValueError:
        out.update({"ok": False, "error": f"non-JSON: {text[:200]}"})
        return out
    if dump:
        out["raw_excerpt"] = json.dumps(payload, ensure_ascii=False)[:1200]
    if resp.status_code != 200 or not payload.get("success", True):
        out.update({"ok": False,
                    "error": str(payload.get("errors") or text[:200])[:300]})
        return out
    out["ok"] = True
    out["payload"] = payload
    return out


def summarize_result(result, kind):
    """把探測結果壓成「有沒有真的拿到數值 + 樣本」。"""
    if not result.get("ok"):
        return {"has_values": False}
    payload = result.get("payload") or {}
    if kind == "timeseries":
        timestamps, values = parse_timeseries_payload(payload)
        real = [v for v in values if v is not None]
        return {"has_values": bool(real),
                "points": len(timestamps),
                "sample": real[:3]}
    result_body = payload.get("result") or {}
    keys = [k for k in result_body if k != "meta"]
    sample = json.dumps({k: result_body[k] for k in keys[:2]},
                        ensure_ascii=False)[:300]
    return {"has_values": bool(keys), "result_keys": keys[:10], "sample": sample}


def extract_adm1(payload):
    """從任意 Radar 回應撈出 (geo_id, name, iso) 三元組。

    geolocations / summary_v2 的殼不一樣（`result.geolocations` vs
    `result.adm1` 之類），而且官方文件沒完整寫。與其硬綁 key，不如遞迴找出所有
    帶 geo_id 樣貌欄位的 dict —— 探測腳本的重點是「有沒有」，不是「長多好看」。
    """
    found = {}

    def walk(node):
        if isinstance(node, dict):
            geo_id = (node.get("geoId") or node.get("geo_id")
                      or node.get("id") or node.get("code"))
            name = node.get("name") or node.get("locationName") or node.get("label")
            iso = (node.get("iso3166Alpha2") or node.get("isoCode")
                   or node.get("subdivisionCode") or node.get("alpha2"))
            if geo_id is not None and name:
                found[str(geo_id)] = {"geo_id": str(geo_id), "name": str(name),
                                      "iso": str(iso) if iso else None,
                                      "type": node.get("type")}
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return list(found.values())


def find_entity(payload, geo_id):
    """在回應裡找出某個 geoId 的完整物件（含所有欄位），找不到回 None。

    第一輪只對到 1 個縣市，代表 name／iso 欄位的名稱和我猜的不一樣。把已知的
    台北物件原封不動印出來，比再猜十次有效。
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("geoId", "geo_id", "id", "code"):
                if str(node.get(key)) == str(geo_id):
                    found.append(node)
                    break
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found[0] if found else None


def resolve_counties(session, token, dump=False):
    """依序試各種寫法，回傳 (attempts, counties)。

    **每個 entity 都印進 stdout**，不是只寫進 artifact —— artifact 要認證才下載得到，
    job log 才是拿得到的那一份（第一輪就是因為只有矩陣沒有清單，卡在「為什麼只
    對到台北」無從判讀）。
    """
    attempts, counties = [], []
    for spec in GEO_LIST_ATTEMPTS:
        res = probe(session, token, spec["path"], spec["params"], dump=dump)
        entries = extract_adm1(res.get("payload") or {}) if res.get("ok") else []
        # 只留看起來像台灣縣市的（ISO 前綴 TW- 或名稱在英文對照表裡）
        tw = [e for e in entries
              if (e.get("iso") or "").upper().startswith("TW-")
              or (e.get("name") or "") in TW_NAME_HINTS]
        known = find_entity(res.get("payload") or {}, KNOWN_TW_ADM1_GEOID)
        attempts.append({
            "id": spec["id"], "status": res.get("status"), "ok": res.get("ok"),
            "error": res.get("error"), "entities_found": len(entries),
            "tw_adm1_found": len(tw),
            "entities": entries[:200],
            "known_entity": known,
            **({"raw_excerpt": res["raw_excerpt"]} if "raw_excerpt" in res else {}),
        })

        print(f"   ↳ [{spec['id']}] status={res.get('status')} "
              f"entities={len(entries)} tw={len(tw)}"
              + (f" ❌ {str(res.get('error'))[:120]}" if not res.get("ok") else ""))
        if known:
            print(f"      已知台北 geoId {KNOWN_TW_ADM1_GEOID} 的完整物件："
                  f"{json.dumps(known, ensure_ascii=False)[:600]}")
        for entry in entries[:60]:
            print(f"      · geo_id={entry.get('geo_id')} "
                  f"type={entry.get('type')} iso={entry.get('iso')} "
                  f"name={entry.get('name')}")
        if len(entries) > 60:
            print(f"      …（另外 {len(entries) - 60} 筆未列出）")
        if tw and not counties:
            counties = tw
            print(f"   ✅ [{spec['id']}] 採用這一輪的 {len(tw)} 個台灣 ADM1")
    return attempts, counties


# 用英文縣市名當作備援比對（有些回應不帶 ISO 代碼）
try:
    from build_tw_counties import COUNTY_NAMES_EN
    TW_NAME_HINTS = set(COUNTY_NAMES_EN.values()) | {
        "Taipei", "New Taipei", "Taichung", "Tainan", "Kaohsiung", "Taoyuan",
        "Keelung", "Hsinchu", "Chiayi", "Miaoli", "Changhua", "Nantou",
        "Yunlin", "Pingtung", "Yilan", "Hualien", "Taitung", "Penghu",
        "Kinmen", "Lienchiang", "Matsu Islands", "Lienkiang",
    }
except Exception:                                    # pragma: no cover
    TW_NAME_HINTS = set()


def markdown_matrix(report):
    """把結果排成 Markdown 表（寫進 GitHub job summary）。"""
    lines = ["## Cloudflare Radar 縣市（ADM1）粒度探測", "",
             f"- 執行時間：{report['generated_at']}",
             f"- 解析到的台灣 ADM1：**{len(report['counties'])}**", ""]

    lines += ["### 縣市清單解析嘗試", "",
              "| 嘗試 | HTTP | 找到 entity | 其中台灣 ADM1 | 錯誤 |",
              "|---|---|---|---|---|"]
    for a in report["resolution_attempts"]:
        lines.append(f"| `{a['id']}` | {a.get('status')} | {a.get('entities_found')} "
                     f"| {a.get('tw_adm1_found')} | {(a.get('error') or '')[:80]} |")

    lines += ["", "### 端點 × geoId 能力矩陣", "",
              "| 端點 | 說明 | 縣市 | HTTP | 有數值 | 樣本／錯誤 |",
              "|---|---|---|---|---|---|"]
    for row in report["matrix"]:
        detail = (str(row.get("sample")) if row.get("has_values")
                  else (row.get("error") or ""))
        lines.append(f"| `{row['id']}` | {row.get('note', '')} | {row['county']} "
                     f"| {row.get('status')} | {'✅' if row.get('has_values') else '❌'} "
                     f"| {detail[:80]} |")

    lines += ["", "### 結論（給 fetch_radar_counties.py 的指標階梯）", ""]
    for metric, ok in report["capabilities"].items():
        lines.append(f"- `{metric}`：{'可用 ✅' if ok else '不可用 ❌'}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Radar 縣市粒度能力探測")
    ap.add_argument("--counties", type=int, default=3,
                    help="完整矩陣要測幾個縣市（預設 3）")
    ap.add_argument("--dump-raw", action="store_true", help="附上原始回應片段")
    ap.add_argument("-o", "--output",
                    default=str(DATA_DIR / "radar_region_probe.json"))
    args = ap.parse_args()

    token = _get_env(*TOKEN_ENV_NAMES)
    if not token:
        print("❌ 缺少 Cloudflare API token（CLOUDFLARE_API_TOKEN / CLAUDEFARETOKEN）")
        sys.exit(1)
    session = requests.Session()

    print("🌍 解析台灣 ADM1 清單 …")
    attempts, counties = resolve_counties(session, token, dump=args.dump_raw)

    # 挑要跑完整矩陣的縣市：優先離島與台北（若解析得到 ISO），否則取前幾個
    by_iso = {(c.get("iso") or "").upper(): c for c in counties}
    picked = [by_iso[iso] for iso in PREFERRED_SAMPLE_ISO if iso in by_iso]
    picked += [c for c in counties if c not in picked]
    picked = picked[:max(1, args.counties)]

    matrix = []
    if not picked:
        print("⚠️ 沒有解析到任何縣市 geoId，矩陣只能跑對照組")
    for county in picked:
        label = f"{county.get('name')} ({county.get('iso') or county['geo_id']})"
        print(f"📡 {label}")
        for spec in ENDPOINT_MATRIX:
            res = probe(session, token, spec["path"],
                        {**spec["params"], "geoId": county["geo_id"]},
                        dump=args.dump_raw)
            summary = summarize_result(res, spec["kind"])
            row = {"id": spec["id"], "note": spec["note"], "county": label,
                   "geo_id": county["geo_id"], "status": res.get("status"),
                   "ok": res.get("ok"), "error": res.get("error"), **summary}
            if args.dump_raw and "raw_excerpt" in res:
                row["raw_excerpt"] = res["raw_excerpt"]
            matrix.append(row)
            print(f"   ↳ {spec['id']}: HTTP {res.get('status')} "
                  f"{'有值' if summary.get('has_values') else '無值'}"
                  + (f"｜{str(res.get('error'))[:80]}" if not res.get("ok") else ""))

    legacy = []
    for spec in LEGACY_ATTEMPTS:
        res = probe(session, token, spec["path"], spec["params"])
        legacy.append({"id": spec["id"], "status": res.get("status"),
                       "ok": res.get("ok"), "error": res.get("error")})
        print(f"🕰️  對照組 {spec['id']}: HTTP {res.get('status')}")

    # 能力結論：某個端點只要在任一縣市拿得到值，就算可用
    capabilities = {}
    for spec in ENDPOINT_MATRIX:
        capabilities[spec["id"]] = any(r["id"] == spec["id"] and r.get("has_values")
                                       for r in matrix)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "probe_range": PROBE_RANGE,
        "resolution_attempts": attempts,
        "counties": counties,
        "matrix": matrix,
        "legacy_attempts": legacy,
        "capabilities": capabilities,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"✅ {out}")

    md = markdown_matrix(report)
    print("\n" + md)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()

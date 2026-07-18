#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
darkship_batch.py — 暗船 SAR 取證批次執行器（GitHub Actions cron 用）

把原本在本機取證工作站由 Claude Code 手動逐步執行的「標準流程」
（抓取證清單 -> 篩選/去重 -> 逐一呼叫 fetch_sar_chip.py -> 產生報告）
搬進一支可重複執行的腳本，供 .github/workflows/darkship-cron.yml 排程呼叫。

前提（呼叫前）：
  - 環境變數 CDSE_ACCESS_KEY / CDSE_SECRET_KEY 已設定（GitHub Actions 用
    secrets 注入）
  - 已安裝 requests boto3 rasterio numpy matplotlib
  - --fetch-script 指向的 fetch_sar_chip.py 在同一個 checkout 內

用法範例（CI 內，讀 checkout 裡的清單，不經過 GitHub Pages 快取）：
  python src/darkship_batch.py \\
      --worklist data/sar_chip_worklist.json \\
      --matches data/sar_ais_matches.json \\
      --fetch-script src/fetch_sar_chip.py \\
      --results chips/results.json \\
      --chips-dir chips \\
      --reports-dir reports \\
      --limit 10

--worklist / --matches 也接受 https:// URL（本機遠端跑時用 Pages 上的 JSON）。

輸出：
  - <chips-dir>/<date>_<lat>_<lon>.png：每個目標的切片圖
  - <results>：累積結果（JSON array，跨執行去重用）
  - <reports-dir>/darkship_report_<YYYY-MM-DD>.md：本輪報告

注意：這支腳本只負責「跑批次 + 產生報告」，不負責 git commit / PR —
那部分由 workflow YAML 的後續 step（git add / commit / push）處理。
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

# fetch_sar_chip.py stdout 的關鍵標記（見 src/fetch_sar_chip.py main()）
BRIGHT_RE = re.compile(
    r"亮目標:\s*~([\d.]+)\s*m、峰值\s*([\d.]+)×\s*海面背景（(\d+)\s*px）"
)
PRODUCT_RE = re.compile(r"✅\s+(\S+\.SAFE)")
NO_TARGET_MARK = "中心附近沒有超過門檻的亮目標"
SATURATED_MARK = "亮區超過上限"
ERROR_MARK = "❌"

CONSECUTIVE_FAILURE_LIMIT = 3
SUBPROCESS_TIMEOUT_SEC = 300


def log(msg):
    print(msg, flush=True)


def load_source(source):
    """讀 worklist / matches JSON：本地路徑或 http(s) URL（加時間戳避免快取）。"""
    if source.startswith("http://") or source.startswith("https://"):
        sep = "&" if "?" in source else "?"
        full_url = f"{source}{sep}ts={int(time.time())}"
        with urllib.request.urlopen(full_url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open(source, encoding="utf-8") as f:
        return json.load(f)


def load_results(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def result_key(r):
    return (str(r.get("lat")), str(r.get("lon")), str(r.get("date")))


def build_todo(targets, done_keys, limit):
    """covered 且未完成的目標，依日期新→舊，最多 limit 筆（0 = 不設限）。"""
    covered = [t for t in targets if t.get("covered")]
    todo = [t for t in covered if result_key(t) not in done_keys]
    todo.sort(key=lambda t: t.get("date", ""), reverse=True)
    if limit and limit > 0:
        todo = todo[:limit]
    return todo


def parse_fetch_output(combined, returncode, record):
    """解析 fetch_sar_chip.py 的 stdout+stderr，填進 record。

    回傳 is_failure（計入連續失敗保護）。純函式（不碰檔案/子行程），
    供 tests/test_darkship_batch.py 直接驗證。
    """
    prod_match = PRODUCT_RE.search(combined)
    if prod_match:
        record["product"] = prod_match.group(1)

    if returncode != 0 and ERROR_MARK not in combined:
        # 子行程沒有留下乾淨的 ❌ 訊息就掛了（逾時、網路、traceback）
        tail = combined.strip().splitlines()[-1] if combined.strip() else "unknown error"
        record["error"] = f"crash: {tail[:200]}"
        return True

    if ERROR_MARK in combined:
        err_line = next((l for l in combined.splitlines() if ERROR_MARK in l), "")
        record["error"] = err_line.strip()
        return True

    bright = BRIGHT_RE.search(combined)
    if bright:
        record["found"] = True
        record["length_m"] = float(bright.group(1))
        record["peak_ratio"] = float(bright.group(2))
        record["n_pixels"] = int(bright.group(3))
        record["saturated"] = SATURATED_MARK in combined
    elif NO_TARGET_MARK in combined:
        record["found"] = False
    else:
        record["error"] = "unrecognized output"
        return True

    return False


def run_one(fetch_script, target, chips_dir, size_km=None):
    lat = target["lat"]
    lon = target["lon"]
    date = target["date"]
    time_str = target.get("time")
    out_png = os.path.join(
        chips_dir, f"{date}_{lat}_{lon}.png".replace(":", "")
    )

    cmd = [sys.executable, fetch_script, str(lat), str(lon), date, "-o", out_png]
    if time_str:
        cmd += ["--time", time_str[:5]]
    if size_km:
        cmd += ["--size-km", str(size_km)]

    # 本機 Windows 主控台 cp950 會因 emoji stdout 拋 UnicodeEncodeError；
    # Ubuntu runner 理論上不會踩到，但固定 utf-8 沒有壞處
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    record = {
        "date": date,
        "lat": lat,
        "lon": lon,
        "time": time_str,
        "zone": target.get("zone"),
        "jurisdiction": target.get("maritime_zone"),
        "product": None,
        "found": False,
        "length_m": None,
        "peak_ratio": None,
        "n_pixels": None,
        "saturated": False,
        "png": None,
        "error": None,
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              timeout=SUBPROCESS_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        record["error"] = f"timeout: {SUBPROCESS_TIMEOUT_SEC}s 內未完成"
        return record, True

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode == 0:
        record["png"] = out_png.replace("\\", "/")
    is_failure = parse_fetch_output(combined, proc.returncode, record)
    return record, is_failure


def verdict(r):
    if r.get("error"):
        return "❌ 失敗"
    if r.get("saturated"):
        return "⚠️ 疑似陸地/固定結構"
    if r.get("found"):
        return "✅ 確認實體目標" if (r.get("peak_ratio") or 0) >= 10 else "🟡 弱目標"
    return "⚪ 無目標（雜訊或低RCS）"


def write_report(report_path, reports_dir, run_summary, match_summary,
                 batch_results, all_results):
    today = datetime.date.today().isoformat()
    lines = []
    lines.append(f"# 暗船 SAR 取證報告 — {today}\n")
    lines.append("## 1. 總覽\n")
    lines.append(f"- 執行時間：{today}（GitHub Actions cron 自動執行）")
    if match_summary:
        lines.append(
            f"- 線上比對摘要：暗偵測 {match_summary.get('dark_total', '?')} 筆、"
            f"殘餘暗船 {match_summary.get('residual_dark', '?')} 筆、"
            f"假暗船剔除率 {match_summary.get('false_dark_removed_pct', '?')}%"
        )
    for k, v in run_summary.items():
        lines.append(f"- {k}：{v}")
    lines.append("")

    lines.append("## 2. 結果總表\n")
    lines.append("| 日期 | 座標 | 海域 | 法域 | 成像時刻 | 判定 | 估計長度 | 峰值比 |")
    lines.append("|------|------|------|------|----------|------|----------|--------|")
    for r in batch_results:
        length = f"~{r['length_m']} m" if r.get("length_m") else "—"
        peak = f"{r['peak_ratio']}×" if r.get("peak_ratio") else "—"
        lines.append(
            f"| {r['date']} | {r['lat']}, {r['lon']} | {r.get('zone','')} | "
            f"{r.get('jurisdiction','')} | {r.get('time','')} | {verdict(r)} | {length} | {peak} |"
        )
    lines.append("")

    lines.append("## 3. 逐目標段落\n")
    for r in batch_results:
        lines.append(f"### {r['date']} ({r['lat']}, {r['lon']})")
        if r.get("png"):
            rel = os.path.relpath(r["png"], start=reports_dir)
            lines.append(f"![chip]({rel.replace(os.sep, '/')})")
        if r.get("error"):
            lines.append(f"❌ 失敗：{r['error']}")
        elif r.get("found"):
            lines.append(
                f"{verdict(r)}，估計長度 ~{r['length_m']} m，"
                f"峰值 {r['peak_ratio']}× 海面背景（{r.get('n_pixels')} px）。"
            )
        else:
            lines.append(f"{verdict(r)}。")
        lines.append("")

    lines.append("## 4. 後續建議\n")
    worth = [r for r in batch_results
             if verdict(r) == "✅ 確認實體目標" and (r.get("length_m") or 0) >= 50]
    if worth:
        lines.append("- 值得人工深查（確認實體目標且長度 ≥ 50m）：")
        for r in worth:
            lines.append(
                f"  - {r['date']} ({r['lat']}, {r['lon']}) ~{r['length_m']} m —"
                f" 建議比對周邊 AIS 船長"
            )
    confirmed = sum(1 for r in all_results if verdict(r) == "✅ 確認實體目標")
    total_valid = sum(1 for r in all_results if not r.get("error"))
    if total_valid:
        lines.append(
            f"- 累積統計：全歷史 {len(all_results)} 筆（有效 {total_valid} 筆），"
            f"確認實體目標 {confirmed} 筆（{confirmed / total_valid * 100:.1f}%）"
        )
    else:
        lines.append(f"- 累積統計：全歷史 {len(all_results)} 筆，尚無有效結果")
    lines.append("")
    lines.append("> 所有結果是研判線索，不是法律認定；"
                 "無目標 ≠ 誤報（小型木殼漁船 RCS 低，SAR 可能拍不到）。")

    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="暗船 SAR 批次取證（cron 版）")
    ap.add_argument("--worklist", required=True,
                    help="sar_chip_worklist.json 的本地路徑或 URL")
    ap.add_argument("--matches", default=None,
                    help="sar_ais_matches.json 的本地路徑或 URL（報告總覽引用，可省略）")
    ap.add_argument("--fetch-script", required=True)
    ap.add_argument("--results", required=True, help="chips/results.json 路徑")
    ap.add_argument("--chips-dir", required=True)
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--limit", type=int, default=10,
                    help="本輪最多測試幾筆新目標（0 = 全部）")
    args = ap.parse_args()

    if not os.environ.get("CDSE_ACCESS_KEY") or not os.environ.get("CDSE_SECRET_KEY"):
        log("❌ 缺少 CDSE_ACCESS_KEY / CDSE_SECRET_KEY，中止（請確認 GitHub Secrets 已設定）")
        return 1

    os.makedirs(args.chips_dir, exist_ok=True)
    os.makedirs(args.reports_dir, exist_ok=True)

    log(f"讀取 worklist：{args.worklist}")
    try:
        worklist = load_source(args.worklist)
    except Exception as e:
        log(f"❌ worklist 讀取失敗：{e}")
        return 1
    targets = worklist.get("targets", [])
    summary = worklist.get("summary", {})
    log(f"worklist targets={len(targets)} summary={summary}")

    match_summary = {}
    if args.matches:
        try:
            match_summary = (load_source(args.matches) or {}).get("summary", {})
        except Exception as e:
            log(f"⚠️ matches 讀取失敗（報告不引用比對摘要）：{e}")

    existing = load_results(args.results)
    done_keys = {result_key(r) for r in existing}

    todo = build_todo(targets, done_keys, args.limit)
    log(f"本輪待測 {len(todo)} 筆（covered 中未完成的，依日期新→舊，上限 {args.limit}）")

    if not todo:
        log("沒有新目標，結束（不產生報告）")
        return 0

    batch_results = []
    consecutive_failures = 0
    for i, t in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] {t['date']} ({t['lat']}, {t['lon']}) ...")
        record, is_failure = run_one(args.fetch_script, t, args.chips_dir)
        batch_results.append(record)
        existing.append(record)

        if is_failure:
            consecutive_failures += 1
            log(f"  失敗：{record.get('error')}")
        else:
            consecutive_failures = 0
            log(f"  {verdict(record)}")

        # 每筆完成即落盤：中途被砍（timeout/取消）也不會遺失已完成的結果
        with open(args.results, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            log(f"連續 {CONSECUTIVE_FAILURE_LIMIT} 筆失敗，停止本輪（可能是金鑰或網路問題）")
            break

    ok = sum(1 for r in batch_results if not r.get("error"))
    found = sum(1 for r in batch_results if r.get("found"))
    failed = sum(1 for r in batch_results if r.get("error"))
    run_summary = {
        "本輪測試": f"{len(batch_results)} 筆",
        "成功": f"{ok} 筆",
        "找到亮目標": f"{found} 筆",
        "失敗": f"{failed} 筆",
    }

    today = datetime.date.today().isoformat()
    report_path = os.path.join(args.reports_dir, f"darkship_report_{today}.md")
    write_report(report_path, args.reports_dir, run_summary, match_summary,
                 batch_results, existing)
    log(f"報告已寫入 {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

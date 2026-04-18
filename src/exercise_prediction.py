#!/usr/bin/env python3
"""
================================================================================
軍演預測分析：暗船活動作為軍演預警指標
Exercise Prediction: Dark Vessel Activity as Early Warning Indicator
================================================================================

每日自動執行：
  1. 下載 PLA 架次資料 (JapanandBattleship.csv)
  2. 讀取本地暗船偵測資料 (vessel_data.json / dark_vessels.json)
  3. 合併日期，計算暗船 vs 架次的相關性與 Granger 因果
  4. 進行軍演事件研究
  5. 儲存結果至 data/exercise_prediction.json
================================================================================
"""

import json
import io
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# PLA 架次 CSV 來源
PLA_CSV_URL = (
    "https://raw.githubusercontent.com/s0914712/pla-data-dashboard"
    "/main/data/JapanandBattleship.csv"
)

# 已知軍演事件
MILITARY_EXERCISES = [
    {
        "name": "Joint Sword 2024A",
        "start": "2024-05-23",
        "end": "2024-05-24",
        "trigger": "賴清德就職",
    },
    {
        "name": "Joint Sword 2024B",
        "start": "2024-10-14",
        "end": "2024-10-15",
        "trigger": "雙十節演說",
    },
    {
        "name": "2024-12 軍演",
        "start": "2024-12-09",
        "end": "2024-12-11",
        "trigger": "年度例行",
    },
    {
        "name": "2025-12 軍演",
        "start": "2025-12-29",
        "end": "2025-12-31",
        "trigger": "年度例行",
    },
]

MAX_LAG = 14
MAX_LAG_GRANGER = 7


# =============================================================================
# 資料載入
# =============================================================================


def fetch_pla_sorties() -> pd.DataFrame:
    """從 GitHub 下載 PLA 架次 CSV"""
    print("   📥 下載 PLA 架次資料...")
    try:
        resp = requests.get(PLA_CSV_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"   ❌ 下載失敗: {e}")
        return pd.DataFrame()

    df = pd.read_csv(io.StringIO(resp.text))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.rename(
        columns={
            "pla_aircraft_sorties": "sorties",
            "plan_vessel_sorties": "vessels",
        }
    )
    df["sorties"] = pd.to_numeric(df["sorties"], errors="coerce").fillna(0)
    df["vessels"] = pd.to_numeric(df["vessels"], errors="coerce").fillna(0)
    print(f"      {len(df)} 筆 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


def load_dark_vessel_daily() -> pd.DataFrame:
    """
    從本地 JSON 載入每日暗船統計。
    優先使用 dark_vessels.json (overall.dark_by_date)，
    其次使用 vessel_data.json (daily)。
    """
    rows = []

    # 來源 1: dark_vessels.json (跨區域彙總)
    dark_path = DATA_DIR / "dark_vessels.json"
    if dark_path.exists():
        with open(dark_path, "r", encoding="utf-8") as f:
            dv = json.load(f)
        for date_str, count in dv.get("overall", {}).get("dark_by_date", {}).items():
            rows.append({"date": date_str, "dark_vessels": count})

    # 來源 2: vessel_data.json (SAR daily)
    vessel_path = DATA_DIR / "vessel_data.json"
    if vessel_path.exists():
        with open(vessel_path, "r", encoding="utf-8") as f:
            vd = json.load(f)
        for entry in vd.get("daily", []):
            rows.append({
                "date": entry["date"],
                "dark_vessels": entry.get("dark_vessels", 0),
            })

    if not rows:
        print("   ⚠️ 找不到暗船資料")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    # 同一天取最大值（兩個來源可能重疊）
    df = df.groupby("date", as_index=False)["dark_vessels"].max()
    df = df.sort_values("date").reset_index(drop=True)
    print(f"   🛰️ 暗船資料: {len(df)} 天 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


# =============================================================================
# 分析函數
# =============================================================================


def lag_correlation(dark: pd.Series, sorties: pd.Series, max_lag: int = MAX_LAG):
    """
    計算暗船 vs 架次的時間滯後相關係數。
    正數 lag = 暗船領先（暗船 t-lag → 架次 t）。
    """
    dark_z = (dark - dark.mean()) / dark.std() if dark.std() > 0 else dark * 0
    sort_z = (sorties - sorties.mean()) / sorties.std() if sorties.std() > 0 else sorties * 0

    results = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            r = dark_z.iloc[:lag].reset_index(drop=True).corr(
                sort_z.iloc[-lag:].reset_index(drop=True)
            )
        elif lag > 0:
            r = dark_z.iloc[lag:].reset_index(drop=True).corr(
                sort_z.iloc[:-lag].reset_index(drop=True)
            )
        else:
            r = dark_z.corr(sort_z)
        if not np.isnan(r):
            results.append({"lag": lag, "correlation": round(float(r), 6)})
    return results


def granger_test(merged: pd.DataFrame):
    """
    Granger 因果檢驗（雙向）。
    需要 scipy 與 statsmodels。
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests, adfuller
    except ImportError:
        print("   ⚠️ statsmodels 未安裝，跳過 Granger 檢驗")
        return {}

    results = {"dark_to_sorties": {}, "sorties_to_dark": {}}

    # 平穩性檢查
    def is_stationary(series):
        if len(series.dropna()) < 20:
            return False
        adf = adfuller(series.dropna(), autolag="AIC")
        return adf[1] < 0.05

    dark_stat = is_stationary(merged["dark_vessels"])
    sort_stat = is_stationary(merged["sorties"])

    if not dark_stat or not sort_stat:
        merged = merged.copy()
        merged["dark_vessels"] = merged["dark_vessels"].diff()
        merged["sorties"] = merged["sorties"].diff()

    data = merged[["sorties", "dark_vessels"]].dropna()
    # 自適應最大 lag：至少需要 3*lag 筆資料
    usable_lag = min(MAX_LAG_GRANGER, max(1, len(data) // 3 - 1))
    if len(data) < usable_lag + 5:
        print("   ⚠️ 資料不足，跳過 Granger 檢驗")
        return results

    print(f"      使用 maxlag={usable_lag} (資料 {len(data)} 筆)")

    # 暗船 → 架次
    try:
        gc = grangercausalitytests(data.values, maxlag=usable_lag, verbose=False)
        for lag in range(1, usable_lag + 1):
            f_stat = gc[lag][0]["ssr_ftest"][0]
            p_val = gc[lag][0]["ssr_ftest"][1]
            results["dark_to_sorties"][str(lag)] = {
                "f_stat": round(float(f_stat), 4),
                "p_value": round(float(p_val), 6),
                "significant": bool(p_val < 0.05),
            }
    except Exception as e:
        print(f"   ⚠️ dark→sorties 檢驗失敗: {e}")

    # 架次 → 暗船
    try:
        data_rev = data[["dark_vessels", "sorties"]]
        gc2 = grangercausalitytests(data_rev.values, maxlag=usable_lag, verbose=False)
        for lag in range(1, usable_lag + 1):
            f_stat = gc2[lag][0]["ssr_ftest"][0]
            p_val = gc2[lag][0]["ssr_ftest"][1]
            results["sorties_to_dark"][str(lag)] = {
                "f_stat": round(float(f_stat), 4),
                "p_value": round(float(p_val), 6),
                "significant": bool(p_val < 0.05),
            }
    except Exception as e:
        print(f"   ⚠️ sorties→dark 檢驗失敗: {e}")

    return results


def event_study(merged: pd.DataFrame):
    """軍演事件研究：比較軍演前 14 天 vs 軍演期間的暗船與架次。"""
    results = []
    for ex in MILITARY_EXERCISES:
        ex_start = pd.to_datetime(ex["start"])
        ex_end = pd.to_datetime(ex["end"])

        before = merged[
            (merged["date"] >= ex_start - timedelta(days=14))
            & (merged["date"] < ex_start)
        ]
        during = merged[
            (merged["date"] >= ex_start) & (merged["date"] <= ex_end)
        ]
        after = merged[
            (merged["date"] > ex_end)
            & (merged["date"] <= ex_end + timedelta(days=7))
        ]

        def safe_mean(s):
            return round(float(s.mean()), 2) if len(s) > 0 else None

        before_dark = safe_mean(before["dark_vessels"])
        during_dark = safe_mean(during["dark_vessels"])
        change_pct = None
        if before_dark and before_dark > 0 and during_dark is not None:
            change_pct = round((during_dark - before_dark) / before_dark * 100, 1)

        results.append({
            "exercise": ex["name"],
            "trigger": ex["trigger"],
            "start": ex["start"],
            "end": ex["end"],
            "before_dark_avg": before_dark,
            "during_dark_avg": during_dark,
            "after_dark_avg": safe_mean(after["dark_vessels"]),
            "dark_change_pct": change_pct,
            "before_sorties_avg": safe_mean(before["sorties"]),
            "during_sorties_avg": safe_mean(during["sorties"]),
            "before_n": len(before),
            "during_n": len(during),
        })
    return results


def high_sortie_analysis(merged: pd.DataFrame):
    """
    分析高架次事件前 7 天的暗船數量。
    門檻：平均 + 2 標準差。
    """
    if len(merged) < 10:
        return {}

    threshold = float(merged["sorties"].mean() + 2 * merged["sorties"].std())
    high_dates = merged.loc[merged["sorties"] > threshold, "date"].tolist()

    if not high_dates:
        return {"threshold": round(threshold, 1), "event_count": 0, "pre_event": {}}

    overall_mean = float(merged["dark_vessels"].mean())
    pre_event = {}
    for days_before in range(1, 8):
        values = []
        for d in high_dates:
            check = d - timedelta(days=days_before)
            row = merged.loc[merged["date"] == check, "dark_vessels"]
            if len(row) > 0:
                values.append(float(row.iloc[0]))
        if values:
            avg = round(np.mean(values), 1)
            diff_pct = round((avg - overall_mean) / overall_mean * 100, 1) if overall_mean > 0 else 0
            pre_event[str(days_before)] = {
                "avg_dark_vessels": avg,
                "diff_from_mean_pct": diff_pct,
            }

    return {
        "threshold": round(threshold, 1),
        "event_count": len(high_dates),
        "overall_dark_mean": round(overall_mean, 1),
        "pre_event": pre_event,
    }


# =============================================================================
# 主程式
# =============================================================================


def main():
    print("=" * 60)
    print("📊 軍演預測分析：暗船活動 vs PLA 架次")
    print("=" * 60)

    # 載入資料
    sorties_df = fetch_pla_sorties()
    dark_df = load_dark_vessel_daily()

    if sorties_df.empty or dark_df.empty:
        print("\n⚠️ 資料不足，無法進行分析")
        # 儲存空結果
        output = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "status": "insufficient_data",
        }
        out_path = DATA_DIR / "exercise_prediction.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 已儲存: {out_path}")
        return

    # 合併
    merged = pd.merge(
        dark_df[["date", "dark_vessels"]],
        sorties_df[["date", "sorties", "vessels"]],
        on="date",
        how="inner",
    )
    print(f"\n   📊 合併資料: {len(merged)} 天")

    if len(merged) == 0:
        print("   ⚠️ 暗船與架次資料日期無重疊")
        output = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "status": "no_overlap",
            "dark_range": f"{dark_df['date'].min():%Y-%m-%d} ~ {dark_df['date'].max():%Y-%m-%d}",
            "sorties_range": f"{sorties_df['date'].min():%Y-%m-%d} ~ {sorties_df['date'].max():%Y-%m-%d}",
        }
        out_path = DATA_DIR / "exercise_prediction.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 已儲存: {out_path}")
        return

    # 基本統計
    corr_dark_sorties = float(merged["dark_vessels"].corr(merged["sorties"]))
    corr_dark_vessels = float(merged["dark_vessels"].corr(merged["vessels"]))

    print(f"   相關係數: dark↔sorties r={corr_dark_sorties:.4f}, dark↔PLAN r={corr_dark_vessels:.4f}")

    # 時間滯後相關
    print("\n   📊 時間滯後相關分析...")
    lag_results = lag_correlation(merged["dark_vessels"], merged["sorties"])
    best = max(lag_results, key=lambda x: abs(x["correlation"]))
    print(f"      最大相關: lag={best['lag']} 天, r={best['correlation']:.4f}")

    # Granger 因果
    print("\n   📊 Granger 因果檢驗...")
    granger_results = granger_test(merged.copy())

    sig_lags = [
        k for k, v in granger_results.get("dark_to_sorties", {}).items()
        if v.get("significant")
    ]
    if sig_lags:
        print(f"      暗船→架次: 顯著 (lag={sig_lags})")
    else:
        print("      暗船→架次: 不顯著")

    # 軍演事件研究
    print("\n   📊 軍演事件研究...")
    events = event_study(merged)
    for ev in events:
        if ev["before_n"] > 0 and ev["during_n"] > 0:
            print(f"      {ev['exercise']}: 暗船 {ev['before_dark_avg']}→{ev['during_dark_avg']} "
                  f"({ev['dark_change_pct']:+.1f}%)" if ev["dark_change_pct"] is not None else
                  f"      {ev['exercise']}: N/A")
        else:
            print(f"      {ev['exercise']}: 資料不足")

    # 高架次前暗船分析
    print("\n   📊 高架次前暗船分析...")
    high_sortie = high_sortie_analysis(merged)
    if high_sortie.get("event_count", 0) > 0:
        print(f"      門檻: >{high_sortie['threshold']} 架次, {high_sortie['event_count']} 事件")

    # 組裝輸出
    output = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "status": "ok",
        "data_summary": {
            "merged_days": len(merged),
            "date_range": f"{merged['date'].min():%Y-%m-%d} ~ {merged['date'].max():%Y-%m-%d}",
            "dark_mean": round(float(merged["dark_vessels"].mean()), 1),
            "dark_std": round(float(merged["dark_vessels"].std()), 1),
            "sorties_mean": round(float(merged["sorties"].mean()), 1),
            "sorties_std": round(float(merged["sorties"].std()), 1),
            "correlation_dark_sorties": round(corr_dark_sorties, 4),
            "correlation_dark_plan_vessels": round(corr_dark_vessels, 4),
        },
        "lag_correlation": {
            "max_lag": MAX_LAG,
            "best_lag": best["lag"],
            "best_correlation": best["correlation"],
            "values": lag_results,
        },
        "granger_causality": granger_results,
        "event_study": events,
        "high_sortie_analysis": high_sortie,
        "daily_merged": [
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "dark_vessels": int(row["dark_vessels"]),
                "sorties": float(row["sorties"]),
                "vessels": float(row["vessels"]),
            }
            for _, row in merged.iterrows()
        ],
    }

    out_path = DATA_DIR / "exercise_prediction.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 分析結果已儲存: {out_path}")


if __name__ == "__main__":
    main()

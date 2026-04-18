#!/usr/bin/env python3
"""
================================================================================
SCFI vs 船舶流量相關性分析
SCFI vs Vessel Traffic Correlation Analysis
================================================================================

分析上海出口集裝箱運價指數 (SCFI) 與台灣周邊商船流量的關係：
  1. 載入 SCFI 週資料 (scfi_history.json)
  2. 載入 AIS 日資料 (ais_history.json) → 聚合為週頻
  3. 計算 Pearson / Spearman 相關係數
  4. 滯後相關分析 (lag -4 ~ +4 週)
  5. Granger 因果檢驗（雙向）
  6. 週變化率相關分析 (rate of change)
  7. 自動產生結論與預測可行性評估

Output: data/scfi_vessel_correlation.json
================================================================================
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DATA_DIR.mkdir(exist_ok=True)

SCFI_FILE = DATA_DIR / "scfi_history.json"
AIS_HISTORY_FILE = DOCS_DIR / "ais_history.json"
AIS_HISTORY_FALLBACK = DATA_DIR / "ais_history.json"
OUTPUT_FILE = DATA_DIR / "scfi_vessel_correlation.json"

MAX_LAG = 4  # 週
MAX_LAG_GRANGER = 3

# =============================================================================
# 資料載入
# =============================================================================


def load_scfi() -> pd.DataFrame:
    """載入 SCFI 週資料"""
    if not SCFI_FILE.exists():
        print("   ⚠️ 找不到 scfi_history.json")
        return pd.DataFrame()
    try:
        with open(SCFI_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"   ⚠️ SCFI 讀取失敗: {e}")
        return pd.DataFrame()

    rows = []
    for entry in payload.get("data", []):
        rows.append({
            "date": entry.get("date"),
            "composite": entry.get("composite"),
            "europe": entry.get("sub_routes", {}).get("europe"),
            "uswc": entry.get("sub_routes", {}).get("uswc"),
            "usec": entry.get("sub_routes", {}).get("usec"),
            "southeast_asia": entry.get("sub_routes", {}).get("southeast_asia"),
            "japan": entry.get("sub_routes", {}).get("japan"),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "composite"]).sort_values("date").reset_index(drop=True)
    print(f"   📊 SCFI: {len(df)} 週 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


def load_ais_daily() -> pd.DataFrame:
    """載入 AIS 日資料並聚合為每日平均"""
    src = AIS_HISTORY_FILE if AIS_HISTORY_FILE.exists() else AIS_HISTORY_FALLBACK
    if not src.exists():
        print("   ⚠️ 找不到 ais_history.json")
        return pd.DataFrame()

    try:
        with open(src, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"   ⚠️ AIS 讀取失敗: {e}")
        return pd.DataFrame()

    rows = []
    for entry in history:
        stats = entry.get("stats", {})
        by_type = stats.get("by_type", {}) or {}
        rows.append({
            "date": entry.get("date"),
            "total_vessels": stats.get("total_vessels", 0),
            "cargo": by_type.get("cargo", 0),
            "tanker": by_type.get("tanker", 0),
            "fishing_vessels": stats.get("fishing_vessels", 0),
        })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    # 一天可能有多筆快照 → 取均值
    df = df.groupby("date", as_index=False).mean(numeric_only=True)
    df["commercial"] = df["cargo"] + df["tanker"]
    df = df.sort_values("date").reset_index(drop=True)
    print(f"   🚢 AIS: {len(df)} 天 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


def aggregate_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """將日資料聚合為週五結束的週平均（與 SCFI 發布日對齊）"""
    if daily.empty:
        return daily
    weekly = (
        daily.set_index("date")
        .resample("W-FRI")
        .mean(numeric_only=True)
        .reset_index()
    )
    weekly = weekly.rename(columns={"date": "week_ending"})
    return weekly


# =============================================================================
# 相關性分析
# =============================================================================


def pearson_spearman(x: pd.Series, y: pd.Series) -> dict:
    """計算 Pearson 與 Spearman 相關係數與 p-value"""
    try:
        from scipy import stats as sstats
    except ImportError:
        return {}

    valid = (~x.isna()) & (~y.isna())
    x, y = x[valid], y[valid]
    n = len(x)
    if n < 3 or x.std() == 0 or y.std() == 0:
        return {
            "pearson_r": None,
            "pearson_p": None,
            "spearman_r": None,
            "spearman_p": None,
            "n": int(n),
        }

    pr, pp = sstats.pearsonr(x, y)
    sr, sp = sstats.spearmanr(x, y)
    return {
        "pearson_r": round(float(pr), 4),
        "pearson_p": round(float(pp), 6),
        "spearman_r": round(float(sr), 4),
        "spearman_p": round(float(sp), 6),
        "n": int(n),
        "significant_pearson": bool(pp < 0.05),
        "significant_spearman": bool(sp < 0.05),
    }


def lag_correlation(x: pd.Series, y: pd.Series, max_lag: int = MAX_LAG) -> list:
    """
    計算 x vs y 的時間滯後相關係數。
    正數 lag = x 領先（x_{t-lag} → y_t，亦即船舶領先 SCFI）。
    每個 lag 至少需有 MIN_LAG_N 個有效重疊點，避免小樣本產生 ±1.0 假相關。
    """
    MIN_LAG_N = 4
    x = x.reset_index(drop=True)
    y = y.reset_index(drop=True)

    if x.std() == 0 or y.std() == 0:
        return []

    xz = (x - x.mean()) / x.std()
    yz = (y - y.mean()) / y.std()

    results = []
    n = len(x)
    for lag in range(-max_lag, max_lag + 1):
        overlap = n - abs(lag)
        if overlap < MIN_LAG_N:
            continue
        if lag < 0:
            xs = xz.iloc[:lag].reset_index(drop=True)
            ys = yz.iloc[-lag:].reset_index(drop=True)
        elif lag > 0:
            xs = xz.iloc[lag:].reset_index(drop=True)
            ys = yz.iloc[:-lag].reset_index(drop=True)
        else:
            xs, ys = xz, yz
        r = xs.corr(ys)
        if pd.notna(r):
            results.append({
                "lag": int(lag),
                "correlation": round(float(r), 4),
                "n": int(overlap),
            })
    return results


def best_lag(lags: list) -> dict:
    """找出最大絕對相關的 lag"""
    if not lags:
        return {}
    best = max(lags, key=lambda x: abs(x["correlation"]))
    lag = best["lag"]
    if lag > 0:
        interpretation = f"船舶流量領先 SCFI {lag} 週"
    elif lag < 0:
        interpretation = f"SCFI 領先船舶流量 {abs(lag)} 週"
    else:
        interpretation = "同步相關（無明確領先關係）"
    return {
        "lag": lag,
        "correlation": best["correlation"],
        "interpretation": interpretation,
    }


def granger_test(merged: pd.DataFrame, col_x: str, col_y: str) -> dict:
    """
    雙向 Granger 因果檢驗。
    col_x, col_y 為欲比較的兩個欄位名稱。
    回傳 {x_to_y: {...}, y_to_x: {...}}
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests, adfuller
    except ImportError:
        print("   ⚠️ statsmodels 未安裝，跳過 Granger 檢驗")
        return {}

    data = merged[[col_x, col_y]].dropna().copy()
    if len(data) < 10:
        return {"note": f"資料不足 ({len(data)} 筆)，需至少 10 週"}

    # 平穩性檢查
    def is_stationary(series):
        s = series.dropna()
        if len(s) < 8:
            return False
        try:
            adf = adfuller(s, autolag="AIC")
            return adf[1] < 0.05
        except Exception:
            return False

    if not (is_stationary(data[col_x]) and is_stationary(data[col_y])):
        data[col_x] = data[col_x].diff()
        data[col_y] = data[col_y].diff()
        data = data.dropna()

    if len(data) < 10:
        return {"note": "差分後資料不足"}

    usable_lag = min(MAX_LAG_GRANGER, max(1, len(data) // 3 - 1))
    print(f"      Granger maxlag={usable_lag} (n={len(data)})")

    results = {"x_to_y": {}, "y_to_x": {}}

    # x → y（例如 vessels → scfi）
    try:
        gc = grangercausalitytests(data[[col_y, col_x]].values, maxlag=usable_lag, verbose=False)
        for lag in range(1, usable_lag + 1):
            p_val = gc[lag][0]["ssr_ftest"][1]
            f_stat = gc[lag][0]["ssr_ftest"][0]
            results["x_to_y"][str(lag)] = {
                "f_stat": round(float(f_stat), 4),
                "p_value": round(float(p_val), 6),
                "significant": bool(p_val < 0.05),
            }
    except Exception as e:
        results["x_to_y"]["error"] = str(e)

    # y → x
    try:
        gc2 = grangercausalitytests(data[[col_x, col_y]].values, maxlag=usable_lag, verbose=False)
        for lag in range(1, usable_lag + 1):
            p_val = gc2[lag][0]["ssr_ftest"][1]
            f_stat = gc2[lag][0]["ssr_ftest"][0]
            results["y_to_x"][str(lag)] = {
                "f_stat": round(float(f_stat), 4),
                "p_value": round(float(p_val), 6),
                "significant": bool(p_val < 0.05),
            }
    except Exception as e:
        results["y_to_x"]["error"] = str(e)

    return results


def make_conclusion(corr: dict, lag: dict, granger: dict, n: int) -> dict:
    """生成中英雙語結論"""
    if n < 5:
        return {
            "zh": f"資料樣本過少（僅 {n} 週），無法進行有意義的統計推論。隨著資料累積，分析會逐漸具有可信度。",
            "en": f"Insufficient sample ({n} weeks) for meaningful inference. Analysis will improve as more data accumulates.",
            "predictability": "insufficient",
        }

    pr = corr.get("commercial", {}).get("pearson_r")
    pp = corr.get("commercial", {}).get("pearson_p")

    strength = "無明顯"
    if pr is not None:
        abs_r = abs(pr)
        if abs_r >= 0.7:
            strength = "強烈"
        elif abs_r >= 0.4:
            strength = "中度"
        elif abs_r >= 0.2:
            strength = "弱"

    direction = "正" if (pr is not None and pr > 0) else "負"
    sig_zh = "具統計顯著" if (pp is not None and pp < 0.05) else "未達統計顯著"

    best = lag.get("commercial", {}) or {}
    lag_val = best.get("lag", 0)
    lag_r = best.get("correlation", 0)
    lag_interp = best.get("interpretation", "")

    # 預測可行性判斷
    granger_v2s = granger.get("vessels_to_scfi", {})
    granger_s2v = granger.get("scfi_to_vessels", {})
    v2s_sig = any(
        isinstance(v, dict) and v.get("significant")
        for v in granger_v2s.values()
    )
    s2v_sig = any(
        isinstance(v, dict) and v.get("significant")
        for v in granger_s2v.values()
    )

    strength_en_map = {
        "強烈": "strong",
        "中度": "moderate",
        "弱": "weak",
        "無明顯": "negligible",
    }
    strength_en = strength_en_map.get(strength, "unknown")
    direction_en = "positive" if direction == "正" else "negative"

    if v2s_sig and not s2v_sig:
        predictability = "vessels_predict_scfi"
        pred_zh = "✅ 船舶流量具統計顯著的領先預測能力（可用於預測 SCFI）"
        pred_en = "Vessel traffic shows statistically significant predictive power for SCFI"
    elif s2v_sig and not v2s_sig:
        predictability = "scfi_predicts_vessels"
        pred_zh = "✅ SCFI 具統計顯著的領先預測能力（可用於預測船舶流量）"
        pred_en = "SCFI shows statistically significant predictive power for vessel traffic"
    elif v2s_sig and s2v_sig:
        predictability = "bidirectional"
        pred_zh = "✅ 雙向具顯著 Granger 因果（互為預測指標）"
        pred_en = "Bidirectional Granger causality detected (mutual predictive power)"
    else:
        if pr is not None and abs(pr) >= 0.4:
            predictability = "correlated_no_causality"
            pred_zh = f"⚠️ 存在 {strength}{direction}相關，但未通過 Granger 因果檢驗，不建議單獨用於預測"
            pred_en = f"{strength_en} {direction_en} correlation detected but no Granger causality; not recommended for standalone prediction"
        else:
            predictability = "weak"
            pred_zh = "❌ 相關性與因果性皆不顯著，預測可行性低"
            pred_en = "Weak correlation and no causality; low predictive feasibility"

    zh = (
        f"【SCFI vs 台灣周邊商船流量分析（n={n} 週）】\n"
        f"• 商船（貨+油）與 SCFI 綜合指數呈 {strength}{direction}相關 "
        f"(Pearson r={pr:.3f}, p={pp:.4f}, {sig_zh})\n"
        f"• 最佳滯後: lag={lag_val} 週，r={lag_r:.3f} → {lag_interp}\n"
        f"• 預測可行性: {pred_zh}\n"
        f"• 註：SCFI 為週資料，小樣本下結論僅供參考；建議持續累積至 30 週以上再作決策依據。"
    )

    lag_interp_en = ""
    if lag_val > 0:
        lag_interp_en = f"vessel traffic leads SCFI by {lag_val} weeks"
    elif lag_val < 0:
        lag_interp_en = f"SCFI leads vessel traffic by {abs(lag_val)} weeks"
    else:
        lag_interp_en = "synchronous (no lead/lag)"

    en = (
        f"[SCFI vs Taiwan-area Commercial Vessels (n={n} weeks)] "
        f"Commercial vessels (cargo+tanker) vs SCFI composite: Pearson r={pr:.3f}, p={pp:.4f}. "
        f"Best lag: {lag_val} weeks (r={lag_r:.3f}) - {lag_interp_en}. {pred_en}."
    )

    return {
        "zh": zh,
        "en": en,
        "predictability": predictability,
        "correlation_strength": strength,
    }


# =============================================================================
# 主流程
# =============================================================================


def main():
    print("=" * 70)
    print("📊 SCFI vs 船舶流量 相關性分析")
    print("=" * 70)

    scfi_df = load_scfi()
    ais_df = load_ais_daily()

    if scfi_df.empty or ais_df.empty:
        output = {
            "status": "insufficient_data",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": "SCFI 或 AIS 資料缺失",
            "interpretation": {
                "zh": "資料來源不完整，無法執行分析",
                "en": "Missing data sources; analysis skipped",
            },
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return

    # 聚合 AIS 為週資料
    weekly_ais = aggregate_weekly(ais_df)
    print(f"   📅 AIS 週聚合: {len(weekly_ais)} 週")

    # 合併（以週結束日為對齊基準）
    scfi_df["week_ending"] = pd.to_datetime(scfi_df["date"])
    merged = pd.merge(
        scfi_df[["week_ending", "composite", "europe", "uswc", "usec",
                 "southeast_asia", "japan"]],
        weekly_ais,
        on="week_ending",
        how="inner",
    )
    merged = merged.sort_values("week_ending").reset_index(drop=True)
    print(f"   🔗 合併後: {len(merged)} 週重疊")

    if len(merged) < 3:
        output = {
            "status": "no_overlap",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sample_size": int(len(merged)),
            "scfi_range": [
                scfi_df["date"].min().strftime("%Y-%m-%d"),
                scfi_df["date"].max().strftime("%Y-%m-%d"),
            ],
            "ais_range": [
                ais_df["date"].min().strftime("%Y-%m-%d"),
                ais_df["date"].max().strftime("%Y-%m-%d"),
            ],
            "interpretation": {
                "zh": f"SCFI 與 AIS 資料重疊不足（僅 {len(merged)} 週），需累積更多資料",
                "en": f"Insufficient overlap ({len(merged)} weeks) between SCFI and AIS data",
            },
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"   ⚠️ 重疊不足，寫入 {OUTPUT_FILE}")
        return

    # 相關性分析：多個船舶指標 vs SCFI 綜合指數
    targets = {
        "commercial": ("商船(貨+油)", "commercial"),
        "cargo": ("貨船", "cargo"),
        "tanker": ("油輪", "tanker"),
        "total_vessels": ("全部船舶", "total_vessels"),
        "fishing_vessels": ("漁船(對照組)", "fishing_vessels"),
    }

    correlations = {}
    for key, (label, col) in targets.items():
        if col not in merged.columns:
            continue
        r = pearson_spearman(merged[col], merged["composite"])
        r["label"] = label
        correlations[key] = r

    # 滯後相關分析（focus on commercial vessels）
    lag_results = {}
    for key, (label, col) in targets.items():
        if col not in merged.columns:
            continue
        lags = lag_correlation(merged[col], merged["composite"])
        lag_results[key] = {
            "label": label,
            "lags": lags,
            "best": best_lag(lags),
        }

    # Granger 因果檢驗（commercial vs composite）
    print("   🔬 Granger 因果檢驗...")
    granger_raw = granger_test(merged, "commercial", "composite")
    granger = {
        "vessels_to_scfi": granger_raw.get("x_to_y", {}),
        "scfi_to_vessels": granger_raw.get("y_to_x", {}),
    }
    if "note" in granger_raw:
        granger["note"] = granger_raw["note"]

    # 週變化率相關
    merged_copy = merged.copy()
    merged_copy["composite_pct"] = merged_copy["composite"].pct_change() * 100
    merged_copy["commercial_pct"] = merged_copy["commercial"].pct_change() * 100
    roc_corr = pearson_spearman(
        merged_copy["commercial_pct"], merged_copy["composite_pct"]
    )

    # 子航線分析
    sub_route_corr = {}
    for route in ["europe", "uswc", "usec", "southeast_asia", "japan"]:
        if route in merged.columns:
            sub_route_corr[route] = pearson_spearman(
                merged["commercial"], merged[route]
            )

    # 產生結論
    best_lag_dict = {k: v.get("best", {}) for k, v in lag_results.items()}
    conclusion = make_conclusion(correlations, best_lag_dict, granger, len(merged))

    # 週序列（供前端圖表）
    weekly_series = []
    for _, row in merged.iterrows():
        weekly_series.append({
            "week_ending": row["week_ending"].strftime("%Y-%m-%d"),
            "scfi_composite": round(float(row["composite"]), 2),
            "commercial": round(float(row["commercial"]), 1),
            "cargo": round(float(row["cargo"]), 1),
            "tanker": round(float(row["tanker"]), 1),
            "total_vessels": round(float(row["total_vessels"]), 1),
            "fishing_vessels": round(float(row["fishing_vessels"]), 1),
        })

    output = {
        "status": "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "description": "SCFI 上海出口集裝箱運價指數 vs 台灣周邊船舶流量相關性分析",
        "sample_size": int(len(merged)),
        "data_summary": {
            "scfi_range": [
                merged["week_ending"].min().strftime("%Y-%m-%d"),
                merged["week_ending"].max().strftime("%Y-%m-%d"),
            ],
            "scfi_mean": round(float(merged["composite"].mean()), 2),
            "scfi_std": round(float(merged["composite"].std()), 2),
            "commercial_mean": round(float(merged["commercial"].mean()), 1),
            "commercial_std": round(float(merged["commercial"].std()), 1),
        },
        "correlations": correlations,
        "sub_route_correlations": sub_route_corr,
        "lag_analysis": lag_results,
        "granger_causality": granger,
        "rate_of_change_correlation": roc_corr,
        "conclusion": conclusion,
        "weekly_series": weekly_series,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"   💾 已儲存 → {OUTPUT_FILE}")
    print(f"   📝 結論: {conclusion['zh'][:80]}...")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"❌ 分析失敗: {e}", file=sys.stderr)
        traceback.print_exc()
        # 即使失敗也產出狀態檔
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "status": "error",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }, f, ensure_ascii=False, indent=2)
        sys.exit(0)

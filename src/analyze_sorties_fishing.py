#!/usr/bin/env python3
"""
================================================================================
共機架次 vs 漁船數量 相關性分析
PLA Sorties vs. Fishing Vessel Correlation Analysis
================================================================================

分析共機架次與台灣周邊（特別是東北角）漁船數量的相關性。
使用 2026 年 3 月的 AIS 漁船偵測資料與 PLA 架次資料。

資料來源:
  - PLA 架次: pla-data-dashboard/data/JapanandBattleship.csv (GitHub)
  - 漁船偵測: data/ais_history.json (本地 AIS 歷史快照)

輸出:
  - data/sorties_fishing_correlation.json
  - data/charts/sorties_fishing_timeseries.png
  - data/charts/sorties_fishing_scatter.png
  - data/charts/sorties_fishing_lagged.png
================================================================================
"""

import json
import io
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from scipy import stats

DATA_DIR = Path("data")
CHARTS_DIR = DATA_DIR / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

PLA_CSV_URL = (
    "https://raw.githubusercontent.com/s0914712/pla-data-dashboard"
    "/main/data/JapanandBattleship.csv"
)

MAX_LAG = 7  # ±7 days (conservative for 21-day sample)
HIGH_SORTIE_THRESHOLD = 20
EVENT_WINDOW = 3  # ±3 days around high-sortie events


# =============================================================================
# 資料載入
# =============================================================================


def fetch_pla_sorties() -> pd.DataFrame:
    """從 GitHub 下載 PLA 架次 CSV，回傳每日架次 DataFrame。"""
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
    df["sorties"] = pd.to_numeric(df["pla_aircraft_sorties"], errors="coerce").fillna(0)
    df = df[["date", "sorties"]].copy()
    print(f"      {len(df)} 筆 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


def load_ais_history() -> pd.DataFrame:
    """
    從 data/ais_history.json 載入 AIS 歷史快照。
    每天可能有多筆快照（每 2 小時），取每日平均值。
    """
    ais_path = DATA_DIR / "ais_history.json"
    if not ais_path.exists():
        print("   ⚠️ 找不到 ais_history.json")
        return pd.DataFrame()

    with open(ais_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for entry in data:
        date_str = entry.get("date")
        s = entry.get("stats", {})
        hotspots = s.get("in_fishing_hotspots", {})
        drill_zones = s.get("in_drill_zones", {})
        rows.append({
            "date": date_str,
            "fishing_vessels": s.get("fishing_vessels", 0),
            "total_vessels": s.get("total_vessels", 0),
            "northeast_fishing": hotspots.get("northeast", 0),
            "taiwan_bank_fishing": hotspots.get("taiwan_bank", 0),
            "penghu_fishing": hotspots.get("penghu", 0),
            "kuroshio_east_fishing": hotspots.get("kuroshio_east", 0),
            "southwest_fishing": hotspots.get("southwest", 0),
            "drill_zone_total": sum(drill_zones.values()),
        })

    if not rows:
        print("   ⚠️ ais_history.json 無資料")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    # 每日取平均（多筆快照）
    numeric_cols = [c for c in df.columns if c != "date"]
    df = df.groupby("date", as_index=False)[numeric_cols].mean()
    df = df.sort_values("date").reset_index(drop=True)

    # 四捨五入
    for col in numeric_cols:
        df[col] = df[col].round(1)

    print(f"   🚢 AIS 漁船資料: {len(df)} 天 ({df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d})")
    return df


# =============================================================================
# 資料合併
# =============================================================================


def merge_datasets(sorties_df: pd.DataFrame, ais_df: pd.DataFrame) -> pd.DataFrame:
    """合併架次與漁船資料（inner join on date）。"""
    if sorties_df.empty or ais_df.empty:
        print("   ❌ 資料不足，無法合併")
        return pd.DataFrame()

    merged = pd.merge(sorties_df, ais_df, on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    print(f"   📊 合併資料: {len(merged)} 天 ({merged['date'].min():%Y-%m-%d} ~ {merged['date'].max():%Y-%m-%d})")
    return merged


# =============================================================================
# 統計分析
# =============================================================================


def compute_correlations(merged: pd.DataFrame) -> dict:
    """計算各漁船指標 vs 架次的 Pearson & Spearman 相關係數。"""
    fishing_cols = [
        ("northeast_fishing", "東北角漁船"),
        ("fishing_vessels", "總漁船數"),
        ("total_vessels", "總船隻數"),
        ("drill_zone_total", "演習區內船隻"),
        ("taiwan_bank_fishing", "台灣灘漁船"),
        ("penghu_fishing", "澎湖漁船"),
        ("kuroshio_east_fishing", "黑潮東側漁船"),
        ("southwest_fishing", "西南漁船"),
    ]
    results = {}
    for col, label in fishing_cols:
        if col not in merged.columns:
            continue
        x = merged["sorties"]
        y = merged[col]
        if x.std() == 0 or y.std() == 0:
            continue
        pr, pp = stats.pearsonr(x, y)
        sr, sp = stats.spearmanr(x, y)
        results[col] = {
            "label": label,
            "pearson_r": round(float(pr), 4),
            "pearson_p": round(float(pp), 4),
            "spearman_r": round(float(sr), 4),
            "spearman_p": round(float(sp), 4),
            "n": len(x),
            "significant_pearson": bool(pp < 0.05),
            "significant_spearman": bool(sp < 0.05),
        }
        sig = "✅" if pp < 0.05 or sp < 0.05 else "❌"
        print(f"      {label}: Pearson r={pr:.3f} (p={pp:.3f}), Spearman r={sr:.3f} (p={sp:.3f}) {sig}")
    return results


def lag_correlation(x: pd.Series, y: pd.Series, max_lag: int = MAX_LAG) -> list:
    """
    計算時間滯後相關係數。
    正數 lag = x 領先（x at t-lag → y at t）。
    """
    x_z = (x - x.mean()) / x.std() if x.std() > 0 else x * 0
    y_z = (y - y.mean()) / y.std() if y.std() > 0 else y * 0

    results = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            r = x_z.iloc[:lag].reset_index(drop=True).corr(
                y_z.iloc[-lag:].reset_index(drop=True)
            )
        elif lag > 0:
            r = x_z.iloc[lag:].reset_index(drop=True).corr(
                y_z.iloc[:-lag].reset_index(drop=True)
            )
        else:
            r = x_z.corr(y_z)
        if not np.isnan(r):
            results.append({"lag": lag, "correlation": round(float(r), 4)})
    return results


def compute_lagged_correlations(merged: pd.DataFrame) -> dict:
    """計算東北角漁船 & 總漁船的滯後相關係數。"""
    results = {}
    for col, label in [("northeast_fishing", "東北角漁船"), ("fishing_vessels", "總漁船數")]:
        if col not in merged.columns:
            continue
        lags = lag_correlation(merged[col], merged["sorties"])
        best = max(lags, key=lambda x: abs(x["correlation"])) if lags else None
        results[col] = {
            "label": label,
            "lags": lags,
            "best_lag": best,
        }
        if best:
            direction = "漁船領先" if best["lag"] > 0 else ("架次領先" if best["lag"] < 0 else "同步")
            print(f"      {label}: 最佳 lag={best['lag']} (r={best['correlation']:.3f}, {direction})")
    return results


def analyze_high_sortie_events(merged: pd.DataFrame) -> list:
    """
    高架次事件分析：觀察高架次日前後漁船數量變化。
    """
    high_days = merged[merged["sorties"] > HIGH_SORTIE_THRESHOLD].copy()
    if high_days.empty:
        print("   ⚠️ 無高架次事件")
        return []

    print(f"\n   🔍 高架次事件分析 (門檻 > {HIGH_SORTIE_THRESHOLD} 架次):")
    events = []
    overall_ne_avg = merged["northeast_fishing"].mean()
    overall_fish_avg = merged["fishing_vessels"].mean()

    for _, row in high_days.iterrows():
        event_date = row["date"]
        window = merged[
            (merged["date"] >= event_date - timedelta(days=EVENT_WINDOW))
            & (merged["date"] <= event_date + timedelta(days=EVENT_WINDOW))
        ]

        # 事件日前後的漁船數據
        before = merged[
            (merged["date"] >= event_date - timedelta(days=EVENT_WINDOW))
            & (merged["date"] < event_date)
        ]
        after = merged[
            (merged["date"] > event_date)
            & (merged["date"] <= event_date + timedelta(days=EVENT_WINDOW))
        ]

        event = {
            "date": event_date.strftime("%Y-%m-%d"),
            "sorties": float(row["sorties"]),
            "northeast_fishing_on_day": float(row["northeast_fishing"]),
            "total_fishing_on_day": float(row["fishing_vessels"]),
            "window_avg_northeast": round(float(window["northeast_fishing"].mean()), 1),
            "overall_avg_northeast": round(float(overall_ne_avg), 1),
            "northeast_anomaly_pct": round(
                (window["northeast_fishing"].mean() - overall_ne_avg) / overall_ne_avg * 100, 1
            ) if overall_ne_avg > 0 else 0,
            "before_avg_northeast": round(float(before["northeast_fishing"].mean()), 1) if len(before) > 0 else None,
            "after_avg_northeast": round(float(after["northeast_fishing"].mean()), 1) if len(after) > 0 else None,
            "window_data": [
                {
                    "date": r["date"].strftime("%Y-%m-%d"),
                    "sorties": float(r["sorties"]),
                    "northeast_fishing": float(r["northeast_fishing"]),
                    "fishing_vessels": float(r["fishing_vessels"]),
                }
                for _, r in window.iterrows()
            ],
        }
        events.append(event)
        anomaly = event["northeast_anomaly_pct"]
        sign = "↑" if anomaly > 0 else "↓"
        print(f"      {event_date:%Y-%m-%d} ({row['sorties']:.0f} 架次): "
              f"東北角 {row['northeast_fishing']:.0f} 艘 "
              f"(窗口均值 vs 整體: {sign}{abs(anomaly):.1f}%)")

    return events


# =============================================================================
# 圖表產生
# =============================================================================


def generate_charts(merged: pd.DataFrame, results: dict):
    """產生分析圖表。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.size"] = 10

    # --- 1. 雙軸時間序列圖 ---
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.bar(merged["date"], merged["sorties"], alpha=0.6, color="#ff6b35",
            label="PLA Sorties", width=0.8)
    ax1.set_xlabel("Date")
    ax1.set_ylabel("PLA Aircraft Sorties", color="#ff6b35")
    ax1.tick_params(axis="y", labelcolor="#ff6b35")

    ax2 = ax1.twinx()
    ax2.plot(merged["date"], merged["northeast_fishing"], color="#00ff88",
             linewidth=2, marker="o", markersize=4, label="NE Fishing Vessels")
    ax2.set_ylabel("Northeast Fishing Vessels", color="#00ff88")
    ax2.tick_params(axis="y", labelcolor="#00ff88")

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    fig.autofmt_xdate(rotation=45)

    # 標記高架次日
    high_days = merged[merged["sorties"] > HIGH_SORTIE_THRESHOLD]
    for _, row in high_days.iterrows():
        ax1.annotate(f'{row["sorties"]:.0f}',
                     xy=(row["date"], row["sorties"]),
                     fontsize=8, ha="center", va="bottom", color="#ff3366")

    corr_info = results.get("correlations", {}).get("northeast_fishing", {})
    r_val = corr_info.get("pearson_r", "N/A")
    p_val = corr_info.get("pearson_p", "N/A")
    title = (f"PLA Sorties vs Northeast Fishing Vessels (March 2026)\n"
             f"Pearson r={r_val}, p={p_val} | n={len(merged)} days")
    ax1.set_title(title, fontsize=11)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    plt.tight_layout()
    fig.savefig(CHARTS_DIR / "sorties_fishing_timeseries.png", dpi=150)
    plt.close(fig)
    print("   📈 sorties_fishing_timeseries.png")

    # --- 2. 散佈圖 ---
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(merged["northeast_fishing"], merged["sorties"],
               c="#00f5ff", edgecolors="#0a0f1c", s=60, alpha=0.8)

    # 回歸線
    x = merged["northeast_fishing"]
    y = merged["sorties"]
    if len(x) > 2:
        slope, intercept, r_val_line, p_val_line, _ = stats.linregress(x, y)
        x_range = np.linspace(x.min(), x.max(), 100)
        ax.plot(x_range, slope * x_range + intercept, color="#ff3366",
                linestyle="--", linewidth=1.5,
                label=f"OLS: r={r_val_line:.3f}, p={p_val_line:.3f}")
        ax.legend(fontsize=9)

    # 標記高架次日
    for _, row in high_days.iterrows():
        ax.annotate(f'{row["date"]:%m/%d}',
                    xy=(row["northeast_fishing"], row["sorties"]),
                    fontsize=7, ha="left", va="bottom", color="#ff6b35")

    ax.set_xlabel("Northeast Fishing Vessels (daily avg)")
    ax.set_ylabel("PLA Aircraft Sorties")
    ax.set_title("Scatter: NE Fishing vs PLA Sorties")
    plt.tight_layout()
    fig.savefig(CHARTS_DIR / "sorties_fishing_scatter.png", dpi=150)
    plt.close(fig)
    print("   📈 sorties_fishing_scatter.png")

    # --- 3. 滯後相關係數圖 ---
    lag_data = results.get("lagged_correlations", {}).get("northeast_fishing", {})
    lags = lag_data.get("lags", [])
    if lags:
        fig, ax = plt.subplots(figsize=(9, 5))
        lag_vals = [l["lag"] for l in lags]
        corr_vals = [l["correlation"] for l in lags]
        colors = ["#ff3366" if abs(c) == max(abs(cv) for cv in corr_vals)
                   else "#00f5ff" for c in corr_vals]
        ax.bar(lag_vals, corr_vals, color=colors, alpha=0.8)
        ax.axhline(y=0, color="#888888", linewidth=0.5)
        ax.set_xlabel("Lag (days)\n← Sorties lead | Fishing leads →")
        ax.set_ylabel("Cross-correlation")
        best = lag_data.get("best_lag", {})
        ax.set_title(
            f"Lagged Cross-Correlation: NE Fishing vs PLA Sorties\n"
            f"Best lag={best.get('lag', '?')} days (r={best.get('correlation', '?')})"
        )
        plt.tight_layout()
        fig.savefig(CHARTS_DIR / "sorties_fishing_lagged.png", dpi=150)
        plt.close(fig)
        print("   📈 sorties_fishing_lagged.png")


# =============================================================================
# 主程式
# =============================================================================


def main():
    print("=" * 60)
    print("共機架次 vs 漁船數量 相關性分析")
    print("PLA Sorties vs Fishing Vessel Correlation")
    print("=" * 60)

    # 1. 載入資料
    print("\n[1/5] 載入資料...")
    sorties_df = fetch_pla_sorties()
    ais_df = load_ais_history()

    # 2. 合併
    print("\n[2/5] 合併資料...")
    merged = merge_datasets(sorties_df, ais_df)
    if merged.empty:
        print("   ❌ 無重疊資料，分析中止")
        return

    # 3. 相關性分析
    print("\n[3/5] 相關性分析...")
    correlations = compute_correlations(merged)

    print("\n[4/5] 滯後相關分析...")
    lagged = compute_lagged_correlations(merged)

    # 4. 高架次事件分析
    events = analyze_high_sortie_events(merged)

    # 5. 組裝結果
    output = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "description": "共機架次 vs 漁船數量相關性分析 (PLA Sorties vs Fishing Vessels)",
        "data_range": {
            "start": merged["date"].min().strftime("%Y-%m-%d"),
            "end": merged["date"].max().strftime("%Y-%m-%d"),
        },
        "sample_size": len(merged),
        "caveat": (
            f"樣本僅 {len(merged)} 天 (2026年3月)。"
            "結果為探索性分析，不具統計結論性。需要更長時間序列驗證。"
        ),
        "correlations": correlations,
        "lagged_correlations": {
            k: {"label": v["label"], "best_lag": v["best_lag"], "lags": v["lags"]}
            for k, v in lagged.items()
        },
        "high_sortie_events": {
            "threshold": HIGH_SORTIE_THRESHOLD,
            "event_window_days": EVENT_WINDOW,
            "events": events,
        },
        "daily_data": [
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "sorties": float(row["sorties"]),
                "northeast_fishing": float(row["northeast_fishing"]),
                "fishing_vessels": float(row["fishing_vessels"]),
                "total_vessels": float(row["total_vessels"]),
                "drill_zone_total": float(row["drill_zone_total"]),
            }
            for _, row in merged.iterrows()
        ],
    }

    # 輸出 JSON
    out_path = DATA_DIR / "sorties_fishing_correlation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n   💾 結果已儲存: {out_path}")

    # 產生圖表
    print("\n[5/5] 產生圖表...")
    generate_charts(merged, output)

    # 摘要
    print("\n" + "=" * 60)
    print("分析完成 ✅")
    ne_corr = correlations.get("northeast_fishing", {})
    if ne_corr:
        r = ne_corr["pearson_r"]
        p = ne_corr["pearson_p"]
        sig = "顯著 ✅" if ne_corr["significant_pearson"] else "不顯著 ❌"
        print(f"  東北角漁船 vs 共機架次: r={r:.3f}, p={p:.3f} ({sig})")
    best_lag = lagged.get("northeast_fishing", {}).get("best_lag")
    if best_lag:
        print(f"  最佳滯後: {best_lag['lag']} 天 (r={best_lag['correlation']:.3f})")
    print(f"  高架次事件: {len(events)} 個 (門檻 > {HIGH_SORTIE_THRESHOLD})")
    print("=" * 60)


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# ====================================================
# AIS 本地排程腳本 (Bash 版)
# 用途：抓取 AIS 資料 → 分析 → 生成 Dashboard → Git push
# 可搭配 cron 每日自動執行，例如：
#   0 8 * * * /path/to/run_ais_local.sh >> /path/to/ais_cron.log 2>&1
# ====================================================

set -euo pipefail

# 切換到腳本所在目錄
cd "$(dirname "$0")"

NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "===================================================="
echo "  AIS 本地排程 - $NOW"
echo "===================================================="

# 1. 抓取 AIS 資料
echo ""
echo "[1/3] 抓取 AIS 資料..."
if ! python3 src/fetch_ais_data.py; then
    echo "[ERROR] fetch_ais_data.py 執行失敗"
    exit 1
fi

# 2. 分析可疑船隻
echo ""
echo "[2/3] 分析可疑船隻..."
if ! python3 src/analyze_suspicious.py; then
    echo "[WARN] analyze_suspicious.py 執行失敗，繼續執行..."
fi

# 3. 生成 Dashboard
echo ""
echo "[3/3] 生成 Dashboard..."
if ! python3 src/generate_dashboard.py; then
    echo "[ERROR] generate_dashboard.py 執行失敗"
    exit 1
fi

# 4. Git commit & push
echo ""
echo "[Git] 提交並推送..."
git add \
    data/ais_snapshot.json \
    data/ais_history.json \
    data/vessel_history.json \
    docs/data.json \
    docs/ais_history.json

if ! git diff --cached --quiet; then
    git commit -m "daily AIS snapshot $(date '+%Y-%m-%d')"
    git push
    echo "[Git] 已推送至遠端"
else
    echo "[Git] 無變更，跳過提交"
fi

echo ""
echo "===================================================="
echo "  完成 - $(date '+%H:%M:%S')"
echo "===================================================="

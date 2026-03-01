@echo off
chcp 65001 >nul
setlocal

:: ====================================================
:: AIS 本地排程腳本
:: 用途：抓取 AIS 資料 → 分析 → 生成 Dashboard → Git push
:: 可搭配 Windows Task Scheduler 每日自動執行
:: ====================================================

cd /d "%~dp0"

echo ====================================================
echo   AIS 本地排程 - %date% %time%
echo ====================================================

:: 1. 抓取 AIS 資料
echo.
echo [1/3] 抓取 AIS 資料...
python src\fetch_ais_data.py
if errorlevel 1 (
    echo [ERROR] fetch_ais_data.py 執行失敗
    goto :end
)

:: 2. 分析可疑船隻
echo.
echo [2/3] 分析可疑船隻...
python src\analyze_suspicious.py
if errorlevel 1 (
    echo [WARN] analyze_suspicious.py 執行失敗，繼續執行...
)

:: 3. 生成 Dashboard
echo.
echo [3/3] 生成 Dashboard...
python src\generate_dashboard.py
if errorlevel 1 (
    echo [ERROR] generate_dashboard.py 執行失敗
    goto :end
)

:: 4. Git commit & push
echo.
echo [Git] 提交並推送...
git add data\ais_snapshot.json data\ais_history.json data\vessel_history.json docs\data.json docs\ais_history.json
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily AIS snapshot %date%"
    git push
    echo [Git] 已推送至遠端
) else (
    echo [Git] 無變更，跳過提交
)

:end
echo.
echo ====================================================
echo   完成 - %time%
echo ====================================================
endlocal

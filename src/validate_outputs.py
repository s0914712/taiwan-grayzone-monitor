#!/usr/bin/env python3
"""管線輸出驗證 — 在 CI commit 前確認關鍵 JSON 檔案完整可用

用法:
    python src/validate_outputs.py            # 只檢查，失敗 exit 1
    python src/validate_outputs.py --restore  # 失敗的 git-tracked 檔案以
                                              # `git checkout --` 還原上次好版本
                                              # （仍 exit 1，讓 workflow 顯示失敗）

取代 workflow 內原本 inline 的「AIS 快照 0 艘船則還原」守衛，並擴大涵蓋
所有前端依賴的輸出檔。各 fetch 步驟保留 continue-on-error 容忍部分失敗，
本腳本是失敗時的響亮訊號。
"""
import json
import subprocess
import sys
from datetime import datetime, timezone

# AIS 快照最長容許「未更新」時數。update-ais.yml 日間每 30 分、夜間每 2 小時跑，
# 正常遠低於此；超過代表 AIS 抓取連續失敗。
# 為什麼要查新鮮度：`fetch_ais_data.save_all()` 在取得 0 艘船時會**保留舊快照**
# （避免清空有效資料），於是所有檔案都「非空且結構正確」，純結構驗證永遠通過。
# 2026-09 的代理黑名單事件就卡在這個盲點：ais_snapshot 停在 09-07，管線卻連續
# 十天回報 operational、每一輪都綠燈。
AIS_SNAPSHOT_MAX_AGE_HOURS = 24


def _age_hours(ts):
    """ISO 8601 字串距今幾小時；無法解析回傳 None。"""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def check_ais_snapshot(d):
    """結構 + 新鮮度；回傳 True 或失敗原因字串。"""
    if not isinstance(d, dict) or not d.get('vessels'):
        return False
    age = _age_hours(d.get('updated_at'))
    if age is None:
        return 'updated_at 缺漏或無法解析'
    if age > AIS_SNAPSHOT_MAX_AGE_HOURS:
        return (f'快照已 {age:.1f} 小時未更新（上限 {AIS_SNAPSHOT_MAX_AGE_HOURS}h）'
                f' — AIS 抓取連續失敗，先確認代理是否被封鎖'
                f'（見 fetch_ais_data.PROXY_SCHEMES）')
    return True


# (路徑, 檢查函式, 必要性) — required=False 只警告不導致失敗
# （部分檔案僅由 update-data.yml 低頻產生，update-ais.yml 執行時可能不存在）
# 檢查函式回傳 True/truthy 表示通過，回傳非空字串則作為失敗原因印出。
CHECKS = [
    ('docs/data.json',
     lambda d: isinstance(d, dict) and d.get('updated_at')
     and isinstance(d.get('ais_snapshot', {}).get('vessels'), list)
     and len(d['ais_snapshot']['vessels']) > 0,
     True),
    ('data/ais_snapshot.json', check_ais_snapshot, True),
    ('docs/ais_track_history.json',
     lambda d: isinstance(d, list) and len(d) > 0,
     True),
    ('docs/ais_track_animation.json',
     lambda d: isinstance(d, list) and len(d) > 0,
     False),
    ('data/ais_history.json',
     lambda d: isinstance(d, list) and len(d) > 0,
     True),
    ('data/suspicious_vessels.json',
     lambda d: isinstance(d, dict) and 'summary' in d,
     True),
    ('data/ship_transfers.json',
     lambda d: isinstance(d, dict),
     False),
    ('data/dark_vessels.json',
     lambda d: isinstance(d, dict),
     False),
    # 公務船編隊：僅 update-data.yml 產生，update-ais.yml 執行時不存在
    ('data/gov_formations.json',
     lambda d: isinstance(d, dict) and 'summary' in d
     and isinstance(d.get('vessel_index'), dict),
     False),
    # 高風險船累積檔：僅 update-data.yml 的 aggregate_highrisk 產生
    ('data/highrisk_accumulator.json',
     lambda d: isinstance(d, dict) and isinstance(d.get('daily'), dict),
     False),
]


def check_file(path, validator):
    """回傳 None 表示通過，否則回傳失敗原因字串"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return '檔案不存在'
    except (OSError, ValueError) as e:
        return f'無法解析: {e}'
    try:
        result = validator(data)
    except (TypeError, AttributeError, KeyError) as e:
        return f'結構檢查失敗: {e}'
    if isinstance(result, str) and result:
        return result
    if not result:
        return '內容不符預期（空檔或缺少必要欄位）'
    return None


def git_restore(path):
    """還原 git-tracked 檔案至上次 commit 版本；untracked 檔案無法還原"""
    r = subprocess.run(['git', 'checkout', '--', path],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"   ↩️ 已還原 {path} 至上次 commit 版本")
        return True
    print(f"   ⚠️ 無法還原 {path}: {r.stderr.strip()}")
    return False


def main():
    restore = '--restore' in sys.argv
    failures = []
    warnings = []

    print("🔍 驗證管線輸出檔案...")
    for path, validator, required in CHECKS:
        reason = check_file(path, validator)
        if reason is None:
            print(f"   ✅ {path}")
        elif required:
            print(f"   ❌ {path}: {reason}")
            failures.append(path)
        else:
            print(f"   ⚠️ {path}: {reason}（非必要，僅警告）")
            warnings.append(path)

    if failures and restore:
        print("\n♻️ --restore: 還原失敗檔案至上次好版本")
        for path in failures:
            git_restore(path)

    print(f"\n{'❌ 驗證失敗' if failures else '✅ 驗證通過'}"
          f"（必要檢查失敗 {len(failures)}，警告 {len(warnings)}）")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()

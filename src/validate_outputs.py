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

# (路徑, 檢查函式, 必要性) — required=False 只警告不導致失敗
# （部分檔案僅由 update-data.yml 低頻產生，update-ais.yml 執行時可能不存在）
CHECKS = [
    ('docs/data.json',
     lambda d: isinstance(d, dict) and d.get('updated_at')
     and isinstance(d.get('ais_snapshot', {}).get('vessels'), list)
     and len(d['ais_snapshot']['vessels']) > 0,
     True),
    ('data/ais_snapshot.json',
     lambda d: isinstance(d, dict) and len(d.get('vessels', [])) > 0,
     True),
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
        if not validator(data):
            return '內容不符預期（空檔或缺少必要欄位）'
    except (TypeError, AttributeError, KeyError) as e:
        return f'結構檢查失敗: {e}'
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

#!/usr/bin/env python3
"""
================================================================================
資料管線心跳 — Pipeline heartbeat (pipeline-heartbeat.yml)
================================================================================

GitHub 的 schedule 事件在高負載時會延遲甚至**直接丟棄**。2026-10-05 → 10-19
實測：update-ais.yml 243 個 cron 時段只觸發 76 次（31%），cloudflare-radar.yml
168 → 59（35%），沒有任何取消/失敗紀錄、排隊時間 0 —— 是排程根本沒觸發。
AIS 實際間隔中位數 4.4h（設計 1–2h），這是 SAR 比對的主要瓶頸。

解法：update-ais.yml 與 cloudflare-radar.yml 不再自己排程，改由這支每 20 分鐘的
心跳查 Actions 執行紀錄、在**逾期**時用 workflow_dispatch 派發（dispatch 不會被
丟，送出即排隊）。一條心跳照顧兩條 workflow：每天實際起約 23 個 ~15 秒的
小 job，不是每條 workflow 各自加閘門的兩倍。

逾期 = 距上次「真的有跑」的 run（排除 cancelled/skipped）超過間隔：
  update-ais.yml       白天（UTC 00–09，台灣 08–17）50 分、夜間 110 分
  cloudflare-radar.yml 110 分
用「上次執行」而不是「上次成功的快照」計時：代理斷線時 AIS 的 run 仍會跑完，
不會變成每 20 分鐘重試一次。

**絕不擠掉別人**：data-pipeline 併發群組最多只保留一個等待中的 run，新排進來
的會把舊的取消掉（跨 workflow）。所以：
  - 群組裡已有 queued/waiting 的 run（例如排在後面的 update-data.yml）→ 不派發
  - 群組閒置 → 最多派發 2 條（一條直接跑、一條排隊）
  - 群組正在跑、沒人等待 → 最多派發 1 條（當唯一的等待者）
多條同時逾期時，先派「逾期比例」（已過時間 / 間隔）最高的。

查詢失敗一律不派發（exit 1 讓 job 變紅，看得到）。心跳本身停擺時，
update-data.yml 的 validate_outputs（AIS 快照 >24h 判失敗）會報警。
================================================================================
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

MANAGED = (
    # (workflow 檔名, 白天間隔分, 夜間間隔分)
    ('update-ais.yml', 50, 110),
    ('cloudflare-radar.yml', 110, 110),
)
# 共用 data-pipeline 併發群組的全部 workflow（判斷群組是否閒置用）
COASTGUARD = ('track-coastguard.yml', 360, 360)
GROUP_WORKFLOWS = ('update-ais.yml', 'cloudflare-radar.yml', 'update-data.yml', COASTGUARD[0])
DAY_HOURS_UTC = range(0, 9)

WAITING_STATUSES = {'queued', 'waiting', 'requested', 'pending'}
RUNNING_STATUSES = {'in_progress'}
NOT_EXECUTED = {'cancelled', 'skipped'}
RUNS_PER_QUERY = 10


def parse_iso(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def interval_min(day_min, night_min, now):
    return day_min if now.hour in DAY_HOURS_UTC else night_min


def last_executed_at(runs):
    """最近一次真的有跑（或正在跑/排隊）的 run 的建立時間；沒有回傳 None。"""
    times = []
    for r in runs:
        if r.get('status') == 'completed' and r.get('conclusion') in NOT_EXECUTED:
            continue
        t = parse_iso(r.get('createdAt'))
        if t is not None:
            times.append(t)
    return max(times) if times else None


def plan_dispatch(now, runs_by_wf, managed=MANAGED, group=GROUP_WORKFLOWS):
    """回傳 (要派發的 workflow 清單, 理由清單)。純函式。"""
    reasons = []
    statuses = [r.get('status') for wf in group for r in runs_by_wf.get(wf, ())]
    if any(s in WAITING_STATUSES for s in statuses):
        return [], ['data-pipeline 群組已有等待中的 run，不派發（避免把它擠掉）']
    busy = any(s in RUNNING_STATUSES for s in statuses)
    slots = 1 if busy else 2

    due = []
    for wf, day_min, night_min in managed:
        limit = interval_min(day_min, night_min, now)
        last = last_executed_at(runs_by_wf.get(wf, ()))
        if last is None:
            due.append((float('inf'), wf))
            reasons.append(f'{wf}: 查無執行紀錄 → 逾期')
            continue
        age = (now - last).total_seconds() / 60.0
        if age >= limit:
            due.append((age / limit, wf))
            reasons.append(f'{wf}: 上次 {age:.0f} 分前 ≥ {limit} 分 → 逾期')
        else:
            reasons.append(f'{wf}: 上次 {age:.0f} 分前 < {limit} 分 → 不用跑')
    due.sort(reverse=True)
    chosen = [wf for _, wf in due[:slots]]
    if len(due) > slots:
        reasons.append(f'群組{"忙碌" if busy else "閒置"}，本次只派 {slots} 條：{", ".join(chosen)}')
    return chosen, reasons


# =============================================================================
# gh CLI（runner 內建；GH_TOKEN / GH_REPO 由 workflow 提供）
# =============================================================================

def gh(*args):
    return subprocess.run(['gh', *args], check=True, capture_output=True,
                          text=True).stdout


def fetch_runs(workflow):
    out = gh('run', 'list', '--workflow', workflow, '--limit', str(RUNS_PER_QUERY),
             '--json', 'createdAt,status,conclusion')
    return json.loads(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description='data-pipeline 心跳：逾期時派發 workflow')
    ap.add_argument('--ref', default=os.environ.get('GITHUB_REF_NAME', 'main'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    try:
        runs_by_wf = {wf: fetch_runs(wf) for wf in GROUP_WORKFLOWS}
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        detail = getattr(e, 'stderr', '') or e
        print(f'❌ 查詢執行紀錄失敗，本次不派發：{detail}', file=sys.stderr)
        return 1

    managed = MANAGED
    if os.environ.get('MST_TRACKING_ENABLED', '').lower() == 'true':
        managed = MANAGED + (COASTGUARD,)
    chosen, reasons = plan_dispatch(datetime.now(timezone.utc), runs_by_wf, managed=managed)
    for r in reasons:
        print(f'  {r}')
    for wf in chosen:
        if args.dry_run:
            print(f'🧪 [dry-run] 會派發 {wf}（ref={args.ref}）')
            continue
        inputs = ('-f', 'dry_run=false') if wf == COASTGUARD[0] else ()
        gh('workflow', 'run', wf, '--ref', args.ref, *inputs)
        print(f'🚀 已派發 {wf}（ref={args.ref}）')
    if not chosen:
        print('💤 本次不派發')
    return 0


if __name__ == '__main__':
    sys.exit(main())


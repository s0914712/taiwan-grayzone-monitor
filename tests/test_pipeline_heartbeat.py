"""Tests for src/pipeline_heartbeat.py — data-pipeline 心跳的派發規則。"""
import subprocess
from datetime import datetime, timedelta, timezone

import pipeline_heartbeat as hb


def at(h, m=0):
    return datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)


def run(t, status='completed', conclusion='success'):
    return {'createdAt': t.isoformat().replace('+00:00', 'Z'),
            'status': status, 'conclusion': conclusion}


def runs(ais_ago=None, radar_ago=None, data=(), now=at(3)):
    out = {'update-ais.yml': [], 'cloudflare-radar.yml': [], 'update-data.yml': list(data)}
    if ais_ago is not None:
        out['update-ais.yml'].append(run(now - timedelta(minutes=ais_ago)))
    if radar_ago is not None:
        out['cloudflare-radar.yml'].append(run(now - timedelta(minutes=radar_ago)))
    return out


def test_nothing_due():
    chosen, _ = hb.plan_dispatch(at(3), runs(ais_ago=20, radar_ago=30))
    assert chosen == []


def test_ais_daytime_hourly_night_two_hourly():
    assert hb.plan_dispatch(at(3), runs(ais_ago=55, radar_ago=10))[0] == ['update-ais.yml']
    night = at(15)
    assert hb.plan_dispatch(night, runs(ais_ago=80, radar_ago=10, now=night))[0] == []
    assert hb.plan_dispatch(night, runs(ais_ago=115, radar_ago=10, now=night))[0] == ['update-ais.yml']


def test_idle_group_dispatches_both_most_overdue_first():
    chosen, _ = hb.plan_dispatch(at(3), runs(ais_ago=60, radar_ago=300))
    assert chosen == ['cloudflare-radar.yml', 'update-ais.yml']   # 300/110 > 60/50


def test_busy_group_dispatches_only_one():
    data = [run(at(3) - timedelta(minutes=2), status='in_progress', conclusion=None)]
    chosen, _ = hb.plan_dispatch(at(3), runs(ais_ago=60, radar_ago=300, data=data))
    assert chosen == ['cloudflare-radar.yml']


def test_never_dispatch_when_something_is_waiting():
    """群組只保留一個等待者，新的會把舊的擠掉 —— 排隊中的 update-data 絕不能被擠掉。"""
    data = [run(at(3) - timedelta(minutes=1), status='queued', conclusion=None)]
    chosen, reasons = hb.plan_dispatch(at(3), runs(ais_ago=500, radar_ago=500, data=data))
    assert chosen == [] and '等待中' in reasons[0]


def test_cancelled_runs_do_not_count_as_executed():
    now = at(3)
    r = runs(radar_ago=10, now=now)
    r['update-ais.yml'] = [run(now - timedelta(minutes=5), conclusion='cancelled'),
                           run(now - timedelta(minutes=90))]
    assert hb.plan_dispatch(now, r)[0] == ['update-ais.yml']


def test_running_ais_is_not_redispatched():
    now = at(3)
    r = runs(radar_ago=10, now=now)
    r['update-ais.yml'] = [run(now - timedelta(minutes=3), status='in_progress', conclusion=None),
                           run(now - timedelta(minutes=200))]
    assert hb.plan_dispatch(now, r)[0] == []


def test_no_history_counts_as_overdue():
    assert 'update-ais.yml' in hb.plan_dispatch(at(3), runs(radar_ago=10))[0]


def test_healthy_github_keeps_original_cadence():
    """心跳每次都觸發成功時，AIS 一天約 17 次（原排程：白天每小時 9 次 + 夜間每 2h 8 次）。"""
    t = at(0, 7) - timedelta(days=1)
    history = {'update-ais.yml': [], 'cloudflare-radar.yml': [], 'update-data.yml': []}
    counts = {'update-ais.yml': 0, 'cloudflare-radar.yml': 0}
    for _ in range(72):
        for wf in hb.plan_dispatch(t, history)[0]:
            history[wf] = [run(t)]
            counts[wf] += 1
        t += timedelta(minutes=20)
    assert 15 <= counts['update-ais.yml'] <= 19
    assert 10 <= counts['cloudflare-radar.yml'] <= 13      # 原排程每 2h = 12


def test_main_does_not_dispatch_when_query_fails(monkeypatch, capsys):
    calls = []

    def boom(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, 'gh', stderr='HTTP 403')
    monkeypatch.setattr(hb, 'gh', boom)
    assert hb.main(['--ref', 'main']) == 1
    assert all(a[0] == 'run' and a[1] == 'list' for a in calls)   # 沒有呼叫 workflow run


def test_main_dispatches_due_workflows(monkeypatch):
    sent = []

    def fake_gh(*args):
        if args[:2] == ('run', 'list'):
            return '[]'
        sent.append(args)
        return ''
    monkeypatch.setattr(hb, 'gh', fake_gh)
    assert hb.main(['--ref', 'main']) == 0
    assert sorted(a[2] for a in sent) == ['cloudflare-radar.yml', 'update-ais.yml']
    assert all(a[:2] == ('workflow', 'run') and a[-2:] == ('--ref', 'main') for a in sent)


def test_dry_run_sends_nothing(monkeypatch):
    sent = []

    def fake_gh(*args):
        if args[:2] == ('run', 'list'):
            return '[]'
        sent.append(args)
        return ''
    monkeypatch.setattr(hb, 'gh', fake_gh)
    hb.main(['--dry-run'])
    assert sent == []


def test_coastguard_due_at_six_hours_not_before():
    now = at(15)
    r = runs(ais_ago=10, radar_ago=10, now=now)
    managed = hb.MANAGED + (hb.COASTGUARD,)
    r[hb.COASTGUARD[0]] = [run(now - timedelta(minutes=359))]
    assert hb.plan_dispatch(now, r, managed=managed)[0] == []
    r[hb.COASTGUARD[0]] = [run(now - timedelta(minutes=360))]
    assert hb.plan_dispatch(now, r, managed=managed)[0] == [hb.COASTGUARD[0]]


def test_queued_coastguard_does_not_get_displaced():
    now = at(3)
    r = runs(ais_ago=500, radar_ago=500, now=now)
    r[hb.COASTGUARD[0]] = [run(now, status='queued', conclusion=None)]
    assert hb.plan_dispatch(now, r)[0] == []


def test_enabled_coastguard_dispatch_is_live_but_not_enabled_by_default(monkeypatch):
    monkeypatch.setenv('MST_TRACKING_ENABLED', 'true')
    sent = []

    def fake_gh(*args):
        if args[:2] == ('run', 'list'):
            workflow = args[args.index('--workflow') + 1]
            if workflow != hb.COASTGUARD[0]:
                return '[{"createdAt":"2099-01-01T00:00:00Z","status":"completed","conclusion":"success"}]'
            return '[]'
        sent.append(args)
        return ''

    monkeypatch.setattr(hb, 'gh', fake_gh)
    assert hb.main(['--ref', 'main']) == 0
    assert sent == [('workflow', 'run', hb.COASTGUARD[0], '--ref', 'main', '-f', 'dry_run=false')]


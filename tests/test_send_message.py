"""LINE 推送 — SendMessage.py 的週/月報路徑

只測純函式（期別解析、報表載入、訊息組裝）；不觸網、不畫圖。
SendMessage 會在 import 時載入 publish_threads / gov_daily_activity 等模組，
但那些都只是 import，不會發出請求。
"""
import json
from datetime import date

import pytest

import SendMessage as sm


# ══════════════════════════════════════════════════════════════════
# 期別解析與報表載入
# ══════════════════════════════════════════════════════════════════

def test_resolve_period_label_weekly_is_previous_complete_week():
    # 2026-08-31 是週一 → 上一個完整 ISO 週是 W35（8/24–8/30）
    assert sm.resolve_period_label('weekly', date(2026, 8, 31)) == '2026-W35'


def test_resolve_period_label_monthly_is_previous_month():
    assert sm.resolve_period_label('monthly', date(2026, 9, 1)) == '2026-08'
    assert sm.resolve_period_label('monthly', date(2026, 1, 2)) == '2025-12'


def _write_report(tmp_path, kind, label, payload):
    d = tmp_path / 'reports' / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{label}.json').write_text(json.dumps(payload), encoding='utf-8')


def test_load_period_report_reads_file(tmp_path):
    _write_report(tmp_path, 'weekly', '2026-W35',
                  {'week': '2026-W35', 'summary': {'unique_highrisk': 7}})
    label, report = sm.load_period_report('weekly', today=date(2026, 8, 31),
                                          base_dir=tmp_path)
    assert label == '2026-W35'
    assert report['summary']['unique_highrisk'] == 7


def test_load_period_report_missing_returns_none(tmp_path):
    """冷啟動：管線還沒產出報表 → 回 None，呼叫端不推空訊息。"""
    label, report = sm.load_period_report('weekly', today=date(2026, 8, 31),
                                          base_dir=tmp_path)
    assert label == '2026-W35' and report is None


def test_load_period_report_corrupt_json(tmp_path, capsys):
    d = tmp_path / 'reports' / 'monthly'
    d.mkdir(parents=True)
    (d / '2026-08.json').write_text('{ not json', encoding='utf-8')
    _, report = sm.load_period_report('monthly', today=date(2026, 9, 1),
                                      base_dir=tmp_path)
    assert report is None
    assert '失敗' in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════
# 訊息組裝
# ══════════════════════════════════════════════════════════════════

REPORT = {
    'period': 'weekly', 'week': '2026-W35',
    'start': '2026-08-24', 'end': '2026-08-30',
    'days_covered': 7, 'daily_cap': 400,
    'summary': {
        'unique_highrisk': 400, 'critical': 113, 'high': 287,
        'cable_loiter_vessels': 303, 'cable_loiter_hours_total': 1290.9,
        'offshore_loiter_vessels': 40,
        'by_type': {'cargo': 179}, 'by_flag': {}, 'daily_counts': {},
    },
    'hotspots': [
        {'lat': 24.4, 'lon': 118.4, 'loiter_hours': 258.2, 'vessels': 16,
         'avg_speed_kn': 0.5, 'events': 17},
        {'lat': 23.0, 'lon': 120.2, 'loiter_hours': 81.8, 'vessels': 4,
         'avg_speed_kn': None, 'events': 3},
    ],
    'vessels': [
        {'mmsi': '413000000', 'name': 'TEST CARGO', 'vessel_type': 'cargo',
         'flag_zh': '中國', 'max_risk_score': 25, 'cable_loiter_hours': 5.8,
         'cable_loiter_avg_speed_kn': 1.7},
        {'mmsi': '412000001', 'name': '', 'vessel_type': 'fishing',
         'flag_zh': '中國', 'max_risk_score': 9, 'cable_loiter_hours': 0,
         'cable_loiter_avg_speed_kn': None},
    ],
}


def test_build_period_template_weekly_content():
    t = sm.build_period_template(REPORT, 'weekly')
    assert '週報 — 2026-W35' in t
    assert '2026-08-24 ~ 2026-08-30' in t
    assert '400 艘' in t and 'critical 113' in t
    assert '303 艘' in t and '1290.9 小時' in t
    assert '離岸長期徘徊商船 40 艘' in t
    assert '24.4N 118.4E' in t
    assert sm.WEEKLY_PAGE_URL in t.splitlines()[-1]


def test_build_period_template_null_speed_is_dash_not_zero():
    """均速為 None（該格無可用 SOG）必須顯示破折號 —— 顯示 0 是謊報靜止。"""
    t = sm.build_period_template(REPORT, 'weekly')
    assert '81.8h／4 艘／均速 —' in t
    assert '均速 0 kn' not in t


def test_build_period_template_unnamed_vessel_falls_back_to_mmsi():
    t = sm.build_period_template(REPORT, 'weekly')
    assert 'MMSI 412000001' in t


def test_build_period_template_monthly_label():
    monthly = dict(REPORT, week=None, month='2026-08', days_covered=28)
    monthly.pop('week')
    t = sm.build_period_template(monthly, 'monthly')
    assert '月報 — 2026-08' in t


def test_build_period_template_partial_period_warning():
    partial = dict(REPORT, days_covered=1)
    t = sm.build_period_template(partial, 'weekly')
    assert '僅累積 1 天' in t
    full = dict(REPORT, days_covered=7)
    assert '僅累積' not in sm.build_period_template(full, 'weekly')


def test_build_period_template_cap_note_only_when_capped():
    """逐船明細達每日上限時要標明非全量，否則讀者會以為那就是全部。"""
    assert '取前 400 艘' in sm.build_period_template(REPORT, 'weekly')
    small = dict(REPORT, summary=dict(REPORT['summary'], unique_highrisk=12))
    assert '取前 400 艘' not in sm.build_period_template(small, 'weekly')


def test_build_period_template_no_hotspots():
    empty = dict(REPORT, hotspots=[])
    t = sm.build_period_template(empty, 'weekly')
    assert '徘徊熱區' not in t
    assert sm.WEEKLY_PAGE_URL in t


def test_compose_period_report_truncates_and_keeps_url(monkeypatch):
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: 'x' * (sm.LINE_MAX_CHARS + 500))
    text = sm.compose_period_report(REPORT, 'weekly')
    assert len(text) <= sm.LINE_MAX_CHARS


def test_compose_period_report_appends_url_when_llm_omits_it(monkeypatch):
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: '短訊息，沒有連結')
    text = sm.compose_period_report(REPORT, 'weekly')
    assert text.splitlines()[-1] == sm.WEEKLY_PAGE_URL


def test_compose_period_report_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: None)
    assert '週報 — 2026-W35' in sm.compose_period_report(REPORT, 'weekly')


def test_fmt_speed():
    assert sm._fmt_speed(None) == '—'
    assert sm._fmt_speed(1.5) == '1.5 kn'
    assert sm._fmt_speed(3.0) == '3 kn'


# ══════════════════════════════════════════════════════════════════
# 推送流程的降級路徑
# ══════════════════════════════════════════════════════════════════

def test_run_period_push_skips_when_report_missing(monkeypatch, capsys):
    """報表不存在時不得推送，且要正常結束（exit 0）—— 冷啟動的正常狀態。"""
    monkeypatch.setattr(sm, 'load_period_report',
                        lambda mode, **kw: ('2026-W35', None))
    called = []
    monkeypatch.setattr(sm, 'push_to_line',
                        lambda *a, **k: called.append(a) or True)
    assert sm.run_period_push('weekly') == 0
    assert called == []
    assert '略過推送' in capsys.readouterr().out


def test_run_period_push_dry_run_does_not_push(monkeypatch):
    monkeypatch.setattr(sm, 'load_period_report',
                        lambda mode, **kw: ('2026-W35', REPORT))
    monkeypatch.setattr(sm, 'render_period_images', lambda report, mode: [])
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: None)
    called = []
    monkeypatch.setattr(sm, 'push_to_line',
                        lambda *a, **k: called.append(a) or True)
    assert sm.run_period_push('weekly', dry_run=True) == 0
    assert called == []


def test_run_period_push_sends_text_plus_images(monkeypatch):
    monkeypatch.setattr(sm, 'load_period_report',
                        lambda mode, **kw: ('2026-W35', REPORT))
    monkeypatch.setattr(sm, 'render_period_images',
                        lambda report, mode: [('/a.png', 'data/charts/a.png'),
                                              ('/b.png', 'data/charts/b.png')])
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: None)
    monkeypatch.setattr(sm, 'upload_charts_to_github',
                        lambda pending, token: ['https://x/a.png',
                                                'https://x/b.png'])
    monkeypatch.setattr(sm, '_get_env', lambda *names: 'stub-token')
    sent = {}
    monkeypatch.setattr(sm, 'push_to_line',
                        lambda messages, t, u: sent.update(m=messages) or True)
    assert sm.run_period_push('weekly') == 0
    msgs = sent['m']
    assert msgs[0]['type'] == 'text'
    assert [m['type'] for m in msgs[1:]] == ['image', 'image']
    assert msgs[1]['originalContentUrl'] == msgs[1]['previewImageUrl']


def test_run_period_push_text_only_without_github_token(monkeypatch):
    """沒有 GITHUB_TOKEN 就上傳不了圖，仍要把文字推出去。"""
    monkeypatch.setattr(sm, 'load_period_report',
                        lambda mode, **kw: ('2026-W35', REPORT))
    monkeypatch.setattr(sm, 'render_period_images',
                        lambda report, mode: [('/a.png', 'data/charts/a.png')])
    monkeypatch.setattr(sm, 'generate_llm_period_report',
                        lambda report, mode: None)

    def fake_env(*names):
        return '' if 'GITHUB_TOKEN' in names else 'stub'
    monkeypatch.setattr(sm, '_get_env', fake_env)
    sent = {}
    monkeypatch.setattr(sm, 'push_to_line',
                        lambda messages, t, u: sent.update(m=messages) or True)
    assert sm.run_period_push('weekly') == 0
    assert len(sent['m']) == 1 and sent['m'][0]['type'] == 'text'


def test_run_period_push_image_cap(monkeypatch):
    """上傳回來的 URL 多於 LINE 上限時只取前 N 張。"""
    monkeypatch.setattr(sm, 'load_period_report',
                        lambda mode, **kw: ('2026-W35', REPORT))
    monkeypatch.setattr(sm, 'render_period_images', lambda report, mode: [('x', 'y')])
    monkeypatch.setattr(sm, 'generate_llm_period_report', lambda report, mode: None)
    monkeypatch.setattr(sm, 'upload_charts_to_github',
                        lambda pending, token: [f'https://x/{i}.png'
                                                for i in range(9)])
    monkeypatch.setattr(sm, '_get_env', lambda *names: 'stub')
    sent = {}
    monkeypatch.setattr(sm, 'push_to_line',
                        lambda messages, t, u: sent.update(m=messages) or True)
    sm.run_period_push('weekly')
    assert len(sent['m']) == 1 + sm.LINE_MAX_IMAGES

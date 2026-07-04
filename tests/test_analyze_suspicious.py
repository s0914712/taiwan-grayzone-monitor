"""威脅評分引擎誤判修正的回歸測試 — analyze_suspicious.py

涵蓋：
  * 港內排除：靠泊/錨泊在港（海纜登陸點附近）不觸發海纜鄰近/徘徊/緩衝帶加分
  * 徘徊速度門檻：>5kn 過境不算徘徊；<5kn 連續 3h+ 才算
  * 徘徊連續性：中途離開（時間戳斷開）不能累計成長徘徊
  * Z 字型：錨泊擺動不算轉向；高速轉向不吃「拖錨」組合加分
  * 圓形偽訊號：錨泊迴旋（小半徑低速）與港內軌跡排除；真圓形仍偵測
  * 不可能物理：2h 快照不比對速度/航向不符；瞬移 + 船名交替 = MMSI 共用
  * UN 制裁：IMO 匹配 +8、純船名匹配 +4
"""
import math

import pytest

import analyze_suspicious as asus


# ── 測試用海纜：一條經過高雄港外的南北向線段 ──────────────────────────
# 高雄港座標 (22.6153, 120.2664)；線段緊貼港口（模擬海纜登陸段）
FAKE_CABLE = [{
    'slug': 'test-cable',
    'points': [(22.0, 120.27), (23.0, 120.27)],
    'bbox': (22.0, 120.27, 23.0, 120.27),
}]

# 遠離所有港口與海纜的開放海域
OPEN_LAT, OPEN_LON = 23.8, 122.5


@pytest.fixture(autouse=True)
def fake_cables(monkeypatch):
    """所有測試使用固定的測試海纜，避免依賴 data/cable-geo.json。"""
    monkeypatch.setattr(asus, '_cable_segments', FAKE_CABLE)
    yield


def make_track(positions, start_hour=0, step_hours=2.0, speed=3.0, anc=False,
               name='TEST SHIP'):
    """由 (lat, lon) 序列建立航跡點，時間間隔 step_hours。"""
    pts = []
    for i, (lat, lon) in enumerate(positions):
        t_hours = start_hour + i * step_hours
        day = 1 + int(t_hours // 24)
        hh = t_hours % 24
        mm = int((hh % 1) * 60)
        pt = {
            't': f'2026-07-{day:02d}T{int(hh):02d}:{mm:02d}:00+00:00',
            'lat': lat, 'lon': lon,
            'speed': speed, 'heading': 0.0, 'name': name,
        }
        if anc:
            pt['anc'] = 1
        pts.append(pt)
    return pts


# =========================================================================
# 港內排除
# =========================================================================

def test_moored_in_port_no_cable_proximity_or_loiter():
    """靠泊高雄港（緊鄰測試海纜）整天 → 港內點全排除，不觸發鄰近/徘徊。"""
    pts = make_track([(22.6153, 120.2664)] * 12, speed=0.1, anc=True)
    asus.annotate_port_points(pts)
    assert all(p.get('in_port') for p in pts)
    is_near, details = asus.check_cable_proximity(pts)
    assert is_near is False
    assert details == {}


def test_out_of_port_near_cable_still_detected():
    """開放海域緊貼海纜 → 鄰近偵測不受港內排除影響。"""
    pts = make_track([(22.8, 120.28)] * 3, speed=6.0)
    asus.annotate_port_points(pts)
    assert not any(p.get('in_port') for p in pts)
    is_near, details = asus.check_cable_proximity(pts)
    assert is_near is True
    assert 'test-cable' in details['cables_nearby']


def test_classify_moored_cargo_in_port_not_suspicious():
    """整條航跡靠泊台灣港內的貨船 — 直接被「停泊台灣港內」規則排除。"""
    profile = {'mmsi': '412000001', 'names_seen': ['PORT SHIP'],
               'types_seen': ['cargo'], 'total_snapshots': 12}
    pts = make_track([(22.6153, 120.2664)] * 12, speed=0.2, anc=True)
    result = asus.classify_vessel(profile, pts)
    assert result['excluded'] is True
    assert result['suspicious'] is False


def test_in_port_scoring_suppressed_for_analyzed_vessel():
    """有身分變更事件的靠港船（安全閥 → 照常分析）：港內點計分抑制仍生效，
    不因靠泊台灣港（緊鄰海纜登陸段）而觸發鄰近/徘徊/緩衝帶加分。"""
    profile = {'mmsi': '412000008', 'names_seen': ['PORT SHIP B'],
               'types_seen': ['cargo'], 'total_snapshots': 12}
    events = [{'mmsi': '412000008', 'timestamp': '2026-07-01T00:00:00+00:00',
               'changes': [{'field': 'name', 'old': 'X', 'new': 'PORT SHIP B'}]}]
    pts = make_track([(22.6153, 120.2664)] * 12, speed=0.2, anc=True)
    result = asus.classify_vessel(profile, pts, identity_events=events)
    assert result['excluded'] is False
    assert result['cable_proximity'] is False
    assert result['cable_loitering'] is False
    assert result['cable_buffer_1km'] is False
    assert result['cable_buffer_jurisdiction'] is False


# =========================================================================
# 徘徊：速度門檻 + 連續性
# =========================================================================

def _near_cable_track(hours, speed, gap_at=None, gap_hours=24.0):
    """沿海纜（開放海域段）緩慢移動 hours 小時的航跡；
    gap_at 指定在第幾點後插入 gap_hours 的中斷。"""
    positions = []
    n = int(hours / 2) + 1
    for i in range(n):
        positions.append((22.8 + i * 0.001, 120.272))
    pts = make_track(positions, speed=speed)
    if gap_at is not None:
        shift = gap_hours
        for p in pts[gap_at:]:
            # 手動平移時間戳
            hh = int(p['t'][11:13]) + int(shift)
            day = 1 + hh // 24
            p['t'] = f'2026-07-{day:02d}T{hh % 24:02d}:00:00+00:00'
    return pts


def test_transit_above_5kn_not_loitering():
    """7.5 節沿海纜過境 6 小時 — 修正前（<8kn）誤判徘徊，修正後不觸發。"""
    pts = _near_cable_track(hours=6, speed=7.5)
    is_near, details = asus.check_cable_proximity(pts)
    assert is_near is True                      # 鄰近仍成立
    assert details['loiter_triggered'] is False  # 但不是徘徊


def test_slow_near_cable_is_loitering():
    """2 節在海纜附近連續 6 小時 → 徘徊。"""
    pts = _near_cable_track(hours=6, speed=2.0)
    is_near, details = asus.check_cable_proximity(pts)
    assert is_near is True
    assert details['loiter_triggered'] is True
    assert details['loiter_slow_hours'] >= asus.CABLE_LOITER_HOURS


def test_loiter_broken_by_gap_not_accumulated():
    """兩段各 2 小時的慢速出現、中間隔 24 小時 —
    修正前用首末時間差會算成 28h 徘徊，修正後每段皆 < 3h → 不觸發。"""
    pts = _near_cable_track(hours=4, speed=2.0, gap_at=2, gap_hours=24.0)
    is_near, details = asus.check_cable_proximity(pts)
    assert is_near is True
    assert details['loiter_triggered'] is False
    assert details['loiter_slow_hours'] < asus.CABLE_LOITER_HOURS


# =========================================================================
# Z 字型：錨泊擺動 + 拖錨速度 gate
# =========================================================================

def test_anchored_swing_not_zigzag():
    """錨泊船隨潮流擺動（anc=1、低速、方位隨機漂移 >100m）不算 Z 字型。"""
    import random
    rng = random.Random(42)
    lat, lon = OPEN_LAT, OPEN_LON
    positions = []
    for _ in range(12):
        lat += rng.uniform(-0.003, 0.003)
        lon += rng.uniform(-0.003, 0.003)
        positions.append((lat, lon))
    pts = make_track(positions, speed=0.4, anc=True)
    is_zigzag, _ = asus.check_zigzag_pattern(pts)
    assert is_zigzag is False


def test_real_zigzag_still_detected_and_speed_tracked():
    """真實低速 Z 字航跡仍偵測，且 turns_below_drag_speed 正確統計。"""
    # 東北-西南交替折返（每段 ~1.5km，方位變化 ~90°）
    positions = []
    lat, lon = OPEN_LAT, OPEN_LON
    for i in range(10):
        if i % 2 == 0:
            lat += 0.012
        else:
            lon += 0.012
        positions.append((lat, lon))
    pts = make_track(positions, speed=4.0)
    is_zigzag, details = asus.check_zigzag_pattern(pts)
    assert is_zigzag is True
    assert details['turns_below_drag_speed'] >= asus.ZIGZAG_MIN_TURNS


def test_high_speed_zigzag_no_anchor_drag_combo():
    """12 節高速 Z 字（漁撈/操船）＋海纜鄰近 → zigzag 成立但不吃拖錨組合 +3。"""
    positions = []
    lat, lon = 22.8, 120.272  # 測試海纜旁
    for i in range(10):
        if i % 2 == 0:
            lat += 0.012
        else:
            lon += 0.012
        positions.append((lat, lon))
    profile = {'mmsi': '412000002', 'names_seen': ['FAST ZZ'],
               'types_seen': ['cargo'], 'total_snapshots': 10}

    fast = asus.classify_vessel(profile, make_track(positions, speed=12.0))
    # 6kn：低於拖錨上限 7kn、但高於徘徊門檻 5kn — 隔離拖錨組合變因
    slow = asus.classify_vessel(profile, make_track(positions, speed=6.0))

    assert fast['zigzag_pattern'] and slow['zigzag_pattern']
    assert fast['cable_proximity'] and slow['cable_proximity']
    assert not slow['cable_loitering']
    # 低速版本比高速版本多拿拖錨組合 +3（raw score 差距 = 3）
    assert slow['raw_score'] - fast['raw_score'] == 3


# =========================================================================
# 圓形偽訊號：錨泊迴旋 / 港內 / 真圓形
# =========================================================================

def _circle_positions(clat, clon, radius_km, n=12):
    positions = []
    for i in range(n):
        ang = 2 * math.pi * i / n
        dlat = (radius_km / 111.0) * math.cos(ang)
        dlon = (radius_km / (111.0 * math.cos(math.radians(clat)))) * math.sin(ang)
        positions.append((clat + dlat, clon + dlon))
    return positions


def test_anchor_swing_circle_suppressed():
    """半徑 0.3km 的低速迴旋圈（錨泊擺動）→ 不算偽訊號。"""
    pts = make_track(_circle_positions(OPEN_LAT, OPEN_LON, 0.3), speed=0.8, anc=True)
    is_circle, details = asus.check_circle_pattern(pts)
    assert is_circle is False
    assert details.get('skipped_reason') == 'anchor_swing'


def test_in_port_circle_suppressed():
    """港內圓形軌跡（港區 GPS 干擾）→ 不算偽訊號。"""
    pts = make_track(_circle_positions(22.6153, 120.2664, 0.8), speed=5.0)
    is_circle, details = asus.check_circle_pattern(pts)
    assert is_circle is False
    assert details.get('skipped_reason', '').startswith('in_port')


def test_genuine_circle_still_detected():
    """開放海域、正常航速的標準圓 → 仍偵測為偽訊號。"""
    pts = make_track(_circle_positions(OPEN_LAT, OPEN_LON, 2.0), speed=5.0)
    is_circle, details = asus.check_circle_pattern(pts)
    assert is_circle is True
    assert 'skipped_reason' not in details


# =========================================================================
# 不可能物理：dt gate + MMSI 共用
# =========================================================================

def test_speed_mismatch_not_checked_at_2h_cadence():
    """2h 快照：回報 20kn、實際平均 3kn（中途停船）→ 不算速度不符。"""
    pts = make_track([(OPEN_LAT, OPEN_LON + i * 0.05) for i in range(6)],
                     speed=20.0, step_hours=2.0)  # 0.05° ≈ 5.1km / 2h ≈ 2.5km/h
    is_susp, details = asus.check_impossible_physics(pts)
    assert details['speed_mismatch_count'] == 0
    assert is_susp is False


def test_speed_mismatch_checked_at_dense_cadence():
    """0.5h 取樣：回報 3kn 但實際 ~10 倍 → 速度不符成立。"""
    pts = make_track([(OPEN_LAT, OPEN_LON + i * 0.25) for i in range(6)],
                     speed=3.0, step_hours=0.5)  # 0.25° ≈ 25km / 0.5h = 50km/h ≈ 27kn
    is_susp, details = asus.check_impossible_physics(pts)
    assert details['speed_mismatch_count'] >= 2
    assert is_susp is True


def test_teleport_with_alternating_names_is_mmsi_collision():
    """兩艘船共用 MMSI（名稱交替、位置跳 200km+）→ 記 mmsi_collision，不算瞬移。"""
    pts = []
    for i in range(6):
        lat, lon = (23.0, 118.0) if i % 2 == 0 else (25.0, 122.0)
        name = 'SHIP A' if i % 2 == 0 else 'SHIP B'
        pts.extend(make_track([(lat, lon)], start_hour=i * 2, speed=5.0, name=name))
    is_susp, details = asus.check_impossible_physics(pts)
    assert details['teleport_count'] == 0
    assert details['mmsi_collision_count'] >= 2
    assert is_susp is False


def test_teleport_same_name_still_detected():
    """同名船瞬移 → 仍判偽訊號。"""
    pts = []
    for i in range(6):
        lat, lon = (23.0, 118.0) if i % 2 == 0 else (25.0, 122.0)
        pts.extend(make_track([(lat, lon)], start_hour=i * 2, speed=5.0))
    is_susp, details = asus.check_impossible_physics(pts)
    assert details['teleport_count'] >= 2
    assert is_susp is True


# =========================================================================
# UN 制裁：IMO vs 純船名
# =========================================================================

def _sanction_entry(matched_by):
    return {'imo': '9999999', 'name': 'SANCTIONED SHIP',
            'resolution': '2397', 'measures': ['asset freeze'],
            'matched_by': matched_by}


def test_sanction_imo_match_scores_8():
    profile = {'mmsi': '412000003', 'names_seen': ['SANCTIONED SHIP'],
               'types_seen': ['cargo'], 'total_snapshots': 5}
    result = asus.classify_vessel(profile, [],
                                  sanctions_match=_sanction_entry('imo'))
    assert result['sanctioned'] is True
    assert result['risk_score'] >= asus.SANCTION_IMO_SCORE


def _blacklist_entry(name='SIA', imo='9397080', programs=('OFAC', 'UANI'),
                     flag='Angola'):
    return {'name': name, 'imo': imo, 'flag': flag,
            'programs': list(programs), 'source': 'blacklist',
            'matched_by': 'imo'}


def test_blacklist_imo_hit_scores_8_and_lists_programs():
    """黑名單 IMO 命中 → +8、flag 標出制裁機構。"""
    profile = {'mmsi': '603928000', 'names_seen': ['SIA'],
               'types_seen': ['tanker'], 'total_snapshots': 5}
    r = asus.classify_vessel(profile, [], sanctions_match=_blacklist_entry())
    assert r['sanctioned'] is True
    assert r['risk_score'] >= asus.SANCTION_IMO_SCORE
    assert any('受制裁油輪' in f and 'OFAC' in f for f in r['flags'])


def test_blacklist_identity_concealment_flagged():
    """IMO 命中但 AIS 廣播船名 ≠ 制裁登記名 → 身分掩蓋旗標
    （如 STAR PIONE 廣播、登記名 LORIAN）。"""
    profile = {'mmsi': '613617404', 'names_seen': ['STAR PIONE'],
               'types_seen': ['tanker'], 'total_snapshots': 5}
    entry = _blacklist_entry(name='LORIAN', imo='9259343',
                             programs=('UANI',), flag='Cameroon')
    r = asus.classify_vessel(profile, [], sanctions_match=entry)
    assert r.get('sanction_identity_concealment') is True
    assert any('身分掩蓋' in f and 'LORIAN' in f for f in r['flags'])


def test_blacklist_matching_name_matches_no_concealment():
    """AIS 船名 == 登記名時不觸發身分掩蓋。"""
    profile = {'mmsi': '603928000', 'names_seen': ['SIA'],
               'types_seen': ['tanker'], 'total_snapshots': 5}
    r = asus.classify_vessel(profile, [], sanctions_match=_blacklist_entry())
    assert r.get('sanction_identity_concealment') is not True


def test_load_sanctions_list_includes_blacklist():
    """load_sanctions_list 應把黑名單 IMO 併入 imo_set（真實檔案）。"""
    by_imo, imo_set, name_set = asus.load_sanctions_list()
    # 7 艘已確認在監測海域的受制裁油輪，其 IMO 應在集合中
    for imo in ('9113379', '9397080', '9259343', '9194139',
                '9395379', '9040118', '9202388'):
        assert imo in imo_set, f'{imo} 應在制裁 IMO 集合'
        assert by_imo[imo].get('source') == 'blacklist'
    # 但這些黑名單船名不應污染 name_set（避免撞名）
    assert 'SIA' not in name_set


def test_sanction_name_only_scores_4():
    profile = {'mmsi': '412000004', 'names_seen': ['SANCTIONED SHIP'],
               'types_seen': ['cargo'], 'total_snapshots': 5}
    imo_hit = asus.classify_vessel(profile, [],
                                   sanctions_match=_sanction_entry('imo'))
    name_hit = asus.classify_vessel(profile, [],
                                    sanctions_match=_sanction_entry('name'))
    assert imo_hit['risk_score'] - name_hit['risk_score'] == (
        asus.SANCTION_IMO_SCORE - asus.SANCTION_NAME_ONLY_SCORE)
    assert any('僅船名匹配' in f for f in name_hit['flags'])


# =========================================================================
# 台灣船隻排除：船旗 416 / 停泊台灣港內（含防偽冒安全閥）
# =========================================================================

def _profile(mmsi):
    return {'mmsi': mmsi, 'names_seen': ['TW TEST'],
            'types_seen': ['cargo'], 'total_snapshots': 5}


def test_taiwan_flag_excluded():
    """MMSI 416 開頭（台灣船旗）→ 排除，不進入分析。"""
    result = asus.classify_vessel(_profile('416123456'), [])
    assert result['excluded'] is True
    assert any(r['id'] == 'flag_taiwan' for r in result['exclusion_rules'])
    assert result['risk_score'] == 0


def test_taiwan_flag_with_identity_events_not_excluded():
    """416 但有身分變更事件 → 安全閥生效，照常分析（防偽冒台灣 MMSI）。"""
    events = [{'mmsi': '416123456', 'timestamp': '2026-07-01T00:00:00+00:00',
               'changes': [{'field': 'name', 'old': 'A', 'new': 'B'}]}]
    result = asus.classify_vessel(_profile('416123456'), [],
                                  identity_events=events)
    assert result['excluded'] is False


def test_taiwan_flag_with_sanction_not_excluded():
    """416 但命中制裁名單 → 安全閥生效，照常分析。"""
    result = asus.classify_vessel(_profile('416123456'), [],
                                  sanctions_match=_sanction_entry('imo'))
    assert result['excluded'] is False
    assert result['sanctioned'] is True


def test_moored_in_taiwan_port_excluded():
    """非 416、最後位置在高雄港 → 排除（停泊台灣港內）。"""
    pts = make_track([(OPEN_LAT, OPEN_LON), (22.6153, 120.2664)], speed=1.0)
    result = asus.classify_vessel(_profile('412000005'), pts)
    assert result['excluded'] is True
    assert any(r['id'] == 'moored_taiwan_port'
               for r in result['exclusion_rules'])


def test_moored_in_cn_port_not_excluded():
    """最後位置在廈門（大陸港）→ 不觸發台灣港排除。"""
    pts = make_track([(OPEN_LAT, OPEN_LON), (24.45, 118.07)], speed=1.0)
    result = asus.classify_vessel(_profile('412000006'), pts)
    assert result['excluded'] is False


def test_visited_taiwan_port_but_now_at_sea_not_excluded():
    """先前靠過台灣港、最後位置在開放海域 → 不排除，照常分析。"""
    pts = make_track([(22.6153, 120.2664), (OPEN_LAT, OPEN_LON)], speed=8.0)
    result = asus.classify_vessel(_profile('412000007'), pts)
    assert result['excluded'] is False


# =========================================================================
# 活躍船過濾：>14 天未見的舊 profile 不進入分析
# =========================================================================

def test_stale_profile_not_recently_active():
    """無航跡、最後出現 30 天前 → 非活躍，跳過分析。"""
    from datetime import datetime, timezone
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    profile = {'mmsi': '412000009',
               'last_seen_timestamps': ['2026-06-01T00:00:00+00:00']}
    assert asus.is_recently_active(profile, has_track=False, now=now) is False


def test_recent_profile_is_active():
    """無航跡但 3 天前出現過 → 活躍。"""
    from datetime import datetime, timezone
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    profile = {'mmsi': '412000010',
               'last_seen_timestamps': ['2026-06-30T12:00:00+00:00']}
    assert asus.is_recently_active(profile, has_track=True, now=now) is True
    assert asus.is_recently_active(profile, has_track=False, now=now) is True


def test_track_only_vessel_is_active():
    """有航跡但無 profile 時間戳（track-only）→ 活躍（航跡檔本身就是 14 天滾動）。"""
    assert asus.is_recently_active({}, has_track=True) is True
    assert asus.is_recently_active({}, has_track=False) is False

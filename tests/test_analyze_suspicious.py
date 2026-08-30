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


def _loiter_track(n=80, speed=1.0, jitter=0.03, lat=OPEN_LAT, lon=OPEN_LON):
    """離岸原地徘徊航跡：n 點、2h 間隔（跨度 >6 天）、小範圍低速。"""
    import random
    rng = random.Random(1)
    positions = [(lat + rng.uniform(-jitter, jitter),
                  lon + rng.uniform(-jitter, jitter)) for _ in range(n)]
    return make_track(positions, speed=speed)


def test_offshore_loiter_foc_tanker_flagged():
    """權宜船旗（非前十大）油輪離岸徘徊 >5 天 → 觸發、計分、判可疑。"""
    profile = {'mmsi': '668116337', 'names_seen': ['RUI WEI'],
               'types_seen': ['tanker'], 'total_snapshots': 50}
    r = asus.classify_vessel(profile, _loiter_track())
    assert r['offshore_loitering'] is True
    assert r['non_top10_flag'] is True
    assert r['risk_score'] >= asus.OFFSHORE_LOITER_SCORE
    assert any('離岸長期徘徊' in f for f in r['flags'])


def test_offshore_loiter_top10_flag_not_scored():
    """相同徘徊行為但掛前十大船旗（巴拿馬 352）→ 偵測到但不加分
    （單純離岸徘徊可能是合法等泊，須搭配權宜船旗才可疑）。"""
    profile = {'mmsi': '352999999', 'names_seen': ['LEGIT TANKER'],
               'types_seen': ['tanker'], 'total_snapshots': 50}
    r = asus.classify_vessel(profile, _loiter_track())
    assert r['offshore_loitering'] is True
    assert r['non_top10_flag'] is False
    assert r['suspicious'] is False


def test_offshore_loiter_ignores_fishing():
    """漁船不套用此規則（僅商船 tanker/cargo/lng）。"""
    ok, _ = asus.check_offshore_loitering(_loiter_track(), 'fishing')
    assert ok is False


def test_transiting_tanker_not_offshore_loiter():
    """高速過境的油輪不算徘徊。"""
    positions = [(OPEN_LAT + i * 0.05, OPEN_LON) for i in range(80)]
    pts = make_track(positions, speed=11.0)
    ok, _ = asus.check_offshore_loitering(pts, 'tanker')
    assert ok is False


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


# =========================================================================
# 割草式測線（Criterion 11）
# =========================================================================
# 既有的 Z 字型只數「大幅轉向次數」，抓不到規律測線 —— 規律測線的轉向都
# 集中在兩端，中段是筆直長邊。以下用兩種真實樣態驗證，並覆蓋誤報抑制。

def _lawnmower(lines=4, leg_deg=0.5, spacing_deg=0.05, step_hours=2.0,
               speed=6.0, base_lat=23.60, base_lon=122.30):
    """階梯式網格：東西向長邊、每趟往北挪 spacing_deg。"""
    positions = []
    for i in range(lines):
        lat = base_lat + i * spacing_deg
        lons = ([base_lon, base_lon + leg_deg] if i % 2 == 0
                else [base_lon + leg_deg, base_lon])
        # 每條測線切成 3 點，模擬 2h 取樣
        positions.append((lat, lons[0]))
        positions.append((lat, (lons[0] + lons[1]) / 2))
        positions.append((lat, lons[1]))
    return make_track(positions, step_hours=step_hours, speed=speed)


def _repeat_transect(passes=4, leg_deg=0.5, jitter_deg=0.008,
                     step_hours=2.0, speed=5.5,
                     base_lat=23.60, base_lon=122.30):
    """重複測線：同一條東西向線來回，僅有些微側向偏移。"""
    positions = []
    for i in range(passes):
        lat = base_lat + (i % 2) * jitter_deg
        lons = ([base_lon, base_lon + leg_deg] if i % 2 == 0
                else [base_lon + leg_deg, base_lon])
        positions.append((lat, lons[0]))
        positions.append((lat, (lons[0] + lons[1]) / 2))
        positions.append((lat, lons[1]))
    return make_track(positions, step_hours=step_hours, speed=speed)


def test_lawnmower_grid_detected():
    ok, det = asus.check_survey_pattern(_lawnmower(), 'research')
    assert ok
    assert det['survey_type'] == 'grid'
    assert det['line_count'] >= asus.SURVEY_MIN_LINES
    assert det['reversals'] >= asus.SURVEY_MIN_REVERSALS
    assert det['spacing_cv'] <= asus.SURVEY_SPACING_CV_MAX


def test_repeat_transect_detected():
    """實測樣態：向陽紅03 在花蓮外海反覆重走同一條測線。"""
    ok, det = asus.check_survey_pattern(_repeat_transect(), 'research')
    assert ok
    assert det['survey_type'] == 'repeat_transect'
    assert det['offset_mad_km'] <= asus.SURVEY_TRANSECT_SPREAD_KM


def test_straight_transit_is_not_a_survey():
    """單向直線過境 —— 沒有反向，不是測線。"""
    positions = [(23.60 + i * 0.05, 122.30 + i * 0.10) for i in range(10)]
    ok, _ = asus.check_survey_pattern(
        make_track(positions, speed=10.0), 'research')
    assert not ok


def test_fishing_type_excluded():
    ok, _ = asus.check_survey_pattern(_lawnmower(), 'fishing')
    assert not ok


def test_cn_fishing_name_excluded_even_when_type_is_wrong():
    """AIS 船種碼不可信：閩東漁廣播成 other/cargo，拖網幾何與測線幾乎相同。
    實測全船隊掃描時，不看船名的話 3.08% 命中、前 25 名全是漁船。"""
    track = _lawnmower()
    ok, _ = asus.check_survey_pattern(track, 'other', ['MINDONGYU63179'])
    assert not ok
    ok, _ = asus.check_survey_pattern(track, 'cargo', ['ZHEPINGYU82055'])
    assert not ok


def test_gov_type_beats_province_name_rule():
    """is_cn_fishing_vessel 的 `^XIANG`（湘）會誤中 XIANG YANG HONG —
    公務/科研分類必須優先，否則向陽紅永遠被當漁船排除。"""
    assert asus.is_cn_fishing_vessel('XIANG YANG HONG 03') is True
    ok, _ = asus.check_survey_pattern(
        _repeat_transect(), 'research', ['XIANG YANG HONG 03'])
    assert ok


def test_passenger_ferry_excluded():
    """渡輪定期航線就是反覆重走同一條線 —— 本業，不是測繪。"""
    ok, _ = asus.check_survey_pattern(_repeat_transect(), 'passenger')
    assert not ok


def test_high_speed_transit_not_survey():
    """>12kn 不是測線作業速度（拖曳儀器跑不了那麼快）。"""
    ok, _ = asus.check_survey_pattern(
        _lawnmower(speed=18.0), 'research')
    assert not ok


def test_signal_gap_does_not_fabricate_a_leg():
    """訊號空白兩端不可連成一條假測線 —— 實測向陽紅03 有 54h 無訊號，
    不切斷的話會併成 66km 的假長邊，把真正的測線樣態蓋掉。"""
    pts = _repeat_transect()
    # 在中段插入 60 小時的空白
    for p in pts[6:]:
        day = int(p['t'][8:10]) + 3
        p['t'] = p['t'][:8] + f'{day:02d}' + p['t'][10:]
    legs = asus._split_into_legs(pts)
    for lg in legs:
        assert asus._gap_hours(lg['start'], lg['end']) is not None
        # 沒有任何一條測線跨越那段空白
        assert lg['length_km'] < 200


def test_in_port_points_excluded_from_survey():
    """港區操船會產生短的平行來回 —— 港內點不納入測線判定。"""
    track = _lawnmower(base_lat=22.6153, base_lon=120.2664,
                       leg_deg=0.01, spacing_deg=0.002)
    asus.annotate_port_points(track)
    ok, _ = asus.check_survey_pattern(track, 'research')
    assert not ok


def test_survey_scores_without_type_multiplier():
    """測線分屬高威脅指標，不吃船型乘數。"""
    profile = {'mmsi': '413701510', 'names_seen': ['XIANG YANG HONG 03'],
               'types_seen': ['research'], 'total_snapshots': 20}
    result = asus.classify_vessel(profile, _repeat_transect())
    assert result['survey_pattern'] is True
    assert result['risk_score'] >= asus.SURVEY_SCORE


# =========================================================================
# 公務船編隊（Criterion 12）
# =========================================================================

def _gov_profile(mmsi='413701510', vtype='research'):
    return {'mmsi': mmsi, 'names_seen': ['XIANG YANG HONG 03'],
            'types_seen': [vtype], 'total_snapshots': 20}


def test_escorted_formation_scores_and_lifts_multiplier():
    """護航科考 +6，且科研船的 ×0.5 行為分折扣被取消。"""
    track = make_track([(23.60, 122.30 + i * 0.05) for i in range(8)],
                       speed=4.0)
    rec = {'count': 3, 'max_duration_hours': 12.6,
           'escorted_research': True, 'severity': 'high'}
    plain = asus.classify_vessel(_gov_profile(), track)
    escorted = asus.classify_vessel(_gov_profile(), track,
                                    formation_record=rec)
    assert plain['gov_formation'] is False
    assert escorted['gov_formation'] is True
    assert escorted['type_multiplier'] == asus.GOV_INTENT_MULTIPLIER_FLOOR
    assert escorted.get('intent_multiplier_floor') is True
    assert escorted['risk_score'] >= (
        plain['risk_score'] + asus.GOV_FORMATION_ESCORT_SCORE)


def test_plain_formation_scores_less_than_escorted():
    track = make_track([(23.60, 122.30 + i * 0.05) for i in range(8)],
                       speed=4.0)
    plain_rec = {'count': 1, 'max_duration_hours': 8.0,
                 'escorted_research': False, 'severity': 'low'}
    escort_rec = dict(plain_rec, escorted_research=True)
    a = asus.classify_vessel(_gov_profile('413225040', 'msa'), track,
                             formation_record=plain_rec)
    b = asus.classify_vessel(_gov_profile('413225040', 'msa'), track,
                             formation_record=escort_rec)
    assert b['risk_score'] - a['risk_score'] == (
        asus.GOV_FORMATION_ESCORT_SCORE - asus.GOV_FORMATION_SCORE)


def test_fishing_vessel_never_gets_multiplier_floor():
    """測線偵測對純數字船名的低速拖網船仍有殘餘誤報 —— 絕不可讓漁船的
    ×0.2 被抬升到 ×1.0（那會讓一次誤報直接推過可疑門檻）。"""
    profile = {'mmsi': '412446279', 'names_seen': ['64143'],
               'types_seen': ['fishing'], 'total_snapshots': 20}
    result = asus.classify_vessel(profile, _lawnmower(speed=3.0))
    assert result['type_multiplier'] == asus.VESSEL_TYPE_MULTIPLIER['fishing']
    assert result.get('intent_multiplier_floor') is not True


def test_vessel_type_falls_back_to_track_when_profile_empty():
    """vessel_profiles.json 是 Actions 快取；冷啟動時船型/船名全空，
    沒有 fallback 的話漁船排除整個失效（實測誤報 13 → 1702）。"""
    track = _lawnmower(speed=3.0)
    for p in track:
        p['type_name'] = 'other'
        p['name'] = 'MINDONGYU63179'
    result = asus.classify_vessel({'mmsi': '412446718'}, track)
    assert result['survey_pattern'] is False


# =========================================================================
# 徘徊事件擷取（loiter_events / loiter_avg_speed_kn — 週報彙整來源）
# =========================================================================

def _dt(day, hour):
    from datetime import datetime, timezone
    return datetime(2026, 7, day, hour, 0, tzinfo=timezone.utc)


def test_split_loiter_runs_breaks_on_gap():
    """相鄰點間隔 > LOITER_MAX_GAP_HOURS 即斷開為兩個 run。"""
    pts = [(_dt(1, 0), 22.8, 120.27, 2.0),
           (_dt(1, 2), 22.8, 120.27, 2.0),
           (_dt(1, 12), 22.8, 120.27, 2.0),   # gap 10h > 4h
           (_dt(1, 14), 22.8, 120.27, 2.0)]
    runs = asus.split_loiter_runs(pts)
    assert len(runs) == 2
    assert [len(r) for r in runs] == [2, 2]


def test_split_loiter_runs_sorts_unordered_input():
    pts = [(_dt(1, 4), 22.8, 120.27, 2.0),
           (_dt(1, 0), 22.8, 120.27, 2.0),
           (_dt(1, 2), 22.8, 120.27, 2.0)]
    runs = asus.split_loiter_runs(pts)
    assert len(runs) == 1
    assert [p[0].hour for p in runs[0]] == [0, 2, 4]


def test_build_loiter_events_min_hours_and_speeds():
    """只收 ≥3h 的 run；avg/min speed 正確；<3h 的 run 不出事件。"""
    long_run = [(_dt(1, h), 22.8 + h * 0.001, 120.27, s)
                for h, s in [(0, 1.0), (2, 2.0), (4, 3.0)]]  # 跨度 4h
    short_run = [(_dt(2, 0), 23.5, 121.0, 2.0),
                 (_dt(2, 2), 23.5, 121.0, 2.0)]              # 跨度 2h
    events = asus.build_loiter_events([long_run, short_run])
    assert len(events) == 1
    ev = events[0]
    assert ev['hours'] == 4.0
    assert ev['avg_speed_kn'] == 2.0
    assert ev['min_speed_kn'] == 1.0
    assert ev['points'] == 3
    assert abs(ev['center_lat'] - 22.802) < 1e-6
    assert ev['start'].startswith('2026-07-01T00')


def test_build_loiter_events_cap_and_order():
    """事件依時數降冪、cap 5。"""
    runs = []
    for i in range(7):
        span = 3 + i  # 3..9 小時
        runs.append([(_dt(1 + i, 0), 22.8, 120.27, 2.0),
                     (_dt(1 + i, span), 22.8, 120.27, 2.0)])
    events = asus.build_loiter_events(runs)
    assert len(events) == 5
    assert [e['hours'] for e in events] == [9.0, 8.0, 7.0, 6.0, 5.0]


def test_cable_proximity_emits_loiter_events():
    """check_cable_proximity 現在輸出事件與均速，且與 loiter_slow_hours 一致。"""
    pts = _near_cable_track(hours=6, speed=2.0)
    _, details = asus.check_cable_proximity(pts)
    assert details['loiter_triggered'] is True
    assert details['loiter_avg_speed_kn'] == 2.0
    assert len(details['loiter_events']) == 1
    ev = details['loiter_events'][0]
    assert ev['hours'] == details['loiter_slow_hours']
    assert 22.7 < ev['center_lat'] < 22.9


def test_cable_proximity_no_loiter_no_events():
    """高速過境：無合格徘徊段 → 事件空、均速 None。"""
    pts = _near_cable_track(hours=6, speed=7.5)
    _, details = asus.check_cable_proximity(pts)
    assert details['loiter_events'] == []
    assert details['loiter_avg_speed_kn'] is None


# =========================================================================
# compact_highrisk_row（highrisk_snapshot.json 的列格式）
# =========================================================================

def test_compact_highrisk_row_fields():
    c = {
        'mmsi': '412345678', 'names': ['SHIP A', 'SHIP B'],
        'vessel_type': 'cargo', 'risk_score': 11, 'risk_level': 'high',
        'non_top10_flag': True, 'sanctioned': False,
        'cable_loitering': True, 'offshore_loitering': False,
        'cable_details': {
            'loiter_slow_hours': 4.0, 'loiter_avg_speed_kn': 1.8,
            'cables_nearby': ['a', 'b', 'c', 'd'],
            'loiter_events': [
                {'center_lat': 24.1234, 'center_lon': 121.5678,
                 'hours': 4.0, 'avg_speed_kn': 1.8,
                 'start': '2026-07-01T00:00:00+00:00'}]},
        'offshore_loiter_details': {'loiter_days': 0.0},
        'last_lat': 24.1, 'last_lon': 121.5,
        'last_seen': '2026-07-01T06:00:00+00:00',
        'geofence': {'zone': 'eez'},
    }
    row = asus.compact_highrisk_row(c)
    assert row['mmsi'] == '412345678'
    assert row['name'] == 'SHIP A'
    assert row['loiter_h'] == 4.0
    assert row['loiter_kn'] == 1.8
    assert row['cables'] == ['a', 'b', 'c']          # cap 3
    assert row['ev'] == [[24.1234, 121.5678, 4.0, 1.8, '2026-07-01']]
    assert row['zone'] == 'eez'


def test_compact_highrisk_row_tolerates_missing_fields():
    """冷啟動/舊格式：缺 details 不噴例外。"""
    row = asus.compact_highrisk_row({'mmsi': '9', 'risk_score': 8})
    assert row['name'] == ''
    assert row['ev'] == []
    assert row['loiter_kn'] is None
    assert row['zone'] is None


def test_names_fall_back_to_track_when_profile_empty():
    """profile 是 Actions 快取備援的，冷啟動時可能空白 —— 船名要退回航跡點，
    否則整份週報的船名欄全空（船型早已有同樣的 fallback）。"""
    pts = make_track([(23.8, 122.5)] * 3, speed=4.0, name='TRACK NAME')
    profile = {'mmsi': '412000099', 'names_seen': [], 'types_seen': ['cargo'],
               'total_snapshots': 3}
    result = asus.classify_vessel(profile, pts)
    assert result['names'] == ['TRACK NAME']


def test_profile_names_win_over_track_names():
    pts = make_track([(23.8, 122.5)] * 3, speed=4.0, name='TRACK NAME')
    profile = {'mmsi': '412000098', 'names_seen': ['PROFILE NAME'],
               'types_seen': ['cargo'], 'total_snapshots': 3}
    result = asus.classify_vessel(profile, pts)
    assert result['names'] == ['PROFILE NAME']

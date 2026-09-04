"""週/月報圖表 — report_charts.py

只測純函式與繪圖層的呼叫（stub ax）；CI 沒裝 matplotlib，因此本檔不得
import matplotlib，report_charts 也把 matplotlib 留在函式內 import。
色階與分位法必須與 docs/js/weekly-report.js 一致（PNG 與網頁不能各講一套），
tests/weekly-report-smoke.js 守前端那一半。
"""
import pytest

import report_charts as rc


# ══════════════════════════════════════════════════════════════════
# 分位色階
# ══════════════════════════════════════════════════════════════════

def test_quantile_cuts_matches_frontend_scheme():
    """與前端 quantileScale 相同：buckets-1 個門檻，取 floor(n*i/buckets)。"""
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    cuts = rc.quantile_cuts(values, 5)
    assert cuts == [3, 5, 7, 9]


def test_quantile_cuts_empty_and_single():
    assert rc.quantile_cuts([], 5) == []
    assert rc.quantile_cuts([7], 5) == [7, 7, 7, 7]


def test_quantile_cuts_ignores_non_numeric():
    assert rc.quantile_cuts([1, None, 3, 'x', 5], 3) == [3, 5]


def test_color_index_and_color_for():
    cuts = [3, 5, 7, 9]
    assert rc.color_index(1, cuts) == 0
    assert rc.color_index(3, cuts) == 0      # <= 邊界落在低檔
    assert rc.color_index(4, cuts) == 1
    assert rc.color_index(100, cuts) == 4    # 超過所有門檻 → 最深
    assert rc.color_for(100, cuts) == rc.HEAT_COLORS[-1]
    assert rc.color_for(1, cuts) == rc.HEAT_COLORS[0]


def test_color_for_no_cuts_is_lightest():
    assert rc.color_for(42, []) == rc.HEAT_COLORS[0]


def test_heat_palette_matches_frontend():
    """色階若與 weekly-report.js 不同，同一份資料在網頁與 LINE 會不同色。"""
    assert rc.HEAT_COLORS == ['#3d3520', '#7a5c1e', '#b3701f', '#e05a33', '#ff2d55']


def test_quantile_beats_linear_on_skewed_data():
    """離島與本島量級差極大 —— 等距分箱會把絕大多數格擠進同一色，
    分位法則讓每一檔都有格子（前端註解記載的坑）。"""
    values = [1, 2, 2, 3, 3, 4, 5, 6, 8, 900]
    cuts = rc.quantile_cuts(values, 5)
    idx = {rc.color_index(v, cuts) for v in values}
    assert len(idx) >= 4          # 分位：至少 4 檔有資料
    lo, hi = min(values), max(values)
    lin = [lo + (hi - lo) * i / 5 for i in range(1, 5)]
    lin_idx = {rc.color_index(v, lin) for v in values}
    assert len(lin_idx) == 2      # 等距：全擠在頭尾兩檔


# ══════════════════════════════════════════════════════════════════
# 視野計算
# ══════════════════════════════════════════════════════════════════

HOTSPOTS = [
    {'lat': 26.2, 'lon': 120.1, 'loiter_hours': 300.0, 'vessels': 19,
     'avg_speed_kn': 1.0, 'events': 25},
    {'lat': 24.4, 'lon': 118.4, 'loiter_hours': 50.0, 'vessels': 5,
     'avg_speed_kn': None, 'events': 4},
]


def test_hotspot_bounds_lat_first_order():
    """回傳順序必須是 (lat_min, lat_max, lon_min, lon_max) —— map_basemap 的約定。"""
    b = rc.hotspot_bounds(HOTSPOTS, pad=0.5, include_bounds=None)
    lat_min, lat_max, lon_min, lon_max = b
    assert lat_min == pytest.approx(23.9)
    assert lat_max == pytest.approx(26.7)
    assert lon_min == pytest.approx(117.9)
    assert lon_max == pytest.approx(120.6)
    assert lat_min < lat_max and lon_min < lon_max


def test_hotspot_bounds_includes_taiwan_view():
    """熱區全聚在福建外海時，仍要把台灣納入視野，否則圖上沒有參照點。"""
    taiwan = (21.9, 25.3, 120.0, 122.0)
    b = rc.hotspot_bounds([HOTSPOTS[1]], pad=0.3, include_bounds=taiwan)
    lat_min, lat_max, lon_min, lon_max = b
    assert lat_min <= 21.9 and lat_max >= 25.3
    assert lon_min == pytest.approx(118.1) and lon_max >= 122.0


def test_hotspot_bounds_empty():
    taiwan = (21.9, 25.3, 120.0, 122.0)
    assert rc.hotspot_bounds([], include_bounds=taiwan) == taiwan
    assert rc.hotspot_bounds([], include_bounds=None) is None


# ══════════════════════════════════════════════════════════════════
# 圖例 / 分布排序
# ══════════════════════════════════════════════════════════════════

def test_legend_bins_labels_and_last_bucket():
    cuts = [3, 5, 7, 9]
    bins = rc.legend_bins(HOTSPOTS, cuts)
    assert len(bins) == len(rc.HEAT_COLORS)
    assert bins[0] == (rc.HEAT_COLORS[0], '≤ 3 h')
    assert bins[-1] == (rc.HEAT_COLORS[-1], '> 9 h')


def test_legend_bins_empty_hotspots():
    assert rc.legend_bins([], [1, 2]) == []


def test_top_types_sorted_desc():
    summary = {'by_type': {'fishing': 134, 'cargo': 179, 'tanker': 58}}
    assert rc.top_types(summary) == [('cargo', 179), ('fishing', 134),
                                     ('tanker', 58)]


def test_top_flags_sorted_by_count_not_mid():
    """MID 是數字型字串；排序必須看 count，不能倚賴鍵順序。"""
    summary = {'by_flag': {
        '100': {'en': 'Unknown', 'zh': '未知', 'count': 3},
        '412': {'en': 'China', 'zh': '中國', 'count': 145},
        '538': {'en': 'Marshall Islands', 'zh': '馬紹爾群島', 'count': 15},
    }}
    rows = rc.top_flags(summary)
    assert [r[0] for r in rows] == ['412', '538', '100']
    assert rows[0][1] == '中國'


def test_top_flags_falls_back_to_en_then_mid():
    summary = {'by_flag': {'999': {'en': 'Nowhere', 'count': 1},
                           '888': {'count': 2}}}
    rows = rc.top_flags(summary)
    assert rows[0] == ('888', '888', 2)
    assert rows[1] == ('999', 'Nowhere', 1)


def test_empty_summary_helpers():
    assert rc.top_types({}) == [] and rc.top_flags({}) == []
    assert rc.top_types(None) == [] and rc.top_flags(None) == []


# ══════════════════════════════════════════════════════════════════
# 繪圖層（stub ax；沿用 tests/test_map_basemap.py 的做法）
# ══════════════════════════════════════════════════════════════════

class StubAx:
    """記錄 add_patch / scatter 呼叫的假座標軸。"""

    def __init__(self):
        self.patches = []
        self.scatters = []

    def add_patch(self, patch):
        self.patches.append(patch)

    def scatter(self, xs, ys, **kwargs):
        self.scatters.append((list(xs), list(ys), kwargs))


def test_draw_hotspot_cells_geometry_and_color():
    pytest.importorskip("matplotlib")   # 只有這兩項需要真的 Rectangle
    ax = StubAx()
    cuts = rc.quantile_cuts([h['loiter_hours'] for h in HOTSPOTS], 5)
    drawn = rc.draw_hotspot_cells(ax, HOTSPOTS, cuts)
    assert drawn == len(ax.patches) == 2
    r = ax.patches[0]
    # 0.1° 格、以中心座標展開（與 grid_utils.grid_cell / 前端一致）
    assert r.get_width() == pytest.approx(0.1)
    assert r.get_height() == pytest.approx(0.1)
    assert r.get_x() == pytest.approx(120.05)
    assert r.get_y() == pytest.approx(26.15)
    # 最高時數的格用最深色
    assert r.get_facecolor()[:3] != (0, 0, 0)


def test_draw_hotspot_cells_skips_missing_coords():
    pytest.importorskip("matplotlib")
    ax = StubAx()
    assert rc.draw_hotspot_cells(ax, [{'lat': None, 'lon': 1.0,
                                       'loiter_hours': 5}], []) == 0
    assert ax.patches == []


def test_draw_vessel_points_small_and_translucent():
    """點必須小且半透明 —— 數百艘實心大點會整片蓋掉底下的熱區格。"""
    ax = StubAx()
    vessels = [
        {'last_lat': 24.0, 'last_lon': 120.0, 'risk_level': 'critical',
         'vessel_type': 'cargo'},
        {'last_lat': 25.0, 'last_lon': 121.0, 'risk_level': 'high',
         'vessel_type': 'fishing'},
        {'last_lat': None, 'last_lon': None, 'risk_level': 'high',
         'vessel_type': 'cargo'},
    ]
    assert rc.draw_vessel_points(ax, vessels) == 2
    crit, high = ax.scatters[0][2], ax.scatters[1][2]
    assert crit['s'] > high['s']            # critical 略大
    assert crit['s'] <= 20 and crit['alpha'] < 1
    assert crit['c'] == rc.TYPE_COLORS['cargo']
    assert crit['edgecolors'] == rc.RISK_COLORS['critical']


def test_draw_vessel_points_respects_limit():
    ax = StubAx()
    many = [{'last_lat': 24.0, 'last_lon': 120.0, 'risk_level': 'high',
             'vessel_type': 'cargo'}] * 10
    assert rc.draw_vessel_points(ax, many, limit=4) == 4


def test_render_hotspot_map_returns_none_without_hotspots(tmp_path):
    """無熱區時回 None，呼叫端據此不附圖（而不是送一張空圖）。"""
    out = tmp_path / 'x.png'
    assert rc.render_hotspot_map({'hotspots': []}, str(out)) is None
    assert not out.exists()

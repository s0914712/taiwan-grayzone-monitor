#!/usr/bin/env python3
"""
================================================================================
海底電纜威脅偵測 — 可疑船隻分析引擎
Suspicious Vessel Analysis: Submarine Cable Threat Detection
================================================================================

偵測邏輯（針對海纜破壞威脅 + AIS 偽訊號）：
  1. 海底電纜鄰近活動 (Cable Proximity)
     - 船隻航跡經過海纜路線 5 公里內（港內點排除 — 海纜登陸點鄰近港口）
  2. Z 字型移動模式 (Zigzag Pattern)
     - 頻繁改變航向，疑似拖錨或破壞行為（排除錨泊擺動；拖錨組合需 ≤7kn）
  3. 200 公尺等深線活動 (Continental Shelf Edge)
     - 在大陸棚邊緣活動，海纜密集區
  4. AIS 身分變更 (Identity Manipulation)
     - 變更船名、呼號、IMO 等識別資訊
  5. AIS 偽訊號偵測 (Spoofing Detection)
     a. 不可能物理 — 瞬移（排除 MMSI 共用）、速度/航向不一致（僅密集取樣）
     b. 方形軌跡 — 多次 ~90° 轉彎 + 封閉路徑（港內操船排除）
     c. 圓形軌跡 — 半徑 CV 極低 + 弧度覆蓋 > 270°（錨泊迴旋/港區干擾排除）
================================================================================
"""

import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from geo_utils import haversine_km, calc_bearing
from io_utils import atomic_write_json
import geofence
# 大陸漁船船名判定 — 與 fetch_ais_data 共用同一份規則（勿另抄一份）
from fetch_ais_data import is_cn_fishing_vessel

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
# vessel_profiles + ais_track_commercial: pipeline-only, live in data/ (Actions cache).
# ais_track_history stays in docs/ (fetched directly by frontend animation pages).
HISTORY_FILE = DATA_DIR / "vessel_profiles.json"
TRACK_HISTORY_FILE = DOCS_DIR / "ais_track_history.json"
TRACK_COMMERCIAL_FILE = DATA_DIR / "ais_track_commercial.json"
CABLE_GEO_FILE = DATA_DIR / "cable-geo.json"
IDENTITY_EVENTS_FILE = DATA_DIR / "identity_events.json"
SANCTIONS_FILE = DATA_DIR / "un_sanctions_vessels.json"
SANCTIONS_BLACKLIST_FILE = DATA_DIR / "sanctions_blacklist.json"  # 多機構影子船隊黑名單
OUTPUT_FILE = DATA_DIR / "suspicious_vessels.json"
# 完整高風險清單（suspicious 不截斷、compact 列）— aggregate_highrisk.py 的
# 累積來源。gitignored：update-ais.yml 一天寫 ~17 次，不能進 git。
HIGHRISK_SNAPSHOT_FILE = DATA_DIR / "highrisk_snapshot.json"
ITU_MARS_CACHE = DATA_DIR / "itu_mars_cache.json"
SHIP_TRANSFERS_FILE = DATA_DIR / "ship_transfers.json"
GOV_FORMATIONS_FILE = DATA_DIR / "gov_formations.json"  # detect_gov_formation.py 產出

# ── 門檻設定 ────────────────────────────────────────────
CABLE_PROXIMITY_KM = 5.0          # 海纜 5 公里內視為鄰近
CABLE_LOITER_HOURS = 3.0          # 海纜鄰近低速徘徊 > 3 小時
CABLE_LOITER_MAX_KNOTS = 5.0      # 低速定義 < 5 knots（>5kn 屬正常過境，非徘徊/拖錨）
LOITER_MAX_GAP_HOURS = 4.0        # 徘徊連續性：慢速時間戳間隔超過此值即斷開（2h 快照 ×2）
ANCHOR_DRAG_MAX_KNOTS = 7.0       # 拖錨情境速度上限 — >7kn 航行中的船幾乎不可能下錨
STATIONARY_MAX_KNOTS = 1.0        # 錨泊/繫泊視為靜止（錨泊擺動漂移不算轉向）
ZIGZAG_HEADING_CHANGE_DEG = 45    # 航向變化 > 45° 視為一次轉向
ZIGZAG_MIN_TURNS = 3              # 至少 3 次轉向才算 Z 字型
DEPTH_200M_CONTOUR_KM = 10.0      # 200m 等深線緩衝區寬度
NAME_CHANGE_THRESHOLD = 2         # 船名變更 ≥ 2 次
GOING_DARK_GAP_HOURS = 18         # AIS 消失 > 18 小時
ANALYSIS_ACTIVE_DAYS = 14         # 僅分析近 14 天活躍的船 — profile 保留 90 天，
                                  # 但早已離開監測海域的船只剩舊 profile，
                                  # 拿舊資料計分只會灌水統計（曾把 5 萬艘全算進來）

# ── 離岸徘徊（影子船隊待命樣態，獨立於海纜）─────────────────────
# 權宜船旗油輪/貨輪在離岸海域連續數天原地低速徘徊 = 浮動儲油/等待 STS 的
# 典型影子船隊待命樣態。既有徘徊分只在「海纜附近」才給，會漏掉不靠海纜卻
# 明顯待命的油輪（如高雄西南外海徘徊 35 天的 RUI WEI）。此規則不看海纜。
OFFSHORE_LOITER_DAYS = 5.0        # 低速點連續跨度 ≥5 天
OFFSHORE_LOITER_MAX_KNOTS = 3.0   # 低速定義 <3kn
OFFSHORE_LOITER_MEDIAN_RADIUS_KM = 20.0  # 低速點距中心的中位半徑 ≤20km（原地打轉）
OFFSHORE_LOITER_MIN_LOW_FRAC = 0.5       # 過半數航跡點低速
OFFSHORE_LOITER_TYPES = ('tanker', 'cargo', 'lng')  # 僅商船（大噸位、有儲運能力）
OFFSHORE_LOITER_SCORE = 4         # 中度指標（線索非鐵證；需搭配非前十大船旗）

# ── AIS 偽訊號偵測門檻 (Spoofing Detection) ───────────────
SPOOF_TELEPORT_KMH = 100.0         # 最大合理速度 ~54kn；超過即瞬移
SPOOF_SPEED_MISMATCH_RATIO = 3.0   # 計算速度 / 回報速度 比值門檻
SPOOF_BEARING_MISMATCH_DEG = 60.0  # 計算航向 vs 回報 COG 差異門檻
PHYSICS_MISMATCH_MAX_DT_HOURS = 1.0  # 速度/航向不符僅在取樣間隔 ≤1h 時檢查
                                     # （2h 快照下瞬時 SOG vs 平均速度比對無意義）
SPOOF_ANCHOR_SWING_RADIUS_KM = 0.6   # 圓形軌跡半徑 ≤0.6km + 低速/錨泊 = 錨泊迴旋，非偽訊號
SPOOF_ANCHOR_SWING_MAX_KNOTS = 2.0   # 錨泊迴旋速度中位數上限
SPOOF_BOX_ANGLE_TOLERANCE = 25.0   # 90° ± 25° 視為直角轉彎（65°-115°）
SPOOF_BOX_MIN_TURNS = 3            # 至少 3 次直角轉彎
SPOOF_BOX_CLOSURE_KM = 5.0         # 起終點 < 5km 視為封閉路徑
SPOOF_CIRCLE_MIN_POINTS = 6        # 圓形偵測最少點數
SPOOF_CIRCLE_RADIUS_CV = 0.25      # 半徑變異係數門檻 (std/mean < 0.25)
SPOOF_CIRCLE_MAX_RADIUS_KM = 5.0   # 半徑 > 5km 排除（可能正常航行）
SPOOF_CIRCLE_MIN_RADIUS_KM = 0.1   # 半徑 < 0.1km 排除（GPS 漂移）
SPOOF_CIRCLE_MIN_ARC_DEG = 270.0   # 弧度覆蓋 > 270° 才算圓形

# ── 星爆/蜘蛛網軌跡（僅針對非漁船）─────────────────────────
# 多次從中心點出發的輻射型往返，常見於 AIS 信號操控
STARBURST_MIN_POINTS = 10            # 至少 10 個有效移動點
STARBURST_MIN_ARC_DEG = 270.0        # 弧度覆蓋 ≥270°（放射狀）
STARBURST_RADIUS_CV_MIN = 0.35       # CV ≥0.35 區別於圓形（圓形是 <0.25）
STARBURST_MIN_RADIUS_KM = 0.5        # 平均半徑下限
STARBURST_MAX_RADIUS_KM = 30.0       # 平均半徑上限
STARBURST_MIN_SPOKES = 5             # 至少 5 個方向有遠端點（30° 分箱）
STARBURST_HUB_FRACTION = 0.3         # ≥30% 的點集中於 hub (r < 0.3 × max_r)

# ── 割草式測線（Lawnmower / Survey Grid）──────────────────────
# 海洋測繪/物探的標準作業：一組平行、等間距的來回測線。既有的 Z 字型偵測
# 只數「大幅轉向次數」，抓不到這種規律樣態 —— 規律測線的轉向都集中在兩端，
# 中段是筆直長邊，轉向次數常低於門檻。實測 2026-08 向陽紅03 在花蓮外海
# 23.5-23.7°N / 122.2-122.8°E 的東西向來回即屬此類。
# 與 Z 字型的差異：測線要求「平行 + 反向 + 等間距」，不只是頻繁轉向。
SURVEY_MIN_LEG_KM = 2.0              # 單一測線長度下限（短段視為轉彎過程）
SURVEY_LEG_BEARING_TOLERANCE_DEG = 35.0   # 同一測線內的航向容差
SURVEY_AXIS_TOLERANCE_DEG = 30.0     # 各測線相對主軸（mod 180°）的容差
SURVEY_REVERSAL_TOLERANCE_DEG = 45.0 # 反向判定：相鄰測線航向差 180°±45°
SURVEY_MIN_LEGS = 3                  # 至少 3 條合格測線
SURVEY_MIN_REVERSALS = 2             # 至少 2 次反向（→ ← → 的來回）
SURVEY_MIN_LINES = 3                 # 至少 3 條「不同」的平行線（去重後）
SURVEY_MIN_PARALLEL_FRACTION = 0.6   # 測區內「同軸航程佔總航程」的下限。
                                     # 以**里程**而非條數計：真網格必然夾雜
                                     # N-1 條短的橫向換線段，用條數算會逼近
                                     # 0.5 而誤殺。實測里程佔比：向陽紅03
                                     # 0.96、合成網格 0.93，低速隨機漂移 0.37
SURVEY_GRID_MIN_MEAN_LEG_KM = 5.0    # 網格樣態的平均測線長下限（漂移產生的
                                     # 假測線多在 2-5km）
SURVEY_MIN_SPACING_KM = 0.5          # 線距下限 — 反覆走同一條線（渡輪、
                                     # 定期航線）線距趨近 0，不是測線
SURVEY_MAX_SPACING_KM = 25.0         # 線距上限（超過即非同一測區）
SURVEY_SPACING_CV_MAX = 0.6          # 線距變異係數 — 等間距才算規律測線
SURVEY_MAX_MEDIAN_KNOTS = 12.0       # 測線作業速度（拖曳儀器，通常 4-10kn）
SURVEY_MIN_SPAN_HOURS = 6.0          # 整段測線作業時間跨度下限
SURVEY_MAX_GAP_HOURS = 6.0           # 相鄰航跡點間隔上限 — 超過即斷開測線。
                                     # 不設此限的話，訊號空白（實測向陽紅03
                                     # 有 54 小時無訊號）兩端會被連成一條假的
                                     # 長直測線，把真正的測線樣態蓋掉
SURVEY_BOX_LINK_KM = 30.0            # 測區分群半徑 — 測線要先依「測區」分群
                                     # 再判定；整段 14 天航跡混了往返母港的
                                     # 長程轉場，直接整條算會把測區樣態蓋掉
# 兩種測繪樣態（皆須平行 + 反向 + 低速 + 時間跨度）：
#   grid            階梯式網格 — ≥3 條不同平行線、線距等間距（經典 lawnmower）
#   repeat_transect 重複測線 — 反覆重走同一條線（實測向陽紅03 在花蓮外海即此類，
#                   單線來回 5 次；磁力/地震測線與海纜路由勘測常見）
SURVEY_TRANSECT_SPREAD_KM = 5.0      # 重複測線：各測線垂距對中位數的
                                     # **中位絕對偏差** ≤此值（用中位數而非
                                     # 全距，才不會被一條進場轉場航段破壞）
SURVEY_TRANSECT_MIN_LEG_KM = 10.0    # 重複測線：平均測線長下限（排除港區短程來回）
# 例行來回作業的船型不納入：漁船拖網、客輪/渡輪定期航線、高速客船
SURVEY_EXCLUDE_TYPES = ('fishing', 'passenger', 'high_speed')
# 公務/科研類別（船名判定漁船時的保護名單，見 _is_routine_sweeper）
GOV_TYPE_NAMES = ('coastguard', 'msa', 'rescue', 'research')
SURVEY_SCORE = 3                     # 高威脅指標（測繪意圖，不受船型乘數影響）

# ── 公務船編隊（detect_gov_formation.py 的輸出）─────────────────
# 科研船 ×0.5 的乘數會把「編隊」這種本質上就是刻意協同的訊號抹平，
# 因此編隊分屬高威脅指標、不乘船型係數。
GOV_FORMATION_SCORE = 4              # 一般公務船編隊（≥2 艘、≥6h）
GOV_FORMATION_ESCORT_SCORE = 6       # 護航科考（科研 + 海警/海巡同框）
# 編隊或測線成立 → 行為分的船型乘數提升至 1.0：這艘船此刻不是在做
# 「例行公務航行」，把科研/公務的 ×0.5 折扣套在它的海纜/等深線行為上
# 會系統性低估。上調至 1.0（不放大、只是不再打折）。
GOV_INTENT_MULTIPLIER_FLOOR = 1.0

# ── 船型威脅乘數 ──────────────────────────────────────────
# 商船（cargo/tanker/lng）錨鍊長、噸位大，對海纜威脅高 → ×1.0
# 漁船常態作業、體積小、危險性低 → ×0.2
# 其他/不明 → ×0.5
VESSEL_TYPE_MULTIPLIER = {
    'cargo': 1.0,
    'tanker': 1.0,
    'lng': 1.0,
    'fishing': 0.2,
    'coastguard': 0.5,  # 海警公務船：大型船體有拖錨風險，但屬國家公務船，給中性權重
    'msa': 0.5,         # 海巡（海事局）公務船
    'rescue': 0.5,      # 海救（救助打撈局）公務船
    'research': 0.5,    # 科研/情報船（科考、海洋調查）
    'other': 0.5,
    'unknown': 0.5,
}

# ── STS 旁靠加分 ──────────────────────────────────────────
STS_SUSPICIOUS_SCORE = 5   # 涉及可疑旁靠事件
STS_ANY_SCORE = 2          # 涉及任何旁靠事件

# ── UN 制裁匹配加分 ───────────────────────────────────────
# IMO 是唯一不變識別碼 → 高信度；純船名比對中式船名重名率高 → 降低權重
SANCTION_IMO_SCORE = 8         # IMO 確認匹配
SANCTION_NAME_ONLY_SCORE = 4   # 僅船名匹配（無 IMO 佐證）

# ── 地理法域 / 海纜緩衝帶加分（行為分，受船型乘數影響）──────────
# 在既有「任一航跡點 5km 內海纜」(cable_proximity, +2) 之上，對「最近位置」
# 做更精細的判讀：緊貼海纜（≤1km）再加分，且若同時位於我國管轄海域
# （內水/領海/鄰接區）則再加分 —— 這正是灰色地帶海纜威脅的核心情境。
CABLE_BUFFER_1KM_SCORE = 1          # 最近位置距海纜 ≤1km
CABLE_BUFFER_JURISDICTION_SCORE = 1  # 且位於內水/領海/鄰接區
JURISDICTION_ZONES = {'internal_waters', 'territorial_sea', 'contiguous_zone'}

# ── 前十大船旗國 MMSI MID（國籍非前十大視為額外可疑）────────
# MID = MMSI 前 3 碼，對照 ITU MID 表
TOP_10_FLAG_MIDS = {
    # Panama
    '351', '352', '353', '354', '355', '356', '357',
    # Liberia
    '636', '637',
    # Marshall Islands
    '538',
    # Hong Kong
    '477',
    # Singapore
    '563', '564', '565', '566',
    # Bahamas
    '308', '309',
    # Malta
    '215', '229', '249', '256',
    # China
    '412', '413', '414',
    # Japan
    '431', '432',
    # Taiwan (ROC)
    '416',
}

# ── 有效 MID（MMSI 前 3 碼須為 ITU 指配）────────────────────
# 觀測到大量 MMSI 根本不是合法識別碼：'KKK' (106000000)、
# 'HOSM AIS TEST SHIP' (100900000)、'00000000000000' (400000000)，
# 以及數百個 8 碼以下的漁網信標（'2680005'、'66750010'…）。
# 這些設備誤設/測試訊號會因「非前十大船旗 +1」「身分變更 +3」等條件
# 長期佔據高風險榜首，卻沒有任何情報價值。
MID_FLAGS_FILE = DATA_DIR / "mid_flags.json"
# 測試/CLI 可能不在 repo 根目錄執行，DATA_DIR 是相對路徑 → 備援用檔案位置回推
_MID_FLAGS_FALLBACK = Path(__file__).resolve().parent.parent / "data" / "mid_flags.json"
_valid_mids_cache = None


def load_valid_mids(path=None):
    """ITU 已指配的 MID 集合（data/mid_flags.json 的鍵，與前端
    docs/js/map-data.js 的 MID_FLAG_TABLE 由 tests/test_mid_flags.py 同步）。

    載入失敗 → 回空集合，呼叫端據此**停用**排除規則：表壞掉時寧可全部照常
    評分，也不要把整支船隊誤判成無效身分。
    """
    global _valid_mids_cache
    if path is None and _valid_mids_cache is not None:
        return _valid_mids_cache
    mids = frozenset()
    for candidate in ([path] if path else [MID_FLAGS_FILE, _MID_FLAGS_FALLBACK]):
        try:
            with open(candidate, 'r', encoding='utf-8') as f:
                table = json.load(f)
            mids = frozenset(
                k for k in table if isinstance(k, str) and re.fullmatch(r'\d{3}', k)
            )
            if mids:
                break
        except Exception:
            continue
    if not mids:
        print("⚠️ 無法載入 MID 表，無效 MMSI 排除規則停用")
    if path is None:
        _valid_mids_cache = mids
    return mids


def is_malformed_mmsi(mmsi):
    """MMSI 不是 9 位純數字 → 格式無效（AIS 設備誤設或去零的岸台/AtoN）。"""
    return not re.fullmatch(r'\d{9}', str(mmsi or ''))


def has_unassigned_mid(mmsi, valid_mids=None):
    """9 位 MMSI 的前 3 碼不在 ITU MID 表中 → 船籍碼無效。
    MID 表載入失敗（空集合）時一律回 False，避免整批誤排除。"""
    mids = load_valid_mids() if valid_mids is None else valid_mids
    if not mids:
        return False
    s = str(mmsi or '')
    return re.fullmatch(r'\d{9}', s) is not None and s[:3] not in mids


# ── 排除規則 (Exclusion Rules) ──────────────────────────────
# 符合任一規則的船隻/設備將被排除在可疑計算之外。
# 新增規則只需在此列表加入一個 dict：
#   id:    唯一識別碼（用於輸出 JSON 標記）
#   label: 人類可讀的排除原因（中文）
#   check: function(mmsi: str, names: list[str]) -> bool
#
# names 參數為該 MMSI 歷史上使用過的所有船名列表。
# ───────────────────────────────────────────────────────────
EXCLUSION_RULES = [
    {
        'id': 'mmsi_9xx',
        'label': '潛水浮標/AtoN (MMSI 9開頭)',
        'check': lambda mmsi, names: mmsi.startswith('9'),
    },
    {
        'id': 'mmsi_898',
        'label': '漁網標記 (MMSI 898開頭)',
        'check': lambda mmsi, names: mmsi.startswith('898'),
    },
    {
        'id': 'name_percent',
        'label': '漁網/魚標信標 (名稱含%)',
        'check': lambda mmsi, names: any('%' in n for n in names if n),
    },
    {
        'id': 'name_buoy',
        'label': '浮標 (名稱含BUOY)',
        'check': lambda mmsi, names: any(
            'BUOY' in (n or '').upper() for n in names if n
        ),
    },
    {
        'id': 'name_voltage_suffix',
        'label': '漁網信標 (名稱尾部電壓值 V)',
        'check': lambda mmsi, names: any(
            re.search(r'\d+\.?\d*V$', (n or '').strip().upper())
            for n in names if n
        ),
    },
    {
        'id': 'name_digit_percent_suffix',
        'label': '漁網信標 (名稱尾部 數字%)',
        'check': lambda mmsi, names: any(
            re.search(r'\d+%$', (n or '').strip())
            for n in names if n
        ),
    },
    {
        'id': 'mmsi_malformed',
        'label': '無效 MMSI (非 9 位數字)',
        'check': lambda mmsi, names: is_malformed_mmsi(mmsi),
    },
    {
        'id': 'mmsi_unassigned_mid',
        'label': '無效 MMSI (MID 未經 ITU 指配)',
        'check': lambda mmsi, names: has_unassigned_mid(mmsi),
    },
]


_exclusion_rule_warned = set()


def check_exclusion_rules(mmsi, names):
    """
    檢查 MMSI / 船名是否符合任一排除規則。
    回傳: (excluded: bool, matched_rules: list[dict])
    每個 matched_rule = {'id': ..., 'label': ...}
    """
    matched = []
    for rule in EXCLUSION_RULES:
        try:
            if rule['check'](mmsi, names):
                matched.append({'id': rule['id'], 'label': rule['label']})
        except Exception as e:
            # 每條規則只警告一次，避免在數千艘船的迴圈中洗版
            if rule['id'] not in _exclusion_rule_warned:
                _exclusion_rule_warned.add(rule['id'])
                print(f"⚠️ 排除規則 {rule['id']} 檢查失敗: {e}")
            continue
    return len(matched) > 0, matched


# ── 台灣周邊 200m 等深線近似座標 ─────────────────────────
# 大陸棚邊緣（西側較淺、東側急降）
DEPTH_200M_CONTOUR = [
    # 台灣東部深水區邊緣（太平洋側）
    (25.5, 122.2), (25.0, 122.1), (24.5, 121.8),
    (24.0, 121.5), (23.5, 121.3), (23.0, 121.0),
    (22.5, 120.8), (22.0, 120.6), (21.5, 120.5),
    # 巴士海峽
    (21.0, 120.8), (20.5, 121.0),
    # 南海北部
    (21.0, 119.0), (21.5, 118.0), (22.0, 117.0),
    # 台灣海峽西側大陸棚邊緣
    (23.0, 118.0), (24.0, 118.5), (25.0, 119.5),
    (25.5, 120.5), (26.0, 121.0),
]

# ── 台灣周邊海纜座標快取 ─────────────────────────────────
_cable_segments = None


def load_cable_segments():
    """載入海纜 GeoJSON，提取台灣周邊的線段座標"""
    global _cable_segments
    if _cable_segments is not None:
        return _cable_segments

    _cable_segments = []

    if not CABLE_GEO_FILE.exists():
        print("⚠️ cable-geo.json not found, skipping cable proximity analysis")
        return _cable_segments

    with open(CABLE_GEO_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for feat in data.get('features', []):
        slug = feat.get('properties', {}).get('slug', '')
        geom = feat.get('geometry', {})
        coords = geom.get('coordinates', [])

        for segment in coords:
            tw_points = []
            for lon, lat in segment:
                # 只保留台灣周邊 (lat 19-28, lon 115-130)
                if 19 <= lat <= 28 and 115 <= lon <= 130:
                    tw_points.append((lat, lon))

            if len(tw_points) >= 2:
                lats = [p[0] for p in tw_points]
                lons = [p[1] for p in tw_points]
                _cable_segments.append({
                    'slug': slug,
                    'points': tw_points,
                    'bbox': (min(lats), min(lons), max(lats), max(lons)),
                })

    print(f"📡 載入 {len(_cable_segments)} 條台灣周邊海纜線段")
    return _cable_segments


def point_to_segment_distance_km(plat, plon, lat1, lon1, lat2, lon2):
    """點到線段的最短距離（公里），用投影法"""
    dx = lat2 - lat1
    dy = lon2 - lon1
    if dx == 0 and dy == 0:
        return haversine_km(plat, plon, lat1, lon1)

    t = max(0, min(1, ((plat - lat1) * dx + (plon - lon1) * dy) / (dx*dx + dy*dy)))
    proj_lat = lat1 + t * dx
    proj_lon = lon1 + t * dy
    return haversine_km(plat, plon, proj_lat, proj_lon)


def angular_diff(a, b):
    """兩角度之間的最小差值 (0-180°)"""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


# =========================================================================
# AIS 偽訊號偵測 (Spoofing Detection)
# =========================================================================

def check_impossible_physics(track_points):
    """
    偵測不可能物理現象：
    - 瞬移 (teleportation): 計算速度超過 SPOOF_TELEPORT_KMH
      （瞬移對兩點船名不同 → 判為 MMSI 共用，另計 mmsi_collision，不算偽訊號）
    - 速度不一致: 計算速度 vs 回報 SOG 比值 > SPOOF_SPEED_MISMATCH_RATIO
    - 航向不一致: 計算航向 vs 回報 COG 差異 > SPOOF_BEARING_MISMATCH_DEG
      （速度/航向不符僅在取樣間隔 ≤ PHYSICS_MISMATCH_MAX_DT_HOURS 檢查 —
       常態 2h 快照下「瞬時 SOG vs 區間平均」比對只會製造誤判）
    回傳: (is_suspicious, details)
    """
    if len(track_points) < 2:
        return False, {}

    teleport_count = 0
    mmsi_collision_count = 0
    max_calc_speed = 0
    speed_mismatch_count = 0
    bearing_mismatch_count = 0
    pairs_checked = 0

    for i in range(1, len(track_points)):
        p1 = track_points[i - 1]
        p2 = track_points[i]

        lat1, lon1 = p1.get('lat'), p1.get('lon')
        lat2, lon2 = p2.get('lat'), p2.get('lon')
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            continue

        # 計算時間差
        try:
            t1 = datetime.fromisoformat(p1['t'].replace('Z', '+00:00'))
            t2 = datetime.fromisoformat(p2['t'].replace('Z', '+00:00'))
            dt_hours = (t2 - t1).total_seconds() / 3600
        except (ValueError, KeyError):
            continue

        if dt_hours <= 0:
            continue

        # 跳過 going-dark 間隔（已由其他偵測器處理）
        if dt_hours > GOING_DARK_GAP_HOURS:
            continue

        pairs_checked += 1
        dist_km = haversine_km(lat1, lon1, lat2, lon2)
        calc_speed_kmh = dist_km / dt_hours

        if calc_speed_kmh > max_calc_speed:
            max_calc_speed = calc_speed_kmh

        # 瞬移偵測 — 兩點船名不同時判為 MMSI 共用（大陸漁船常見），非偽訊號
        if calc_speed_kmh > SPOOF_TELEPORT_KMH:
            n1 = (p1.get('name') or '').strip().upper()
            n2 = (p2.get('name') or '').strip().upper()
            if n1 and n2 and n1 != n2:
                mmsi_collision_count += 1
            else:
                teleport_count += 1

        # 速度/航向不一致：僅在取樣密集時（dt ≤1h）比對才有意義
        if dt_hours <= PHYSICS_MISMATCH_MAX_DT_HOURS:
            # 速度不一致偵測
            reported_sog = p1.get('speed', 0)
            reported_kmh = reported_sog * 1.852  # knots → km/h
            if calc_speed_kmh > 5 and reported_kmh > 5:  # 避免低速噪音
                ratio = calc_speed_kmh / reported_kmh
                if ratio > SPOOF_SPEED_MISMATCH_RATIO or \
                   ratio < (1.0 / SPOOF_SPEED_MISMATCH_RATIO):
                    speed_mismatch_count += 1

            # 航向不一致偵測
            if dist_km >= 1.0:  # 移動距離夠大才比較航向
                calc_brg = calc_bearing(lat1, lon1, lat2, lon2)
                reported_hdg = p1.get('heading')
                if reported_hdg is not None and reported_hdg > 0:
                    diff = angular_diff(calc_brg, reported_hdg)
                    if diff > SPOOF_BEARING_MISMATCH_DEG:
                        bearing_mismatch_count += 1

    is_suspicious = (teleport_count > 0 or
                     speed_mismatch_count >= 2 or
                     bearing_mismatch_count >= 2)

    return is_suspicious, {
        'teleport_count': teleport_count,
        'mmsi_collision_count': mmsi_collision_count,
        'max_calc_speed_kmh': round(max_calc_speed, 1),
        'speed_mismatch_count': speed_mismatch_count,
        'bearing_mismatch_count': bearing_mismatch_count,
        'pairs_checked': pairs_checked,
    }


def check_box_pattern(track_points):
    """
    偵測方形軌跡圖案 (Box Pattern)：
    - 多次接近 90° 的轉彎 (65°-115°)
    - 路徑封閉或包圍面積極小
    常見於 AIS 位置偽造（GPS spoofing）。
    回傳: (is_box, details)
    """
    # 過濾有效移動點
    moving = [p for p in track_points
              if p.get('lat') is not None and p.get('lon') is not None
              and p.get('speed', 0) > 0.5]

    if len(moving) < 4:
        return False, {}

    # 計算連續航向
    bearings = []
    for i in range(1, len(moving)):
        dist = haversine_km(moving[i-1]['lat'], moving[i-1]['lon'],
                            moving[i]['lat'], moving[i]['lon'])
        if dist < 0.1:  # 跳過幾乎不動的點
            continue
        brg = calc_bearing(moving[i-1]['lat'], moving[i-1]['lon'],
                           moving[i]['lat'], moving[i]['lon'])
        bearings.append((brg, i))

    if len(bearings) < 4:
        return False, {}

    # 計算航向變化，統計接近 90° 的轉彎
    right_angle_turns = 0
    for j in range(1, len(bearings)):
        delta = angular_diff(bearings[j][0], bearings[j-1][0])
        if abs(delta - 90) <= SPOOF_BOX_ANGLE_TOLERANCE:
            right_angle_turns += 1

    # 檢查路徑封閉性
    first_pt = moving[0]
    last_pt = moving[-1]
    closure_km = haversine_km(first_pt['lat'], first_pt['lon'],
                              last_pt['lat'], last_pt['lon'])
    path_closed = closure_km < SPOOF_BOX_CLOSURE_KM

    # 計算 bounding box 對角線
    lats = [p['lat'] for p in moving]
    lons = [p['lon'] for p in moving]
    bbox_km = haversine_km(min(lats), min(lons), max(lats), max(lons))

    is_box = (right_angle_turns >= SPOOF_BOX_MIN_TURNS and
              (path_closed or bbox_km < 5.0))

    details = {
        'right_angle_turns': right_angle_turns,
        'path_closed': path_closed,
        'closure_distance_km': round(closure_km, 2),
        'bounding_box_km': round(bbox_km, 2),
    }

    # 港內排除：進出泊位/錨地的操船本來就是直角轉彎 + 小範圍封閉路徑
    if is_box:
        clat = sum(lats) / len(lats)
        clon = sum(lons) / len(lons)
        port = geofence.is_in_port_cached(clat, clon)
        if port:
            details['skipped_reason'] = f'in_port:{port}'
            return False, details

    return is_box, details


def check_circle_pattern(track_points):
    """
    偵測圓形軌跡圖案 (Circle Pattern)：
    - 計算所有點到質心的距離（半徑）
    - 半徑變異係數 (CV) 極低 = 高度對稱
    - 弧度覆蓋 > 270° = 接近完整圓
    常見於 AIS 訊號蓄意操控偽跡。
    回傳: (is_circle, details)
    """
    # 過濾有效移動點
    moving = [p for p in track_points
              if p.get('lat') is not None and p.get('lon') is not None
              and p.get('speed', 0) > 0.5]

    if len(moving) < SPOOF_CIRCLE_MIN_POINTS:
        return False, {}

    # 計算質心
    clat = sum(p['lat'] for p in moving) / len(moving)
    clon = sum(p['lon'] for p in moving) / len(moving)

    # 計算每點到質心的半徑
    radii = [haversine_km(clat, clon, p['lat'], p['lon']) for p in moving]
    mean_r = sum(radii) / len(radii)

    if mean_r < SPOOF_CIRCLE_MIN_RADIUS_KM or mean_r > SPOOF_CIRCLE_MAX_RADIUS_KM:
        return False, {}

    # 變異係數
    variance = sum((r - mean_r) ** 2 for r in radii) / len(radii)
    std_r = math.sqrt(variance)
    cv = std_r / mean_r if mean_r > 0 else 999

    # 計算弧度覆蓋：各點相對於質心的方位角
    angles = sorted(calc_bearing(clat, clon, p['lat'], p['lon'])
                    for p in moving)

    # 計算最大角度間隙 → 覆蓋 = 360 - 最大間隙
    if len(angles) < 2:
        arc_coverage = 0
    else:
        gaps = [angles[i+1] - angles[i] for i in range(len(angles) - 1)]
        gaps.append(360 - angles[-1] + angles[0])  # wrap-around gap
        arc_coverage = 360 - max(gaps)

    is_circle = (cv < SPOOF_CIRCLE_RADIUS_CV and
                 arc_coverage >= SPOOF_CIRCLE_MIN_ARC_DEG)

    details = {
        'center_lat': round(clat, 4),
        'center_lon': round(clon, 4),
        'mean_radius_km': round(mean_r, 3),
        'radius_cv': round(cv, 3),
        'arc_coverage_deg': round(arc_coverage, 1),
        'point_count': len(moving),
    }

    if is_circle:
        # 錨泊迴旋排除：船繞錨點隨潮流迴轉本來就是小半徑近圓軌跡。
        # 半徑 ≤0.6km 且（過半數點回報錨泊/繫泊 或 速度中位數 <2kn）→ 非偽訊號
        if mean_r <= SPOOF_ANCHOR_SWING_RADIUS_KM:
            anc_count = sum(1 for p in moving if p.get('anc'))
            speeds = sorted(p.get('speed', 0) for p in moving)
            median_speed = speeds[len(speeds) // 2]
            if (anc_count * 2 >= len(moving)
                    or median_speed < SPOOF_ANCHOR_SWING_MAX_KNOTS):
                details['skipped_reason'] = 'anchor_swing'
                return False, details

        # 港內排除：港區 GPS 干擾（"crop circles"）產生的圓形軌跡
        # 屬環境干擾而非船隻蓄意偽造，對海纜威脅評分是雜訊
        port = geofence.is_in_port_cached(clat, clon)
        if port:
            details['skipped_reason'] = f'in_port:{port}'
            return False, details

    return is_circle, details


def check_starburst_pattern(track_points, vessel_type='unknown'):
    """
    偵測星爆/蜘蛛網軌跡 (Starburst Pattern)：
    - 多條輻射狀往返自中心點
    - 弧度覆蓋廣（≥270°）但半徑變異大（CV ≥0.35）→ 區別於圓形
    - 大部分點集中於 hub，少數點延伸成輻條
    僅針對非漁船（fishing 直接 return False）
    回傳: (is_starburst, details)
    """
    if vessel_type == 'fishing':
        return False, {}

    moving = [p for p in track_points
              if p.get('lat') is not None and p.get('lon') is not None
              and p.get('speed', 0) > 0.5]

    if len(moving) < STARBURST_MIN_POINTS:
        return False, {}

    clat = sum(p['lat'] for p in moving) / len(moving)
    clon = sum(p['lon'] for p in moving) / len(moving)

    radii = [haversine_km(clat, clon, p['lat'], p['lon']) for p in moving]
    mean_r = sum(radii) / len(radii)
    max_r = max(radii)

    if mean_r < STARBURST_MIN_RADIUS_KM or mean_r > STARBURST_MAX_RADIUS_KM:
        return False, {}

    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in radii) / len(radii))
    cv = std_r / mean_r if mean_r > 0 else 0

    if cv < STARBURST_RADIUS_CV_MIN:
        return False, {}

    bearings = [calc_bearing(clat, clon, p['lat'], p['lon']) for p in moving]

    angles = sorted(bearings)
    gaps = [angles[i+1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append(360 - angles[-1] + angles[0])
    arc_coverage = 360 - max(gaps)

    if arc_coverage < STARBURST_MIN_ARC_DEG:
        return False, {}

    # 輻條偵測：12 個 30° 分箱，計算有多少分箱含「遠端點」(r > 0.5 × max_r)
    spoke_bins = [False] * 12
    far_threshold = 0.5 * max_r
    for b, r in zip(bearings, radii):
        if r >= far_threshold:
            spoke_bins[int(b // 30) % 12] = True
    spoke_count = sum(spoke_bins)

    if spoke_count < STARBURST_MIN_SPOKES:
        return False, {}

    # Hub 集中度：≥30% 的點落在 r < 0.3 × max_r
    hub_threshold = 0.3 * max_r
    hub_count = sum(1 for r in radii if r < hub_threshold)
    hub_fraction = hub_count / len(radii)

    if hub_fraction < STARBURST_HUB_FRACTION:
        return False, {}

    return True, {
        'center_lat': round(clat, 4),
        'center_lon': round(clon, 4),
        'mean_radius_km': round(mean_r, 3),
        'max_radius_km': round(max_r, 3),
        'radius_cv': round(cv, 3),
        'arc_coverage_deg': round(arc_coverage, 1),
        'spoke_count': spoke_count,
        'hub_fraction': round(hub_fraction, 3),
        'point_count': len(moving),
    }


def _local_xy(points, ref_lat, ref_lon):
    """經緯度 → 以 (ref_lat, ref_lon) 為原點的平面公里座標 (east, north)。
    測區跨度僅數十公里，平面近似誤差遠小於 AIS 取樣造成的不確定性。"""
    coslat = math.cos(math.radians(ref_lat))
    return [((p['lon'] - ref_lon) * 111.320 * coslat,
             (p['lat'] - ref_lat) * 110.574) for p in points]


def _gap_hours(p1, p2):
    """兩航跡點的時間間隔（小時）；時戳缺漏或無法解析回傳 None。"""
    try:
        t1 = datetime.fromisoformat(p1['t'].replace('Z', '+00:00'))
        t2 = datetime.fromisoformat(p2['t'].replace('Z', '+00:00'))
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
    return abs((t2 - t1).total_seconds()) / 3600


def _split_into_legs(pts):
    """把航跡切成「測線段」：連續、航向大致一致的移動段。

    回傳: [{'bearing','length_km','start','end','mid_lat','mid_lon',
            'speeds':[...]}, ...]
    """
    segs = []
    for i in range(1, len(pts)):
        p1, p2 = pts[i - 1], pts[i]
        dist = haversine_km(p1['lat'], p1['lon'], p2['lat'], p2['lon'])
        if dist < 0.1:  # 移動不足 100m — 靠港操船/漂流，不構成航段
            continue
        gap = _gap_hours(p1, p2)
        segs.append({
            'bearing': calc_bearing(p1['lat'], p1['lon'], p2['lat'], p2['lon']),
            'dist': dist,
            'p1': p1,
            'p2': p2,
            # 訊號空白：兩端連成的直線是內插產物，不是實際航跡
            'gapped': gap is None or gap > SURVEY_MAX_GAP_HOURS,
        })
    segs = [sg for sg in segs if not sg['gapped']]
    if not segs:
        return []

    legs = []
    cur = [segs[0]]
    for seg in segs[1:]:
        # 與當前測線「起始航向」比較，而非逐段比較：逐段比較會讓緩慢的
        # 弧形轉彎被一路接受，最後併成一條假的長直線。
        # 航跡點不連續（中間被 gap 剔掉）時也要斷開。
        contiguous = seg['p1'] is cur[-1]['p2']
        if contiguous and angular_diff(
                seg['bearing'],
                cur[0]['bearing']) <= SURVEY_LEG_BEARING_TOLERANCE_DEG:
            cur.append(seg)
        else:
            legs.append(cur)
            cur = [seg]
    legs.append(cur)

    out = []
    for group in legs:
        start, end = group[0]['p1'], group[-1]['p2']
        span_km = haversine_km(start['lat'], start['lon'],
                               end['lat'], end['lon'])
        speeds = [g['p1'].get('speed') or 0 for g in group]
        speeds.append(group[-1]['p2'].get('speed') or 0)
        out.append({
            'bearing': calc_bearing(start['lat'], start['lon'],
                                    end['lat'], end['lon']),
            'length_km': span_km,
            'start': start,
            'end': end,
            'mid_lat': (start['lat'] + end['lat']) / 2,
            'mid_lon': (start['lon'] + end['lon']) / 2,
            'speeds': speeds,
        })
    return out


def _axis_mean_deg(bearings):
    """一組航向的「軸向」平均（mod 180°，反向視為同軸）。
    以倍角法做圓形平均，避免 350° 與 10° 平均成 180° 的繞回錯誤。"""
    sx = sum(math.sin(math.radians(b * 2)) for b in bearings)
    sy = sum(math.cos(math.radians(b * 2)) for b in bearings)
    if sx == 0 and sy == 0:
        return None
    return (math.degrees(math.atan2(sx, sy)) / 2) % 180


def _axis_diff(bearing, axis_deg):
    """航向與軸向的夾角（0-90°，反向視為同軸）。"""
    d = abs((bearing % 180) - axis_deg) % 180
    return min(d, 180 - d)


def _is_routine_sweeper(vessel_type, names):
    """判斷是否為「來回平行掃是本業」的船 —— 測線判定應排除。

    只看 AIS 船種碼不夠：實測大量福建漁船（MINDONGYU63179 等）廣播成
    other/unknown/cargo/tanker，其 2-4kn 的拖網作業與測線幾乎無法從幾何上
    區分（全船隊掃描時 3% 命中，前 25 名全是漁船）。因此加上船名判定。

    注意 is_cn_fishing_vessel() 的省份前綴含 `^XIANG`（湘），會誤中
    XIANG YANG HONG（向陽紅）——公務/科研分類優先，不受船名規則影響。
    """
    if vessel_type in GOV_TYPE_NAMES:
        return False
    if vessel_type in SURVEY_EXCLUDE_TYPES:
        return True
    return any(is_cn_fishing_vessel(n) for n in (names or []) if n)


def check_survey_pattern(track_points, vessel_type='unknown', names=None):
    """偵測割草式測線（lawnmower / survey grid）—— 平行、等間距、來回的測繪樣態。

    判定條件（皆須成立）：
      1. 非「來回掃是本業」的船（漁船／客輪／高速客船；漁船另以船名判定，
         見 _is_routine_sweeper）
      2. ≥SURVEY_MIN_LEGS 條長度 ≥SURVEY_MIN_LEG_KM 的測線
      3. 各測線平行：相對主軸夾角 ≤SURVEY_AXIS_TOLERANCE_DEG，且同軸測線
         佔測區全部測線的 ≥SURVEY_MIN_PARALLEL_FRACTION
      4. ≥SURVEY_MIN_REVERSALS 次反向（相鄰測線航向差 180°±容差）
      5. 去重後 ≥SURVEY_MIN_LINES 條不同平行線，線距落在
         [SURVEY_MIN_SPACING_KM, SURVEY_MAX_SPACING_KM] 且變異係數
         ≤SURVEY_SPACING_CV_MAX（等間距）
      6. 測線速度中位數 ≤SURVEY_MAX_MEDIAN_KNOTS
      7. 時間跨度 ≥SURVEY_MIN_SPAN_HOURS
    港內點排除（港區操船會產生短的平行來回）。
    回傳: (is_survey, details)
    """
    if _is_routine_sweeper(vessel_type, names):
        return False, {}

    pts = [p for p in track_points
           if p.get('lat') is not None and p.get('lon') is not None
           and not p.get('in_port')]
    if len(pts) < 4:
        return False, {}

    legs = [lg for lg in _split_into_legs(pts)
            if lg['length_km'] >= SURVEY_MIN_LEG_KM]
    if len(legs) < SURVEY_MIN_LEGS:
        return False, {}

    # 依測區分群後逐區判定，取樣態最完整（測線數最多）的一區
    best, best_details = False, {}
    for box in _cluster_legs_by_box(legs):
        if len(box) < SURVEY_MIN_LEGS:
            continue
        ok, details = _evaluate_survey_legs(box)
        if ok and details['leg_count'] > best_details.get('leg_count', 0):
            best, best_details = True, details
    return best, best_details


def _cluster_legs_by_box(legs, link_km=SURVEY_BOX_LINK_KM):
    """依測線中點做單一鏈結分群 → 各「測區」的測線清單（維持時間順序）。"""
    n = len(legs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if haversine_km(legs[i]['mid_lat'], legs[i]['mid_lon'],
                            legs[j]['mid_lat'], legs[j]['mid_lon']) <= link_km:
                parent[find(j)] = find(i)

    boxes = {}
    for i in range(n):
        boxes.setdefault(find(i), []).append(legs[i])
    return list(boxes.values())


def _evaluate_survey_legs(legs):
    """對單一測區的測線清單做平行 / 反向 / 等間距判定。"""
    # ── 主軸：以測線長度加權（長測線才是測區主方向）──
    weighted = []
    for lg in legs:
        weighted.extend([lg['bearing']] * max(1, int(lg['length_km'])))
    axis = _axis_mean_deg(weighted)
    if axis is None:
        return False, {}

    parallel = [lg for lg in legs
                if _axis_diff(lg['bearing'], axis) <= SURVEY_AXIS_TOLERANCE_DEG]
    if len(parallel) < SURVEY_MIN_LEGS:
        return False, {}
    # 同軸里程佔比：測線作業是有計畫的來回，絕大部分航程都在主軸上；
    # 四處亂走的低速漂移雖也湊得出幾條等間距平行線，同軸里程卻只佔少數
    total_km = sum(lg['length_km'] for lg in legs)
    parallel_km = sum(lg['length_km'] for lg in parallel)
    parallel_fraction = parallel_km / total_km if total_km else 0
    if parallel_fraction < SURVEY_MIN_PARALLEL_FRACTION:
        return False, {}

    # ── 反向次數：相鄰測線是否 180° 掉頭 ──
    reversals = 0
    for i in range(1, len(parallel)):
        delta = angular_diff(parallel[i]['bearing'], parallel[i - 1]['bearing'])
        if delta >= 180 - SURVEY_REVERSAL_TOLERANCE_DEG:
            reversals += 1
    if reversals < SURVEY_MIN_REVERSALS:
        return False, {}

    # ── 線距：測線中點對主軸的垂距，去重後看間距是否規律 ──
    ref_lat = sum(lg['mid_lat'] for lg in parallel) / len(parallel)
    ref_lon = sum(lg['mid_lon'] for lg in parallel) / len(parallel)
    mids = [{'lat': lg['mid_lat'], 'lon': lg['mid_lon']} for lg in parallel]
    xy = _local_xy(mids, ref_lat, ref_lon)
    th = math.radians(axis)
    # 主軸單位向量 (sinθ, cosθ)；垂直分量 = x·cosθ − y·sinθ
    offsets = sorted(x * math.cos(th) - y * math.sin(th) for x, y in xy)

    mean_leg_km = sum(lg['length_km'] for lg in parallel) / len(parallel)

    # ── 樣態 A：階梯式網格（不同平行線 + 等間距）──
    # 同一條線上的多次通過合併（避免 diff≈0 灌爆變異係數）
    lines = [offsets[0]]
    for o in offsets[1:]:
        if o - lines[-1] >= SURVEY_MIN_SPACING_KM:
            lines.append(o)

    survey_type = None
    mean_sp = cv = 0.0
    if len(lines) >= SURVEY_MIN_LINES:
        spacings = [lines[i] - lines[i - 1] for i in range(1, len(lines))]
        mean_sp = sum(spacings) / len(spacings)
        var = sum((sp - mean_sp) ** 2 for sp in spacings) / len(spacings)
        cv = math.sqrt(var) / mean_sp if mean_sp else 99
        if (SURVEY_MIN_SPACING_KM <= mean_sp <= SURVEY_MAX_SPACING_KM
                and cv <= SURVEY_SPACING_CV_MAX
                and mean_leg_km >= SURVEY_GRID_MIN_MEAN_LEG_KM):
            survey_type = 'grid'

    # ── 樣態 B：重複測線（反覆重走同一條線）──
    # 垂距的中位絕對偏差：一條進出測區的轉場航段不會把整體判定帶偏
    med_off = offsets[len(offsets) // 2]
    devs = sorted(abs(o - med_off) for o in offsets)
    offset_mad = devs[len(devs) // 2]
    if (survey_type is None
            and offset_mad <= SURVEY_TRANSECT_SPREAD_KM
            and mean_leg_km >= SURVEY_TRANSECT_MIN_LEG_KM):
        survey_type = 'repeat_transect'

    if survey_type is None:
        return False, {}

    # ── 速度中位數 ──
    all_speeds = sorted(s for lg in parallel for s in lg['speeds'])
    median_speed = all_speeds[len(all_speeds) // 2] if all_speeds else 0
    if median_speed > SURVEY_MAX_MEDIAN_KNOTS:
        return False, {}

    # ── 時間跨度 ──
    times = []
    for lg in parallel:
        for key in ('start', 'end'):
            try:
                times.append(datetime.fromisoformat(
                    lg[key]['t'].replace('Z', '+00:00')))
            except (ValueError, KeyError, AttributeError, TypeError):
                continue
    if len(times) < 2:
        return False, {}
    span_hours = (max(times) - min(times)).total_seconds() / 3600
    if span_hours < SURVEY_MIN_SPAN_HOURS:
        return False, {}

    return True, {
        'survey_type': survey_type,
        'leg_count': len(parallel),
        'parallel_fraction': round(parallel_fraction, 2),
        'line_count': len(lines),
        'reversals': reversals,
        'axis_deg': round(axis, 1),
        'mean_spacing_km': round(mean_sp, 2),
        'spacing_cv': round(cv, 2),
        'offset_mad_km': round(offset_mad, 2),
        'median_speed_kn': round(median_speed, 1),
        'span_hours': round(span_hours, 1),
        'mean_leg_km': round(mean_leg_km, 1),
        'center_lat': round(ref_lat, 4),
        'center_lon': round(ref_lon, 4),
    }


def check_offshore_loitering(track_points, vessel_type):
    """偵測商船的離岸長期徘徊（影子船隊待命樣態，與海纜無關）。

    條件（皆須成立）：
      - 船型為 tanker/cargo/lng（大噸位、具儲運能力）
      - 過半數航跡點低速（<OFFSHORE_LOITER_MAX_KNOTS）
      - 低速點距中心的**中位半徑** ≤OFFSHORE_LOITER_MEDIAN_RADIUS_KM（原地打轉，
        用中位數對少數遠端 excursion 穩健）
      - 低速點的**連續跨度** ≥OFFSHORE_LOITER_DAYS 天（gap>1天即斷開重算）
      - 港內點排除（靠泊不算）
    回傳: (is_loiter, details)
    """
    if vessel_type not in OFFSHORE_LOITER_TYPES:
        return False, {}

    pts = [p for p in track_points
           if p.get('lat') is not None and p.get('lon') is not None
           and not p.get('in_port')]
    if len(pts) < 4:
        return False, {}

    slow = [p for p in pts if p.get('speed', 99) < OFFSHORE_LOITER_MAX_KNOTS]
    low_frac = len(slow) / len(pts)
    if low_frac < OFFSHORE_LOITER_MIN_LOW_FRAC or len(slow) < 4:
        return False, {}

    # 低速點的中位半徑（距中心）
    clat = sum(p['lat'] for p in slow) / len(slow)
    clon = sum(p['lon'] for p in slow) / len(slow)
    radii = sorted(haversine_km(clat, clon, p['lat'], p['lon']) for p in slow)
    median_radius = radii[len(radii) // 2]
    if median_radius > OFFSHORE_LOITER_MEDIAN_RADIUS_KM:
        return False, {}

    # 低速點的最長連續跨度（相鄰間隔 >1 天即斷開）
    times = []
    for p in slow:
        try:
            times.append(datetime.fromisoformat(p['t'].replace('Z', '+00:00')))
        except (ValueError, KeyError, AttributeError):
            continue
    times.sort()
    max_span_days = 0.0
    seg_start = prev = None
    for t in times:
        if seg_start is None:
            seg_start = prev = t
            continue
        if (t - prev).total_seconds() / 3600 > 24:
            seg_start = t
        else:
            max_span_days = max(
                max_span_days, (t - seg_start).total_seconds() / 86400)
        prev = t

    is_loiter = max_span_days >= OFFSHORE_LOITER_DAYS
    return is_loiter, {
        'loiter_days': round(max_span_days, 1),
        'low_speed_fraction': round(low_frac, 2),
        'median_radius_km': round(median_radius, 1),
        'center_lat': round(clat, 4),
        'center_lon': round(clon, 4),
        'point_count': len(pts),
    }


def split_loiter_runs(slow_points, max_gap_hours=LOITER_MAX_GAP_HOURS):
    """把海纜旁低速點序列依時間間隔切成連續 runs（純函式，供單元測試）。

    slow_points: [(datetime, lat, lon, speed_kn), ...]，未必已排序。
    相鄰點間隔 > max_gap_hours 即斷開 — 船中途離開再回來不能累計成一段長徘徊。
    回傳依時間排序的 run list（每個 run 是點的 list）。
    """
    if not slow_points:
        return []
    pts = sorted(slow_points, key=lambda p: p[0])
    runs = [[pts[0]]]
    for p in pts[1:]:
        gap = (p[0] - runs[-1][-1][0]).total_seconds() / 3600
        if gap > max_gap_hours:
            runs.append([p])
        else:
            runs[-1].append(p)
    return runs


def build_loiter_events(runs, min_hours=CABLE_LOITER_HOURS, max_events=5):
    """從 split_loiter_runs 的結果組出徘徊事件（純函式）。

    只收跨度 ≥ min_hours 的 run；依時數降冪排序、cap max_events。
    aggregate_highrisk.py 用事件的中心座標統計熱區、avg_speed_kn 統計
    「海纜高風險滯留期間平均船速」— 這些資訊原本在此被丟棄。
    """
    events = []
    for run in runs:
        if len(run) < 2:
            continue
        hours = (run[-1][0] - run[0][0]).total_seconds() / 3600
        if hours < min_hours:
            continue
        speeds = [p[3] for p in run if isinstance(p[3], (int, float))]
        events.append({
            'start': run[0][0].isoformat(),
            'end': run[-1][0].isoformat(),
            'hours': round(hours, 1),
            'center_lat': round(sum(p[1] for p in run) / len(run), 4),
            'center_lon': round(sum(p[2] for p in run) / len(run), 4),
            'avg_speed_kn': round(sum(speeds) / len(speeds), 1) if speeds else None,
            'min_speed_kn': round(min(speeds), 1) if speeds else None,
            'points': len(run),
        })
    events.sort(key=lambda e: -e['hours'])
    return events[:max_events]


def check_cable_proximity(track_points):
    """
    檢查船隻航跡是否經過海纜附近
    同時偵測低速徘徊（<5kn 在海纜 5km 內、連續超過 3 小時）
    港內點不計入 — 海纜登陸點鄰近港口，靠泊/錨泊屬例行活動。
    回傳: (is_near, details)
    """
    cables = load_cable_segments()
    if not cables:
        return False, {}

    # 計算船隻航跡的 bounding box，用於快速排除不相關的海纜
    # 港內點直接跳過：正常靠泊會被海纜登陸段誤判為鄰近/徘徊
    valid_pts = [p for p in track_points
                 if p.get('lat') is not None and p.get('lon') is not None
                 and not p.get('in_port')]
    if not valid_pts:
        return False, {}
    # CABLE_PROXIMITY_KM ≈ 0.045° buffer at equator, use 0.06° for safety
    bbox_buf = 0.06
    tk_lat_min = min(p['lat'] for p in valid_pts) - bbox_buf
    tk_lat_max = max(p['lat'] for p in valid_pts) + bbox_buf
    tk_lon_min = min(p['lon'] for p in valid_pts) - bbox_buf
    tk_lon_max = max(p['lon'] for p in valid_pts) + bbox_buf

    # 預篩選：只保留 bbox 與航跡重疊的海纜
    nearby_cables = [c for c in cables
                     if c['bbox'][0] <= tk_lat_max and c['bbox'][2] >= tk_lat_min
                     and c['bbox'][1] <= tk_lon_max and c['bbox'][3] >= tk_lon_min]

    near_cables = set()
    min_dist = float('inf')
    near_count = 0
    loiter_slow_points = []  # 海纜鄰近且低速的 (時間, lat, lon, speed)

    for pt in valid_pts:
        plat = pt['lat']
        plon = pt['lon']

        is_near_cable = False
        for cable in nearby_cables:
            points = cable['points']
            for i in range(len(points) - 1):
                dist = point_to_segment_distance_km(
                    plat, plon,
                    points[i][0], points[i][1],
                    points[i+1][0], points[i+1][1]
                )
                if dist < min_dist:
                    min_dist = dist
                if dist <= CABLE_PROXIMITY_KM:
                    near_cables.add(cable['slug'])
                    near_count += 1
                    is_near_cable = True
                    break
            if is_near_cable:
                break

        # 記錄低速徘徊點（海纜鄰近 + 速度 < 5 knots）
        if is_near_cable and pt.get('speed', 99) < CABLE_LOITER_MAX_KNOTS:
            ts = pt.get('t', '')
            if ts:
                try:
                    t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    t = None
                if t is not None:
                    loiter_slow_points.append((t, plat, plon, pt.get('speed')))

    # 從實際時間戳計算「最長連續」徘徊時數 —
    # 相鄰慢速時間戳間隔 > LOITER_MAX_GAP_HOURS 即斷開（船中途離開再回來
    # 不能累計成一段長徘徊）。loiter_slow_hours 與 loiter_events 由同一批
    # runs 導出，兩者不會打架。
    runs = split_loiter_runs(loiter_slow_points)
    loiter_hours = max(
        ((r[-1][0] - r[0][0]).total_seconds() / 3600
         for r in runs if len(r) >= 2), default=0.0)
    loiter_events = build_loiter_events(runs)
    # 合格徘徊段（≥3h）全部點的 SOG 均值 — 週報「海纜滯留期間平均船速」
    qual_speeds = [
        p[3]
        for r in runs
        if len(r) >= 2
        and (r[-1][0] - r[0][0]).total_seconds() / 3600 >= CABLE_LOITER_HOURS
        for p in r if isinstance(p[3], (int, float))]
    loiter_avg_speed = (round(sum(qual_speeds) / len(qual_speeds), 1)
                        if qual_speeds else None)
    is_loitering = loiter_hours >= CABLE_LOITER_HOURS

    is_near = len(near_cables) > 0
    return is_near, {
        'cables_nearby': list(near_cables),
        'min_distance_km': round(min_dist, 2) if min_dist < float('inf') else None,
        'proximity_points': near_count,
        'loiter_slow_hours': round(loiter_hours, 1),
        'loiter_triggered': is_loitering,
        'loiter_events': loiter_events,
        'loiter_avg_speed_kn': loiter_avg_speed,
    }


def check_zigzag_pattern(track_points):
    """
    檢測 Z 字型移動模式（頻繁大幅改變航向）
    使用 calc_bearing() 從實際位置計算航向，避免依賴可能不準確的 AIS heading。
    排除錨泊/繫泊與近乎靜止的點 — 錨泊船隨潮流擺動，2h 快照間可漂移
    100-300m，方位近乎隨機，會被誤判為連續大幅轉向。
    回傳: (is_zigzag, details)
    """
    if len(track_points) < 4:
        return False, {}

    # 過濾錨泊(anc)與近靜止點，再從連續位置計算實際航向
    moving = [p for p in track_points
              if not p.get('anc')
              and p.get('speed', 0) >= STATIONARY_MAX_KNOTS]

    if len(moving) < 4:
        return False, {}

    bearings = []  # (bearing, 該段起點回報速度)
    for i in range(1, len(moving)):
        p1, p2 = moving[i - 1], moving[i]
        lat1, lon1 = p1.get('lat'), p1.get('lon')
        lat2, lon2 = p2.get('lat'), p2.get('lon')
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            continue
        dist = haversine_km(lat1, lon1, lat2, lon2)
        if dist < 0.1:  # 移動不足 100m，跳過
            continue
        bearings.append((calc_bearing(lat1, lon1, lat2, lon2),
                         p2.get('speed', 0)))

    if len(bearings) < 4:
        return False, {}

    # 計算航向變化；同時統計低速（≤7kn，可能下錨）狀態下的轉向次數，
    # 供「海纜鄰近 + Z字型 = 拖錨」組合判斷 — 高速轉向不可能是拖錨
    turns = 0
    turns_below_drag_speed = 0
    heading_changes = []
    for i in range(1, len(bearings)):
        delta = angular_diff(bearings[i][0], bearings[i - 1][0])
        heading_changes.append(delta)
        if delta >= ZIGZAG_HEADING_CHANGE_DEG:
            turns += 1
            if bearings[i][1] <= ANCHOR_DRAG_MAX_KNOTS:
                turns_below_drag_speed += 1

    avg_change = sum(heading_changes) / len(heading_changes) if heading_changes else 0
    is_zigzag = turns >= ZIGZAG_MIN_TURNS

    return is_zigzag, {
        'turn_count': turns,
        'turns_below_drag_speed': turns_below_drag_speed,
        'avg_heading_change': round(avg_change, 1),
        'threshold': f'>={ZIGZAG_MIN_TURNS} turns of >={ZIGZAG_HEADING_CHANGE_DEG}°',
    }


def check_depth_200m_activity(track_points):
    """
    檢查船隻是否在 200m 等深線附近活動
    回傳: (is_near_contour, details)
    """
    if not track_points:
        return False, {}

    near_count = 0
    min_dist = float('inf')

    for pt in track_points:
        plat = pt.get('lat')
        plon = pt.get('lon')
        if plat is None or plon is None:
            continue

        # 計算到等深線各段的最短距離
        for i in range(len(DEPTH_200M_CONTOUR) - 1):
            dist = point_to_segment_distance_km(
                plat, plon,
                DEPTH_200M_CONTOUR[i][0], DEPTH_200M_CONTOUR[i][1],
                DEPTH_200M_CONTOUR[i+1][0], DEPTH_200M_CONTOUR[i+1][1]
            )
            if dist < min_dist:
                min_dist = dist
            if dist <= DEPTH_200M_CONTOUR_KM:
                near_count += 1
                break

    total = len([p for p in track_points if p.get('lat') is not None])
    ratio = near_count / max(total, 1)
    is_near = ratio >= 0.3  # 30% 以上的時間在等深線附近

    return is_near, {
        'contour_proximity_ratio': round(ratio, 3),
        'contour_points': near_count,
        'total_points': total,
        'min_distance_km': round(min_dist, 2) if min_dist < float('inf') else None,
    }


def analyze_ais_anomalies(profile, identity_events=None):
    """
    AIS 異常偵測
    - 多次變更船名
    - Going dark（AIS 訊號消失再出現）
    - 身分變更事件（來自 identity_events.json）
    """
    anomalies = []

    # 船名變更偵測
    name_count = len(profile.get('names_seen', []))
    if name_count >= NAME_CHANGE_THRESHOLD:
        anomalies.append({
            'type': 'name_change',
            'description': f'使用 {name_count} 個不同船名',
            'names': profile['names_seen'],
            'severity': 'high' if name_count >= 5 else 'medium'
        })

    # Going dark 偵測
    timestamps = profile.get('last_seen_timestamps', [])
    dark_events = 0
    if len(timestamps) >= 2:
        for i in range(1, len(timestamps)):
            try:
                t1 = datetime.fromisoformat(timestamps[i-1].replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(timestamps[i].replace('Z', '+00:00'))
                gap_hours = (t2 - t1).total_seconds() / 3600
                if gap_hours > GOING_DARK_GAP_HOURS:
                    dark_events += 1
            except (ValueError, KeyError, AttributeError):
                continue

    if dark_events > 0:
        anomalies.append({
            'type': 'going_dark',
            'description': f'AIS 訊號消失 {dark_events} 次',
            'count': dark_events,
            'severity': 'high' if dark_events >= 3 else 'medium'
        })

    # 船型變更偵測
    types_seen = profile.get('types_seen', [])
    real_types = [t for t in types_seen if t not in ('unknown', 'other')]
    if len(real_types) >= 2:
        anomalies.append({
            'type': 'type_change',
            'description': f'船型變更: {" → ".join(real_types)}',
            'types': real_types,
            'severity': 'medium'
        })

    # 身分變更事件偵測
    if identity_events:
        event_count = len(identity_events)
        has_multi = any(ev.get('multi_field') for ev in identity_events)

        if event_count > 0:
            severity = 'high' if event_count >= 3 or has_multi else 'medium'
            field_changes = []
            for ev in identity_events:
                for ch in ev.get('changes', []):
                    field_changes.append(f"{ch['field']}: {ch['old']} → {ch['new']}")
            anomalies.append({
                'type': 'identity_change',
                'description': f'7 天內 {event_count} 次身分變更',
                'count': event_count,
                'multi_field': has_multi,
                'details': field_changes[:10],
                'severity': severity,
            })

    return anomalies


def load_identity_events():
    """載入身分變更事件，按 MMSI 分組，僅保留近 7 天"""
    if not IDENTITY_EVENTS_FILE.exists():
        return {}
    try:
        with open(IDENTITY_EVENTS_FILE, 'r', encoding='utf-8') as f:
            events = json.load(f)
    except Exception:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    by_mmsi = {}
    for ev in events:
        try:
            ts = datetime.fromisoformat(ev['timestamp'].replace('Z', '+00:00'))
            if ts < cutoff:
                continue
        except (ValueError, KeyError):
            continue
        mmsi = ev.get('mmsi', '')
        if mmsi:
            by_mmsi.setdefault(mmsi, []).append(ev)
    return by_mmsi


def load_sanctions_list():
    """載入制裁船舶清單，回傳 (by_imo, imo_set, name_set)。

    兩個來源合併：
      1. UN 1718 清單（`un_sanctions_vessels.json`，~27 艘）—— IMO **與船名**皆可比對。
      2. 多機構影子船隊黑名單（`sanctions_blacklist.json`，1400+ 艘 OFAC/EU/UANI…）
         —— **僅以 IMO 比對**（船名不進 name_set：1400+ 筆含大量常見船名，
         純名稱易撞名灌分）。黑名單條目標記 source='blacklist' + programs。
    """
    by_imo = {}
    imo_set = set()
    name_set = set()

    # 1) UN 清單（IMO + 名稱）
    if SANCTIONS_FILE.exists():
        try:
            with open(SANCTIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for v in data.get('vessels', []):
                imo = v.get('imo', '')
                name = v.get('name', '').upper().strip()
                if imo:
                    imo_set.add(imo)
                    by_imo.setdefault(imo, dict(v, source='un'))
                if name:
                    name_set.add(name)
        except Exception as e:
            print(f"⚠️ 載入 UN 制裁清單失敗: {e}")

    # 2) 多機構黑名單（僅 IMO；UN 已收錄的 IMO 不覆蓋）
    if SANCTIONS_BLACKLIST_FILE.exists():
        try:
            with open(SANCTIONS_BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                bl = json.load(f)
            n = 0
            for v in bl.get('vessels', []):
                imo = (v.get('imo') or '').strip()
                if imo and imo not in by_imo:
                    imo_set.add(imo)
                    by_imo[imo] = dict(v, source='blacklist')
                    n += 1
            print(f"🚫 制裁黑名單: +{n} 艘（IMO 比對）")
        except Exception as e:
            print(f"⚠️ 載入制裁黑名單失敗: {e}")

    return by_imo, imo_set, name_set


def load_ship_transfers():
    """
    載入 ship_transfers.json，回傳 dict: {mmsi: {'count': N, 'suspicious': bool}}
    用於可疑計分中的 STS 旁靠加分。
    """
    if not SHIP_TRANSFERS_FILE.exists():
        print("⚠️ ship_transfers.json 不存在，跳過 STS 加分")
        return {}

    try:
        with open(SHIP_TRANSFERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}

    sts_map = {}  # mmsi -> {count, suspicious}
    all_events = (data.get('active_transfers', [])
                  + data.get('history', []))

    for ev in all_events:
        is_susp = ev.get('classification') == 'suspicious'
        for vkey in ('vessel1', 'vessel2'):
            mmsi = str(ev.get(vkey, {}).get('mmsi', ''))
            if not mmsi:
                continue
            if mmsi not in sts_map:
                sts_map[mmsi] = {'count': 0, 'suspicious': False}
            sts_map[mmsi]['count'] += 1
            if is_susp:
                sts_map[mmsi]['suspicious'] = True

    print(f"🚢 STS 旁靠紀錄: {len(sts_map)} 艘船 "
          f"({sum(1 for v in sts_map.values() if v['suspicious'])} 艘涉及可疑旁靠)")
    return sts_map


def load_gov_formations():
    """載入 gov_formations.json 的 vessel_index → {mmsi: {...}}。

    由 detect_gov_formation.py 產出（公務船編隊事件）。檔案不存在時回傳空
    dict，計分自動略過此項（管線任一步失敗不應讓整體分析崩掉）。
    """
    if not GOV_FORMATIONS_FILE.exists():
        print("⚠️ gov_formations.json 不存在，跳過公務船編隊加分")
        return {}
    try:
        with open(GOV_FORMATIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    idx = data.get('vessel_index', {}) or {}
    escort = sum(1 for v in idx.values() if v.get('escorted_research'))
    print(f"🛡️ 公務船編隊: {len(idx)} 艘涉入（{escort} 艘屬護航科考）")
    return idx


def load_itu_mars_cache():
    """
    載入 ITU MARS 快取資料（由 lookup_itu_mars.py 建立）。
    回傳 dict: {mmsi: {ship_name, call_sign, administration, imo_number, ...}}
    """
    if not ITU_MARS_CACHE.exists():
        return {}
    try:
        with open(ITU_MARS_CACHE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        found = {k: v for k, v in data.items() if v.get('found')}
        print(f"🏛️ ITU MARS 快取: {len(found)} 筆有效記錄")
        return found
    except Exception:
        return {}


def check_itu_mars_mismatch(profile, mars_record):
    """
    比對 AIS 回報資訊 vs ITU MARS 官方登記資料。
    偵測船名不符、管理國/船旗不一致等身分偽造徵兆。
    回傳: (has_mismatch, details)
    """
    if not mars_record or not mars_record.get('found'):
        return False, {}

    mismatches = []
    details = {'mars_record': mars_record}

    # 船名比對
    mars_name = (mars_record.get('ship_name') or '').upper().strip()
    ais_names = [n.upper().strip() for n in profile.get('names_seen', []) if n]

    if mars_name and ais_names:
        # 若 AIS 使用的所有船名都與 MARS 登記名不同
        if mars_name not in ais_names:
            mismatches.append({
                'field': 'ship_name',
                'mars': mars_name,
                'ais': ais_names,
                'description': f'船名不符：登記 {mars_name}，AIS 使用 {", ".join(ais_names[:3])}'
            })

    # 管理國 vs MMSI MID 比對
    mars_admin = (mars_record.get('administration') or '').strip()
    mmsi = profile.get('mmsi', '')
    if mars_admin and len(mmsi) >= 3:
        # MID 前三碼對應的常見管理國縮寫
        # 簡單比對：MARS administration 應與 MMSI MID 對應的國家一致
        # 這裡記錄供人工判讀，不自動判定是否不符
        details['mars_administration'] = mars_admin

    # IMO 比對
    mars_imo = (mars_record.get('imo_number') or '').strip()
    ais_imo = (profile.get('last_imo') or '').strip()
    if mars_imo and ais_imo and mars_imo != ais_imo:
        mismatches.append({
            'field': 'imo_number',
            'mars': mars_imo,
            'ais': ais_imo,
            'description': f'IMO不符：登記 {mars_imo}，AIS 回報 {ais_imo}'
        })

    # 呼號比對
    mars_cs = (mars_record.get('call_sign') or '').upper().strip()
    ais_cs = (profile.get('last_callsign') or '').upper().strip()
    if mars_cs and ais_cs and mars_cs != ais_cs:
        mismatches.append({
            'field': 'call_sign',
            'mars': mars_cs,
            'ais': ais_cs,
            'description': f'呼號不符：登記 {mars_cs}，AIS 回報 {ais_cs}'
        })

    details['mismatches'] = mismatches
    return len(mismatches) > 0, details


def load_track_history():
    """載入 tier-1 + tier-2 航跡，按 MMSI 組織航跡"""
    tracks = {}  # mmsi -> [points]

    # Tier-1: CN fishing + suspicious
    # Tier-2: cargo, tanker, LNG, identity-changed
    track_sources = [
        ("tier-1", [TRACK_HISTORY_FILE]),
        ("tier-2", [TRACK_COMMERCIAL_FILE]),
    ]

    for tier_label, candidates in track_sources:
        for path in candidates:
            if path.exists():
                print(f"📂 Reading {tier_label} track history: {path}")
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry in data:
                    ts = entry.get('timestamp', '')
                    for v in entry.get('vessels', []):
                        mmsi = v.get('mmsi')
                        if not mmsi:
                            continue
                        if mmsi not in tracks:
                            tracks[mmsi] = []
                        pt = {
                            't': ts,
                            'lat': v.get('lat'),
                            'lon': v.get('lon'),
                            'speed': v.get('speed', 0),
                            'heading': v.get('heading'),
                            # 船名：偵測 MMSI 共用（瞬移偽訊號誤判排除）
                            'name': v.get('name'),
                        }
                        # 船型/公務類別：vessel_profiles.json 是 Actions 快取，
                        # 冷啟動時會是空的；沒有這個 fallback，船型會全變
                        # unknown，測線判定的漁船排除也就整個失效
                        # （實測冷快取下誤報從 13 艘暴增到 1702 艘）
                        if v.get('type_name'):
                            pt['type_name'] = v['type_name']
                        if v.get('gov'):
                            pt['gov'] = v['gov']
                        # 錨泊/繫泊旗標（nav_status 1/5，fetch_ais_data 寫入）
                        if v.get('anc'):
                            pt['anc'] = 1
                        tracks[mmsi].append(pt)
                break  # found this tier, skip fallback path
        else:
            print(f"⚠️ {tier_label} track history not found")

    print(f"📊 Track history: {len(tracks)} vessels")
    return tracks


def is_recently_active(profile, has_track, now=None):
    """判斷船隻是否「近期活躍」（近 ANALYSIS_ACTIVE_DAYS 天內出現過）。

    有航跡點（tier-1/tier-2 皆為 14/28 天滾動）即視為活躍；
    否則看 profile 最後一次出現時間。早已離開監測海域的船
    （只剩 90 天 profile）跳過分析，避免舊資料灌水統計。
    """
    if has_track:
        return True
    timestamps = profile.get('last_seen_timestamps') or []
    if not timestamps:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        last_seen = datetime.fromisoformat(
            timestamps[-1].replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return False
    return (now - last_seen) <= timedelta(days=ANALYSIS_ACTIVE_DAYS)


def annotate_port_points(track_points):
    """對每個航跡點標註 in_port（台灣港口 2km / 大陸港灣 8km+，見 geofence）。
    港內的靠泊/錨泊/操船屬例行活動，海纜鄰近、徘徊、緩衝帶加分皆應排除。"""
    for pt in track_points:
        lat, lon = pt.get('lat'), pt.get('lon')
        if lat is None or lon is None:
            continue
        port = geofence.is_in_port_cached(lat, lon)
        if port:
            pt['in_port'] = port


def classify_vessel(profile, track_points, identity_events=None,
                     sanctions_match=None, mars_record=None,
                     sts_record=None, formation_record=None):
    """
    綜合分類單一船隻的可疑程度
    標準：海纜鄰近 + Z字型 + 200m等深線 + AIS變更 + UN制裁
          + AIS偽訊號 + ITU MARS 登記比對 + STS旁靠
          + 割草式測線 + 公務船編隊
    分數依船型乘數調整：商船 ×1.0、漁船 ×0.2、其他 ×0.5
    """
    mmsi = profile['mmsi']

    # vessel_profiles.json 是 Actions 快取備援的，冷啟動時可能整份是空的
    # （classify_vessel 的船型判定早已為此準備 track fallback）。船名同理：
    # 沒有 profile 名稱時改用航跡點上的船名，否則整份報表的船名欄全是空的。
    names = profile.get('names_seen', [])
    if not names:
        track_names = []
        for p in reversed(track_points or []):
            n = (p.get('name') or '').strip()
            if n and n not in track_names:
                track_names.append(n)
                if len(track_names) >= 2:
                    break
        names = track_names

    classification = {
        'mmsi': mmsi,
        'names': names,
        'total_snapshots': profile.get('total_snapshots', 0),
        'cable_proximity': False,
        'cable_loitering': False,
        'non_top10_flag': False,
        'zigzag_pattern': False,
        'depth_200m_activity': False,
        'sanctioned': False,
        'spoof_impossible_physics': False,
        'spoof_box_pattern': False,
        'spoof_circle_pattern': False,
        'spoof_starburst_pattern': False,
        'spoof_starburst_details': {},
        'itu_mars_mismatch': False,
        'survey_pattern': False,
        'gov_formation': False,
        'ais_anomalies': [],
        'risk_level': 'normal',
        'flags': [],
    }

    # ── 排除規則檢查（漁網、浮標、信標等非船舶設備）──
    # 提前檢查，避免對非船舶設備執行昂貴分析
    all_names = profile.get('names_seen', [])
    excluded, matched_rules = check_exclusion_rules(mmsi, all_names)
    classification['excluded'] = excluded
    if excluded:
        if matched_rules:
            classification['exclusion_rules'] = matched_rules
        reasons = ' + '.join(r['label'] for r in matched_rules)
        classification['risk_level'] = 'normal'
        classification['risk_score'] = 0
        classification['raw_score'] = 0
        classification['suspicious'] = False
        classification['vessel_type'] = 'unknown'
        classification['type_multiplier'] = 0
        classification['flags'] = [f'排除: {reasons}']
        return classification

    # ── 港內點標註（供海纜鄰近/徘徊/緩衝帶排除）──
    if track_points:
        annotate_port_points(track_points)

    # ── 台灣船隻排除（非監測對象；本監測針對大陸灰色地帶活動）──
    # 1. 船旗國為台灣：MMSI MID 416 開頭
    # 2. 停泊台灣港內：最後位置位於台灣港（geofence.PORTS；大陸港不觸發）
    # 安全閥：有身分變更事件或命中 UN 制裁名單者不排除、照常分析 —
    # 防止大陸船偽冒台灣 MMSI 躲過偵測（實務上有案例）。
    if not identity_events and not sanctions_match:
        tw_rules = []
        if mmsi.startswith('416'):
            tw_rules.append({'id': 'flag_taiwan',
                             'label': '台灣船旗 (MID 416)'})
        if track_points:
            last_port = track_points[-1].get('in_port')
            if last_port and last_port in geofence.PORTS:
                tw_rules.append({'id': 'moored_taiwan_port',
                                 'label': f'停泊台灣港內 ({last_port})'})
        if tw_rules:
            reasons = ' + '.join(r['label'] for r in tw_rules)
            classification['excluded'] = True
            classification['exclusion_rules'] = tw_rules
            classification['risk_level'] = 'normal'
            classification['risk_score'] = 0
            classification['raw_score'] = 0
            classification['suspicious'] = False
            classification['vessel_type'] = 'unknown'
            classification['type_multiplier'] = 0
            classification['flags'] = [f'排除: {reasons}']
            return classification

    # ── Criterion 1: 海纜鄰近活動 ──
    if track_points:
        cable_near, cable_details = check_cable_proximity(track_points)
        classification['cable_proximity'] = cable_near
        classification['cable_details'] = cable_details
        if cable_near:
            cables_str = ', '.join(cable_details.get('cables_nearby', [])[:3])
            classification['flags'].append(f'海纜鄰近活動：{cables_str}')

        # Criterion 1b: 海纜鄰近低速徘徊 (>3hr, <8kn)
        if cable_details.get('loiter_triggered'):
            classification['cable_loitering'] = True
            hrs = cable_details.get('loiter_slow_hours', 0)
            classification['flags'].append(
                f'海纜低速徘徊：{hrs}h (<{CABLE_LOITER_MAX_KNOTS}kn)'
            )

    # ── Criterion 2: Z 字型移動 ──
    if track_points:
        zigzag, zigzag_details = check_zigzag_pattern(track_points)
        classification['zigzag_pattern'] = zigzag
        classification['zigzag_details'] = zigzag_details
        if zigzag:
            classification['flags'].append(
                f'Z字型移動模式：{zigzag_details["turn_count"]} 次大幅轉向'
            )

    # ── Criterion 3: 200m 等深線活動 ──
    if track_points:
        depth_near, depth_details = check_depth_200m_activity(track_points)
        classification['depth_200m_activity'] = depth_near
        classification['depth_200m_details'] = depth_details
        if depth_near:
            pct = round(depth_details['contour_proximity_ratio'] * 100)
            classification['flags'].append(f'200m等深線活動：{pct}% 時間')

    # ── Criterion 4: AIS 異常 ──
    anomalies = analyze_ais_anomalies(profile, identity_events)
    classification['ais_anomalies'] = anomalies
    if anomalies:
        classification['flags'].extend([a['description'] for a in anomalies])

    # ── Criterion 5: 非前十大船旗國 ──
    mid = mmsi[:3] if len(mmsi) >= 3 else ''
    if mid and mid not in TOP_10_FLAG_MIDS:
        classification['non_top10_flag'] = True
        classification['flags'].append(f'非前十大船旗國 (MID {mid})')

    # ── Criterion 6: 制裁清單（UN 1718 + 多機構影子船隊黑名單）──
    if sanctions_match:
        classification['sanctioned'] = True
        classification['sanction_info'] = sanctions_match
        name_only = sanctions_match.get('matched_by') == 'name'
        suffix = '｜僅船名匹配，無 IMO 佐證' if name_only else ''

        if sanctions_match.get('source') == 'blacklist':
            # 多機構黑名單：標出是哪些機構制裁 + 船旗
            progs = ', '.join(sanctions_match.get('programs', [])) or '未列機構'
            flag_ctry = sanctions_match.get('flag', '')
            flag_str = f' 旗:{flag_ctry}' if flag_ctry else ''
            classification['flags'].append(
                f'⚠️ 受制裁油輪 ({progs}){flag_str}{suffix}'
            )
            # 身分掩蓋偵測：IMO 命中、但 AIS 廣播船名 ≠ 制裁登記名
            reg_name = (sanctions_match.get('name') or '').upper().strip()
            ais_names = {(n or '').upper().strip()
                         for n in profile.get('names_seen', [])}
            if not name_only and reg_name and ais_names and reg_name not in ais_names:
                classification['sanction_identity_concealment'] = True
                classification['flags'].append(
                    f'🚨 身分掩蓋：AIS 船名 ≠ 制裁登記名（登記 {reg_name}）'
                )
        else:
            # UN 1718 清單
            res = sanctions_match.get('resolution', '1718')
            measures = ', '.join(sanctions_match.get('measures', []))
            classification['flags'].append(
                f'⚠️ UN 制裁船舶 (UNSCR {res}: {measures}){suffix}'
            )

    # ── Criterion 7: AIS 偽訊號偵測 (Spoofing) ──
    if track_points:
        physics, physics_details = check_impossible_physics(track_points)
        classification['spoof_impossible_physics'] = physics
        classification['spoof_physics_details'] = physics_details
        if physics:
            parts = []
            if physics_details.get('teleport_count'):
                parts.append(f'{physics_details["teleport_count"]}次瞬移')
            if physics_details.get('speed_mismatch_count'):
                parts.append(f'速度不符{physics_details["speed_mismatch_count"]}次')
            if physics_details.get('bearing_mismatch_count'):
                parts.append(f'航向不符{physics_details["bearing_mismatch_count"]}次')
            classification['flags'].append(
                f'AIS異常物理：{", ".join(parts)}'
            )

        box, box_details = check_box_pattern(track_points)
        classification['spoof_box_pattern'] = box
        classification['spoof_box_details'] = box_details
        if box:
            classification['flags'].append(
                f'AIS方形軌跡：{box_details["right_angle_turns"]}次直角轉彎 '
                f'(bbox {box_details["bounding_box_km"]}km)'
            )

        circle, circle_details = check_circle_pattern(track_points)
        classification['spoof_circle_pattern'] = circle
        classification['spoof_circle_details'] = circle_details
        if circle:
            classification['flags'].append(
                f'AIS圓形軌跡：半徑{circle_details["mean_radius_km"]}km '
                f'CV={circle_details["radius_cv"]} '
                f'弧度{circle_details["arc_coverage_deg"]}°'
            )

    # ── Criterion 8: ITU MARS 登記比對 ──
    if mars_record:
        has_mismatch, mars_details = check_itu_mars_mismatch(
            profile, mars_record)
        classification['itu_mars_mismatch'] = has_mismatch
        classification['itu_mars_details'] = mars_details
        if has_mismatch:
            for m in mars_details.get('mismatches', []):
                classification['flags'].append(
                    f'ITU登記不符：{m["description"]}'
                )
    else:
        classification['itu_mars_mismatch'] = False
        classification['itu_mars_details'] = {}

    # ── 判定主要船型 ──
    types_seen = list(profile.get('types_seen', []))
    # profile 缺漏時（Actions 快取冷啟動）改由航跡點補；公務類別優先
    if track_points:
        types_seen.extend(
            p['type_name'] for p in track_points if p.get('type_name'))
        types_seen.extend(p['gov'] for p in track_points if p.get('gov'))
    # 取最後一個非 unknown/other 的船型；否則 unknown
    vessel_type = 'unknown'
    for t in reversed(types_seen):
        if t not in ('unknown', 'other'):
            vessel_type = t
            break
    classification['vessel_type'] = vessel_type
    type_mult = VESSEL_TYPE_MULTIPLIER.get(vessel_type, 0.5)
    classification['type_multiplier'] = type_mult

    # ── 星爆/蜘蛛網軌跡（僅非漁船，僅標記不加分）──
    if track_points:
        starburst, starburst_details = check_starburst_pattern(
            track_points, vessel_type)
        classification['spoof_starburst_pattern'] = starburst
        classification['spoof_starburst_details'] = starburst_details
        if starburst:
            classification['flags'].append(
                f"AIS星爆軌跡（非漁船）：{starburst_details['spoke_count']}條輻條 "
                f"中心({starburst_details['center_lat']:.4f}, "
                f"{starburst_details['center_lon']:.4f}) "
                f"半徑{starburst_details['mean_radius_km']}km"
            )

    # ── Criterion 11: 割草式測線（測繪意圖）──
    classification['survey_pattern'] = False
    if track_points:
        # 船名同樣以 profile ∪ 航跡點取聯集（理由同船型 fallback）
        survey_names = set(all_names)
        survey_names.update(
            p['name'] for p in track_points if p.get('name'))
        survey, survey_details = check_survey_pattern(
            track_points, vessel_type, survey_names)
        classification['survey_pattern'] = survey
        classification['survey_details'] = survey_details
        if survey:
            label = ('階梯式網格' if survey_details['survey_type'] == 'grid'
                     else '重複測線')
            classification['flags'].append(
                f"割草式測線（{label}）：{survey_details['leg_count']} 條測線 "
                f"/ {survey_details['reversals']} 次反向，"
                f"主軸 {survey_details['axis_deg']}°，"
                f"平均線長 {survey_details['mean_leg_km']}km，"
                f"{survey_details['median_speed_kn']}kn"
            )

    # ── Criterion 12: 公務船編隊（detect_gov_formation.py）──
    if formation_record:
        classification['gov_formation'] = True
        classification['gov_formation_details'] = formation_record
        hrs = formation_record.get('max_duration_hours', 0)
        if formation_record.get('escorted_research'):
            classification['flags'].append(
                f'🛡️ 護航科考編隊：科研船 + 執法船同框 {hrs}h '
                f'（{formation_record.get("count", 1)} 次）')
        else:
            classification['flags'].append(
                f'公務船編隊：{formation_record.get("count", 1)} 次，最長 {hrs}h')

    # ── 離岸長期徘徊（商船，獨立於海纜）──
    classification['offshore_loitering'] = False
    if track_points:
        offshore, offshore_details = check_offshore_loitering(
            track_points, vessel_type)
        classification['offshore_loitering'] = offshore
        classification['offshore_loiter_details'] = offshore_details
        if offshore:
            classification['flags'].append(
                f"離岸長期徘徊：{offshore_details['loiter_days']}天原地"
                f"（{offshore_details['low_speed_fraction']*100:.0f}%低速, "
                f"中位半徑{offshore_details['median_radius_km']}km）"
            )

    # ── 海域法域 + 海纜緩衝帶（最近位置）──────────────────────
    # 先算出 geofence 標註，供計分與輸出共用（失敗安全降級，不影響評分）。
    gf = None
    last_in_port = False
    classification['cable_buffer_1km'] = False
    classification['cable_buffer_jurisdiction'] = False
    if track_points:
        _last = track_points[-1]
        last_in_port = bool(_last.get('in_port'))
        _lat, _lon = _last.get('lat'), _last.get('lon')
        if _lat is not None and _lon is not None:
            try:
                gf = geofence.annotate(_lat, _lon)
            except Exception:
                gf = None
    # 港內不加分：靠泊在港（≒海纜登陸點附近、必為內水）不是灰色地帶威脅情境
    if gf and not last_in_port:
        if gf.get('cable_band') == 'within_1km':
            classification['cable_buffer_1km'] = True
            zone = gf.get('zone')
            if zone in JURISDICTION_ZONES:
                classification['cable_buffer_jurisdiction'] = True
                classification['flags'].append(
                    f'緊貼海纜 ≤1km（{zone}）')
            else:
                classification['flags'].append('緊貼海纜 ≤1km')

    # ── 風險計分 ──
    raw_score = 0

    # ── 基礎行為分（單獨不構成可疑）──
    if classification['cable_proximity']:
        raw_score += 2  # 海纜 5km 內
    if classification['cable_loitering']:
        raw_score += 3  # 海纜低速徘徊 >3hr <8kn
    if classification['zigzag_pattern']:
        raw_score += 1  # Z字型
    if classification['depth_200m_activity']:
        raw_score += 1
    if classification['non_top10_flag']:
        raw_score += 1  # 非前十大船旗國
    if classification['cable_buffer_1km']:
        raw_score += CABLE_BUFFER_1KM_SCORE  # 最近位置緊貼海纜 ≤1km
    if classification['cable_buffer_jurisdiction']:
        raw_score += CABLE_BUFFER_JURISDICTION_SCORE  # 且位於我國管轄海域

    # ── 組合加分（多重指標交叉 = 高度可疑）──
    # 拖錨組合：轉向須發生在可能下錨的速度（≤7kn）— 高速 Z 字是漁撈/操船，非拖錨
    if (classification['cable_proximity'] and classification['zigzag_pattern']
            and classification.get('zigzag_details', {})
                .get('turns_below_drag_speed', 0) >= ZIGZAG_MIN_TURNS):
        raw_score += 3  # 海纜鄰近 + 低速 Z字型 = 可能拖錨
    if classification['cable_proximity'] and classification['cable_loitering']:
        raw_score += 2  # 海纜鄰近 + 長時間徘徊

    # ── 船型乘數下限：公務/科研船在編隊或測線作業中不打折 ──
    # 科研 ×0.5、公務 ×0.5 的折扣是為「例行公務航行」設計的；一艘正在編隊
    # 護航或跑測線的國家船舶，其海纜鄰近/等深線行為不該再打對折。
    # 僅適用於公務/科研船（vessel_type ∈ GOV_TYPE_NAMES）或已成案的編隊成員 —
    # 測線偵測對純數字船名的低速拖網船仍有殘餘誤報，不可讓漁船的 ×0.2 被抬升。
    if (classification['gov_formation']
            or (classification['survey_pattern']
                and vessel_type in GOV_TYPE_NAMES)):
        if type_mult < GOV_INTENT_MULTIPLIER_FLOOR:
            type_mult = GOV_INTENT_MULTIPLIER_FLOOR
            classification['type_multiplier'] = type_mult
            classification['intent_multiplier_floor'] = True

    # ── 套用船型乘數（商船 ×1.0, 漁船 ×0.2, 其他 ×0.5）──
    score = raw_score * type_mult

    # ── 高威脅指標（不受船型乘數影響）──
    if classification['sanctioned']:
        # IMO 確認 +8（最高優先）；純船名匹配 +4（重名可能，降低權重）
        if sanctions_match and sanctions_match.get('matched_by') == 'name':
            score += SANCTION_NAME_ONLY_SCORE
        else:
            score += SANCTION_IMO_SCORE
    for a in anomalies:
        if a['severity'] == 'high':
            score += 3  # 嚴重 AIS 異常（多次船名變更等）
        else:
            score += 1

    # ── 離岸長期徘徊 + 非前十大船旗（權宜船旗油輪待命 = 影子船隊樣態）──
    # 兩者並存才加分：單純離岸徘徊可能是合法等泊，搭配權宜船旗才是可疑輪廓。
    if classification['offshore_loitering'] and classification['non_top10_flag']:
        score += OFFSHORE_LOITER_SCORE

    # ── AIS 偽訊號（不受船型乘數影響，每項 +4）──
    if classification['spoof_impossible_physics']:
        score += 4
    if classification['spoof_box_pattern']:
        score += 4
    if classification['spoof_circle_pattern']:
        score += 4
    # 偽訊號 + 海纜鄰近 = 蓄意隱匿
    spoofing = (classification['spoof_impossible_physics'] or
                classification['spoof_box_pattern'] or
                classification['spoof_circle_pattern'])
    if spoofing and classification['cable_proximity']:
        score += 3

    # ── ITU MARS 登記不符（不受船型乘數影響）──
    if classification['itu_mars_mismatch']:
        score += 3

    # ── STS 旁靠加分（不受船型乘數影響）──
    if sts_record:
        classification['sts_transfer'] = True
        classification['sts_count'] = sts_record['count']
        classification['sts_suspicious'] = sts_record['suspicious']
        if sts_record['suspicious']:
            score += STS_SUSPICIOUS_SCORE
            classification['flags'].append(
                f'可疑旁靠 (STS)：{sts_record["count"]} 次')
        else:
            score += STS_ANY_SCORE
            classification['flags'].append(
                f'旁靠紀錄：{sts_record["count"]} 次')

    # ── 割草式測線（不受船型乘數影響）──
    if classification['survey_pattern']:
        score += SURVEY_SCORE

    # ── 公務船編隊（不受船型乘數影響）──
    if classification['gov_formation']:
        score += (GOV_FORMATION_ESCORT_SCORE
                  if formation_record.get('escorted_research')
                  else GOV_FORMATION_SCORE)

    # 四捨五入為整數
    score = round(score)

    # ── 風險等級 ──
    if score >= 12:
        classification['risk_level'] = 'critical'
    elif score >= 8:
        classification['risk_level'] = 'high'
    elif score >= 5:
        classification['risk_level'] = 'medium'

    classification['raw_score'] = raw_score
    classification['risk_score'] = score
    classification['suspicious'] = score >= 8

    # 附加位置資訊（geofence 已於計分前算好，此處沿用）
    if track_points:
        last = track_points[-1]
        classification['last_lat'] = last.get('lat')
        classification['last_lon'] = last.get('lon')
        classification['last_seen'] = last.get('t')
        if gf:
            classification['geofence'] = gf

    return classification


def compact_highrisk_row(c):
    """可疑船 classification → 週報累積用 compact 列（純函式）。

    只保留 aggregate_highrisk.py 需要的欄位；徘徊事件壓成
    [[lat, lon, hours, avg_kn, start_date], ...] — start_date 讓彙整端能
    跨日去重（14 天航跡視窗下同一事件會連續多天出現在快照裡）。
    欄位縮寫換空間：這個檔涵蓋全部 suspicious（~1750 艘），完整記錄會是
    suspicious_vessels.json 的數倍。
    """
    cd = c.get('cable_details') or {}
    od = c.get('offshore_loiter_details') or {}
    gf = c.get('geofence') or {}
    names = c.get('names') or []
    ev = [[e['center_lat'], e['center_lon'], e['hours'], e.get('avg_speed_kn'),
           (e.get('start') or '')[:10]]
          for e in (cd.get('loiter_events') or [])]
    return {
        'mmsi': c.get('mmsi'),
        'name': names[0] if names else '',
        'vessel_type': c.get('vessel_type', 'unknown'),
        'risk_score': c.get('risk_score', 0),
        'risk_level': c.get('risk_level', 'normal'),
        'non_top10_flag': bool(c.get('non_top10_flag')),
        'sanctioned': bool(c.get('sanctioned')),
        'cable_loitering': bool(c.get('cable_loitering')),
        'offshore_loitering': bool(c.get('offshore_loitering')),
        'loiter_h': cd.get('loiter_slow_hours', 0.0),
        'loiter_kn': cd.get('loiter_avg_speed_kn'),
        'ev': ev,
        'cables': (cd.get('cables_nearby') or [])[:3],
        'off_days': od.get('loiter_days', 0.0),
        'last_lat': c.get('last_lat'),
        'last_lon': c.get('last_lon'),
        'last_seen': c.get('last_seen'),
        'zone': gf.get('zone'),
    }


def main():
    print("=" * 60)
    print("🔍 海底電纜威脅偵測 — 可疑船隻分析")
    print("   Cable Threat Detection Engine")
    print("=" * 60)
    print(f"執行時間: {datetime.now(timezone.utc).isoformat()}")

    # 載入資料
    id_events_by_mmsi = load_identity_events()
    id_event_count = sum(len(v) for v in id_events_by_mmsi.values())
    print(f"🔄 身分變更事件: {id_event_count} 筆 ({len(id_events_by_mmsi)} 艘船)")

    # 載入制裁清單（UN 1718 + 多機構影子船隊黑名單）
    sanctions_by_imo, sanctions_imo_set, sanctions_name_set = load_sanctions_list()
    print(f"🚫 制裁船舶總計: {len(sanctions_imo_set)} 艘 IMO / {len(sanctions_name_set)} 名稱")

    # 載入船隻 profile（用於 AIS 異常偵測）
    profiles = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            profiles_data = json.load(f)
        if isinstance(profiles_data, dict):
            profiles = profiles_data
    print(f"📋 船隻 profiles: {len(profiles)}")

    # 載入 ITU MARS 快取（用於交叉比對船舶登記資料）
    mars_cache = load_itu_mars_cache()

    # 載入 STS 旁靠紀錄
    sts_map = load_ship_transfers()

    # 載入公務船編隊事件
    formation_map = load_gov_formations()

    # 載入航跡歷史（用於海纜鄰近、Z字型、等深線分析）
    tracks = load_track_history()

    # 預載海纜資料
    load_cable_segments()

    # 合併所有 MMSI（profile + track 的聯集），僅保留近期活躍的船
    all_mmsi = set(profiles.keys()) | set(tracks.keys())
    now = datetime.now(timezone.utc)
    active_mmsi = {
        m for m in all_mmsi
        if is_recently_active(profiles.get(m, {}), m in tracks, now)
    }
    stale_skipped = len(all_mmsi) - len(active_mmsi)
    print(f"\n📊 分析 {len(active_mmsi)} 艘活躍船隻"
          f"（跳過 {stale_skipped} 艘 >{ANALYSIS_ACTIVE_DAYS} 天未見）...")

    classifications = []
    suspicious_vessels = []
    excluded_vessels = []

    for mmsi in active_mmsi:
        profile = profiles.get(mmsi, {
            'mmsi': mmsi,
            'names_seen': [],
            'types_seen': [],
            'total_snapshots': 0,
        })
        if 'mmsi' not in profile:
            profile['mmsi'] = mmsi

        track_pts = tracks.get(mmsi, [])
        id_events = id_events_by_mmsi.get(mmsi)

        # 檢查是否在 UN 制裁清單中（比對 IMO 和船名）
        # matched_by 標記匹配方式：imo（高信度 +8）/ name（純船名 +4）
        sanction_hit = None
        imo = profile.get('last_imo', '')
        if imo and imo in sanctions_imo_set:
            hit = sanctions_by_imo.get(imo)
            if hit:
                sanction_hit = dict(hit, matched_by='imo')
        if not sanction_hit:
            for name in profile.get('names_seen', []):
                if name.upper().strip() in sanctions_name_set:
                    # 名稱匹配 — 找到對應的制裁條目
                    for sv in sanctions_by_imo.values():
                        if sv.get('name', '').upper().strip() == name.upper().strip():
                            sanction_hit = dict(sv, matched_by='name')
                            break
                    break

        mars_rec = mars_cache.get(mmsi)
        sts_rec = sts_map.get(mmsi)
        formation_rec = formation_map.get(mmsi)
        result = classify_vessel(profile, track_pts, id_events,
                                 sanctions_match=sanction_hit,
                                 mars_record=mars_rec,
                                 sts_record=sts_rec,
                                 formation_record=formation_rec)
        if result.get('excluded'):
            excluded_vessels.append(result)
        else:
            classifications.append(result)
            if result['suspicious']:
                suspicious_vessels.append(result)

    # 按風險分數排序
    suspicious_vessels.sort(key=lambda x: x['risk_score'], reverse=True)
    classifications.sort(key=lambda x: x.get('risk_score', 0), reverse=True)

    # Top 10% 高風險船隻數量（排除後的船隻，取前 10%）
    non_excluded_count = len(classifications)
    top_10pct_cutoff = max(1, non_excluded_count // 10)
    top_10pct_vessels = classifications[:top_10pct_cutoff]
    # 只計算 score > 0 的（避免把大量 0 分船隻算進去）
    top_10pct_with_score = [v for v in top_10pct_vessels if v.get('risk_score', 0) > 0]

    # 排除規則統計
    exclusion_stats = {}
    for ev in excluded_vessels:
        for rule in ev.get('exclusion_rules', []):
            rid = rule['id']
            exclusion_stats[rid] = exclusion_stats.get(rid, 0) + 1

    # 統計
    risk_counts = {'critical': 0, 'high': 0, 'medium': 0, 'normal': 0}
    cable_count = 0
    loiter_count = 0
    zigzag_count = 0
    depth_count = 0
    anomaly_count = 0
    non_top10_count = 0
    sanctioned_count = 0
    spoof_physics_count = 0
    spoof_box_count = 0
    spoof_circle_count = 0
    spoof_starburst_count = 0
    mars_mismatch_count = 0
    sts_transfer_count = 0
    offshore_loiter_count = 0
    survey_pattern_count = 0
    survey_type_counts = {}
    gov_formation_count = 0
    gov_formation_escort_count = 0
    zone_counts = {}
    cable_band_counts = {}
    cable_buffer_1km_count = 0
    cable_buffer_jur_count = 0

    for c in classifications:
        risk_counts[c['risk_level']] += 1
        if c.get('cable_buffer_1km'):
            cable_buffer_1km_count += 1
        if c.get('cable_buffer_jurisdiction'):
            cable_buffer_jur_count += 1
        gf = c.get('geofence')
        if gf:
            zone_counts[gf.get('zone', 'unknown')] = zone_counts.get(gf.get('zone', 'unknown'), 0) + 1
            band = gf.get('cable_band', 'unknown')
            cable_band_counts[band] = cable_band_counts.get(band, 0) + 1
        if c.get('cable_proximity'):
            cable_count += 1
        if c.get('cable_loitering'):
            loiter_count += 1
        if c.get('zigzag_pattern'):
            zigzag_count += 1
        if c.get('depth_200m_activity'):
            depth_count += 1
        if c.get('ais_anomalies'):
            anomaly_count += 1
        if c.get('non_top10_flag'):
            non_top10_count += 1
        if c.get('sanctioned'):
            sanctioned_count += 1
        if c.get('spoof_impossible_physics'):
            spoof_physics_count += 1
        if c.get('spoof_box_pattern'):
            spoof_box_count += 1
        if c.get('spoof_circle_pattern'):
            spoof_circle_count += 1
        if c.get('spoof_starburst_pattern'):
            spoof_starburst_count += 1
        if c.get('itu_mars_mismatch'):
            mars_mismatch_count += 1
        if c.get('sts_transfer'):
            sts_transfer_count += 1
        if c.get('offshore_loitering'):
            offshore_loiter_count += 1
        if c.get('survey_pattern'):
            survey_pattern_count += 1
            st = c.get('survey_details', {}).get('survey_type', 'unknown')
            survey_type_counts[st] = survey_type_counts.get(st, 0) + 1
        if c.get('gov_formation'):
            gov_formation_count += 1
            if c.get('gov_formation_details', {}).get('escorted_research'):
                gov_formation_escort_count += 1

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'methodology': 'Submarine Cable Threat Detection',
        'criteria': {
            'cable_proximity_km': CABLE_PROXIMITY_KM,
            'cable_loiter_hours': CABLE_LOITER_HOURS,
            'cable_loiter_max_knots': CABLE_LOITER_MAX_KNOTS,
            'loiter_max_gap_hours': LOITER_MAX_GAP_HOURS,
            'anchor_drag_max_knots': ANCHOR_DRAG_MAX_KNOTS,
            'stationary_max_knots': STATIONARY_MAX_KNOTS,
            'physics_mismatch_max_dt_hours': PHYSICS_MISMATCH_MAX_DT_HOURS,
            'offshore_loiter_days': OFFSHORE_LOITER_DAYS,
            'offshore_loiter_max_knots': OFFSHORE_LOITER_MAX_KNOTS,
            'offshore_loiter_score': OFFSHORE_LOITER_SCORE,
            'sanction_imo_score': SANCTION_IMO_SCORE,
            'sanction_name_only_score': SANCTION_NAME_ONLY_SCORE,
            'port_exclusion': True,  # 港內點不計海纜鄰近/徘徊/緩衝帶
            'zigzag_min_turns': ZIGZAG_MIN_TURNS,
            'zigzag_heading_change_deg': ZIGZAG_HEADING_CHANGE_DEG,
            'depth_200m_contour_km': DEPTH_200M_CONTOUR_KM,
            'name_change_threshold': NAME_CHANGE_THRESHOLD,
            'spoof_teleport_kmh': SPOOF_TELEPORT_KMH,
            'spoof_box_angle_tolerance': SPOOF_BOX_ANGLE_TOLERANCE,
            'spoof_circle_radius_cv': SPOOF_CIRCLE_RADIUS_CV,
            'starburst_min_arc_deg': STARBURST_MIN_ARC_DEG,
            'starburst_radius_cv_min': STARBURST_RADIUS_CV_MIN,
            'starburst_max_radius_km': STARBURST_MAX_RADIUS_KM,
            'starburst_min_spokes': STARBURST_MIN_SPOKES,
            'starburst_hub_fraction': STARBURST_HUB_FRACTION,
            'starburst_excludes_fishing': True,
            'vessel_type_multiplier': VESSEL_TYPE_MULTIPLIER,
            'sts_suspicious_score': STS_SUSPICIOUS_SCORE,
            'sts_any_score': STS_ANY_SCORE,
            'survey_min_legs': SURVEY_MIN_LEGS,
            'survey_min_reversals': SURVEY_MIN_REVERSALS,
            'survey_min_leg_km': SURVEY_MIN_LEG_KM,
            'survey_spacing_cv_max': SURVEY_SPACING_CV_MAX,
            'survey_min_parallel_fraction': SURVEY_MIN_PARALLEL_FRACTION,
            'survey_grid_min_mean_leg_km': SURVEY_GRID_MIN_MEAN_LEG_KM,
            'survey_transect_spread_km': SURVEY_TRANSECT_SPREAD_KM,
            'survey_max_median_knots': SURVEY_MAX_MEDIAN_KNOTS,
            'survey_max_gap_hours': SURVEY_MAX_GAP_HOURS,
            'survey_score': SURVEY_SCORE,
            'survey_excludes_fishing_by_name': True,
            'gov_formation_score': GOV_FORMATION_SCORE,
            'gov_formation_escort_score': GOV_FORMATION_ESCORT_SCORE,
            'gov_intent_multiplier_floor': GOV_INTENT_MULTIPLIER_FLOOR,
            'cable_buffer_1km_score': CABLE_BUFFER_1KM_SCORE,
            'cable_buffer_jurisdiction_score': CABLE_BUFFER_JURISDICTION_SCORE,
        },
        'exclusion_rules': [
            {'id': r['id'], 'label': r['label']} for r in EXCLUSION_RULES
        ] + [
            # 台灣船隻排除（於 classify_vessel 內以航跡/事件資料判定，
            # 非 EXCLUSION_RULES 機制；有身分變更或制裁命中者不排除）
            {'id': 'flag_taiwan', 'label': '台灣船旗 (MID 416)'},
            {'id': 'moored_taiwan_port', 'label': '停泊台灣港內（最後位置）'},
        ],
        'summary': {
            'total_analyzed': len(active_mmsi),
            'stale_skipped': stale_skipped,
            'active_window_days': ANALYSIS_ACTIVE_DAYS,
            'excluded_count': len(excluded_vessels),
            'exclusion_breakdown': exclusion_stats,
            'suspicious_count': len(suspicious_vessels),
            'top_10pct_count': len(top_10pct_with_score),
            'top_10pct_min_score': top_10pct_with_score[-1]['risk_score'] if top_10pct_with_score else 0,
            'cable_proximity_triggered': cable_count,
            'cable_loitering_triggered': loiter_count,
            'zigzag_pattern_detected': zigzag_count,
            'depth_200m_activity': depth_count,
            'ais_anomaly_detected': anomaly_count,
            'non_top10_flag': non_top10_count,
            'sanctioned_vessels': sanctioned_count,
            'spoof_impossible_physics': spoof_physics_count,
            'spoof_box_pattern': spoof_box_count,
            'spoof_circle_pattern': spoof_circle_count,
            'spoof_starburst_pattern': spoof_starburst_count,
            'itu_mars_mismatch': mars_mismatch_count,
            'sts_transfer': sts_transfer_count,
            'offshore_loitering': offshore_loiter_count,
            'survey_pattern': survey_pattern_count,
            'survey_pattern_types': survey_type_counts,
            'gov_formation': gov_formation_count,
            'gov_formation_escorted_research': gov_formation_escort_count,
            'cable_buffer_1km': cable_buffer_1km_count,
            'cable_buffer_jurisdiction': cable_buffer_jur_count,
            'risk_distribution': risk_counts,
            'maritime_zone_distribution': zone_counts,
            'cable_band_distribution': cable_band_counts,
        },
        'suspicious_vessels': suspicious_vessels[:50],
        'all_classifications': classifications[:200],
    }

    atomic_write_json(OUTPUT_FILE, output)

    # 完整高風險 snapshot（suspicious 全列、不截斷）→ 週/月彙整的累積來源。
    # suspicious_vessels.json 只留 top-50，週報只讀它會漏掉九成以上高風險船。
    atomic_write_json(HIGHRISK_SNAPSHOT_FILE, {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'count': len(suspicious_vessels),
        'vessels': [compact_highrisk_row(c) for c in suspicious_vessels],
    }, compact=True)

    print(f"\n📋 分析結果:")
    print(f"   分析船隻數: {len(active_mmsi)} "
          f"(另跳過 {stale_skipped} 艘 >{ANALYSIS_ACTIVE_DAYS} 天未見)")
    print(f"   排除 (非船舶設備): {len(excluded_vessels)}")
    if exclusion_stats:
        for rid, cnt in sorted(exclusion_stats.items(), key=lambda x: -x[1]):
            print(f"     - {rid}: {cnt}")
    print(f"   可疑船隻 (score ≥ 8): {len(suspicious_vessels)}")
    print(f"   海纜鄰近: {cable_count}")
    print(f"   海纜低速徘徊 (>3hr <8kn): {loiter_count}")
    print(f"   Z字型移動: {zigzag_count}")
    print(f"   200m等深線: {depth_count}")
    print(f"   AIS 異常: {anomaly_count}")
    print(f"   非前十大船旗: {non_top10_count}")
    print(f"   UN 制裁匹配: {sanctioned_count}")
    print(f"   偽訊號-異常物理: {spoof_physics_count}")
    print(f"   偽訊號-方形軌跡: {spoof_box_count}")
    print(f"   偽訊號-圓形軌跡: {spoof_circle_count}")
    print(f"   星爆軌跡(非漁船,僅標記): {spoof_starburst_count}")
    print(f"   ITU MARS不符: {mars_mismatch_count}")
    print(f"   STS旁靠涉入: {sts_transfer_count}")
    print(f"   離岸長期徘徊(商船): {offshore_loiter_count}")
    print(f"   割草式測線: {survey_pattern_count} {survey_type_counts}")
    print(f"   公務船編隊: {gov_formation_count} "
          f"(護航科考 {gov_formation_escort_count})")
    print(f"   風險分布: {risk_counts}")
    print(f"\n📁 結果已輸出至: {OUTPUT_FILE}")


if __name__ == '__main__':
    main()

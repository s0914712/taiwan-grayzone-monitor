"""海域法域分類與海纜緩衝區 — Maritime zone classification & cable buffers.

提供把單一座標（或一批座標）對應到：
  * 海域法域：內水 / 領海(12浬) / 鄰接區(24浬) / 經濟海域(≤200浬) / 公海
    — 以內政部公告的領海基線（docs/data/territorial_baseline.json）量測。
  * 最近海底電纜距離與緩衝帶：≤1km / ≤5km / ≤10km / >10km
    — 以 data/cable-geo.json 的線段量測。

設計為純 stdlib（update-ais.yml 只裝 requests + pysocks），幾何運算復用
geo_utils。幾何資料以惰性單例載入；資料檔缺失時各函式安全降級（回傳 unknown）。

CLI：``python src/geofence.py <lat> <lon>`` 印出單點分類，便於測試/示範。

注意：此處 EEZ 採「距基線 ≤200 浬」之簡化定義，未做中線劃界，僅供風險研判
參考，非法律上的專屬經濟海域界線。
"""
import json
import sys
from pathlib import Path

from geo_utils import (
    haversine_km, point_in_polygon, distance_to_polyline_km,
    point_to_segment_distance_km, km_to_nm, nm_to_km,
)

_REPO = Path(__file__).resolve().parent.parent
BASELINE_FILE = _REPO / "docs" / "data" / "territorial_baseline.json"
CABLE_GEO_FILE = _REPO / "data" / "cable-geo.json"

# 法域距離門檻（浬）
TERRITORIAL_SEA_NM = 12.0
CONTIGUOUS_ZONE_NM = 24.0
EEZ_NM = 200.0

# 海纜緩衝帶（公里）
CABLE_BANDS_KM = (1.0, 5.0, 10.0)

# 只保留台灣周邊海域的海纜線段（與 analyze_suspicious 一致）
_CABLE_BBOX = (19, 28, 115, 130)  # lat_min, lat_max, lon_min, lon_max

_baselines = None      # list[list[(lat, lon)]]
_cable_segments = None  # list[dict(points, bbox)]


# ── 資料載入 ────────────────────────────────────────────────────────────────
def load_baselines():
    """載入領海基線多邊形，回傳 list of polygons（每個為 [(lat, lon), ...]）。"""
    global _baselines
    if _baselines is not None:
        return _baselines
    _baselines = []
    if not BASELINE_FILE.exists():
        return _baselines
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _baselines
    for region, pts in data.items():
        poly = [(p[1], p[0]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(poly) >= 3:
            _baselines.append(poly)
    return _baselines


def load_cable_segments():
    """載入台灣周邊海纜線段，回傳 list of dict(points=[(lat,lon)...], bbox)。"""
    global _cable_segments
    if _cable_segments is not None:
        return _cable_segments
    _cable_segments = []
    if not CABLE_GEO_FILE.exists():
        return _cable_segments
    try:
        data = json.loads(CABLE_GEO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _cable_segments
    la0, la1, lo0, lo1 = _CABLE_BBOX
    for feat in data.get("features", []):
        for segment in feat.get("geometry", {}).get("coordinates", []):
            pts = [(lat, lon) for lon, lat in segment
                   if la0 <= lat <= la1 and lo0 <= lon <= lo1]
            if len(pts) >= 2:
                lats = [p[0] for p in pts]
                lons = [p[1] for p in pts]
                _cable_segments.append({
                    "points": pts,
                    "bbox": (min(lats), min(lons), max(lats), max(lons)),
                })
    return _cable_segments


# ── 分類函式 ────────────────────────────────────────────────────────────────
def classify_maritime_zone(lat, lon, baselines=None):
    """把座標分類到海域法域。

    回傳 dict：
        {"zone": <str>, "distance_to_baseline_nm": <float|None>,
         "distance_to_baseline_km": <float|None>, "inside_baseline": <bool>}
    zone ∈ internal_waters / territorial_sea / contiguous_zone / eez /
            high_seas / unknown
    """
    if baselines is None:
        baselines = load_baselines()
    if not baselines:
        return {"zone": "unknown", "distance_to_baseline_nm": None,
                "distance_to_baseline_km": None, "inside_baseline": False}

    if any(point_in_polygon(lat, lon, poly) for poly in baselines):
        return {"zone": "internal_waters", "distance_to_baseline_nm": 0.0,
                "distance_to_baseline_km": 0.0, "inside_baseline": True}

    dists = [distance_to_polyline_km(lat, lon, poly, closed=True) for poly in baselines]
    dists = [d for d in dists if d is not None]
    if not dists:
        return {"zone": "unknown", "distance_to_baseline_nm": None,
                "distance_to_baseline_km": None, "inside_baseline": False}
    d_km = min(dists)
    d_nm = km_to_nm(d_km)
    if d_nm <= TERRITORIAL_SEA_NM:
        zone = "territorial_sea"
    elif d_nm <= CONTIGUOUS_ZONE_NM:
        zone = "contiguous_zone"
    elif d_nm <= EEZ_NM:
        zone = "eez"
    else:
        zone = "high_seas"
    return {"zone": zone, "distance_to_baseline_nm": round(d_nm, 2),
            "distance_to_baseline_km": round(d_km, 2), "inside_baseline": False}


def nearest_cable(lat, lon, segments=None, max_band_km=None):
    """最近海底電纜距離與緩衝帶。

    回傳 dict：{"nearest_cable_km": <float|None>, "cable_band": <str>}
    cable_band ∈ within_1km / within_5km / within_10km / beyond_10km / unknown
    max_band_km 可加速：超過此距離的線段以 bbox 預先排除（預設取最大緩衝帶）。
    """
    if segments is None:
        segments = load_cable_segments()
    if not segments:
        return {"nearest_cable_km": None, "cable_band": "unknown"}

    best = None
    # bbox 預過濾：經緯度 1° 上限約 111 km，用較寬鬆的角度緩衝。
    prefilter = None
    if max_band_km is not None:
        prefilter = max_band_km / 100.0  # ~1.1km/0.01°，寬鬆即可
    for seg in segments:
        if prefilter is not None and best is not None:
            la0, lo0, la1, lo1 = seg["bbox"]
            if (lat < la0 - prefilter or lat > la1 + prefilter or
                    lon < lo0 - prefilter or lon > lo1 + prefilter):
                continue
        pts = seg["points"]
        for i in range(len(pts) - 1):
            d = point_to_segment_distance_km(lat, lon,
                                             pts[i][0], pts[i][1],
                                             pts[i + 1][0], pts[i + 1][1])
            if best is None or d < best:
                best = d
    if best is None:
        return {"nearest_cable_km": None, "cable_band": "unknown"}
    if best <= CABLE_BANDS_KM[0]:
        band = "within_1km"
    elif best <= CABLE_BANDS_KM[1]:
        band = "within_5km"
    elif best <= CABLE_BANDS_KM[2]:
        band = "within_10km"
    else:
        band = "beyond_10km"
    return {"nearest_cable_km": round(best, 2), "cable_band": band}


def annotate(lat, lon):
    """組合單點的法域 + 海纜緩衝帶標註。"""
    out = classify_maritime_zone(lat, lon)
    out.update(nearest_cable(lat, lon))
    return out


# ── 港口判定（台灣商港/漁港 + 大陸沿岸港口/錨地）──────────────────────────
# 自 detect_ship_transfers.py 移入共用：STS 偵測與威脅評分引擎皆需排除港內
# 例行活動（靠泊/錨泊/整補）。台灣港口半徑 2km；大陸港灣預設 8km，
# 部分大型灣澳以第三元素指定半徑。
PORT_EXCLUSION_KM = 2.0            # 台灣港口排除半徑 2 公里
CN_PORT_EXCLUSION_KM = 8.0         # 大陸沿岸大型港口/錨地排除半徑（錨地範圍大）

# ── 港口座標（商港 + 漁港）────────────────────────────────
PORTS = {
    # === 商港 Commercial Ports ===
    "高雄港 Kaohsiung":      (22.6153, 120.2664),
    "基隆港 Keelung":        (25.1509, 121.7405),
    "台中港 Taichung":       (24.2906, 120.5148),
    "台北港 Taipei":         (25.1580, 121.3728),
    "花蓮港 Hualien":        (23.9780, 121.6260),
    "蘇澳港 Suao":          (24.5946, 121.8622),
    "馬公港 Magong":         (23.5637, 119.5666),
    "金門料羅灣 Kinmen":     (24.4275, 118.3170),
    "馬祖福澳港 Matsu":     (26.1608, 119.9490),
    # === 漁港 Fishing Ports ===
    # ── 基隆市 Keelung ──
    "正濱漁港 Zhengbin":                 (25.1480, 121.7520),
    "八斗子漁港 Badouzi":                (25.1370, 121.7960),
    "大武崙漁港 Dawulun":                (25.1590, 121.7170),
    "外木山漁港 Waimushan":              (25.1580, 121.7250),
    "長潭里漁港 Changtanli":             (25.1310, 121.8020),
    "望海巷漁港 Wanghaixiang":           (25.1340, 121.8060),
    # ── 新北市 New Taipei ──
    "下罟子漁港 Xiaguzi":                (25.1250, 121.3970),
    "淡水第一漁港 Tamsui1":              (25.1790, 121.4280),
    "淡水第二漁港 Tamsui2":              (25.1760, 121.4310),
    "六塊厝漁港 Liukuaicuo":             (25.2020, 121.4370),
    "後厝漁港 Houcuo":                   (25.2230, 121.4860),
    "麟山鼻漁港 Linshanbi":              (25.2710, 121.5210),
    "富基漁港 Fuji":                     (25.2870, 121.5380),
    "石門漁港 Shimen":                   (25.2900, 121.5570),
    "草里漁港 Caoli":                    (25.2860, 121.5680),
    "磺港漁港 Huanggang":                (25.2580, 121.6120),
    "水尾漁港 Shuiwei":                  (25.2420, 121.6360),
    "野柳漁港 Yehliu":                   (25.2070, 121.6890),
    "東澳漁港 Dongao_NTP":              (25.1950, 121.6970),
    "龜吼漁港 Guihou":                   (25.2000, 121.6930),
    "萬里漁港 Wanli":                    (25.1800, 121.6890),
    "深澳漁港 Shenao":                   (25.1290, 121.8170),
    "水湳洞漁港 Shuinandong":            (25.1200, 121.8590),
    "南雅漁港 Nanya":                    (25.1180, 121.8520),
    "鼻頭漁港 Bitou_NTP":               (25.1240, 121.8710),
    "龍洞漁港 Longdong":                 (25.1100, 121.9180),
    "和美漁港 Hemei":                    (25.1020, 121.9240),
    "美豔山漁港 Meiyanshan":             (25.0990, 121.9310),
    "澳底漁港 Aodi":                     (25.0940, 121.9370),
    "澳仔漁港 Aozai":                   (25.0800, 121.9500),
    "龍門漁港 Longmen_NTP":              (25.0230, 121.9430),
    "福隆漁港 Fulong":                   (25.0170, 121.9480),
    "卯澳漁港 Maoao":                   (25.0120, 121.9630),
    "馬崗漁港 Magang":                   (25.0080, 121.9680),
    # ── 桃園市 Taoyuan ──
    "竹圍漁港 Zhuwei":                   (25.1100, 121.2360),
    "永安漁港 Yongan":                   (25.0030, 121.0130),
    # ── 新竹縣市 Hsinchu ──
    "新竹漁港 Nanliao":                  (24.8280, 120.9290),
    "海山漁港 Haishan":                  (24.7620, 120.9010),
    "坡頭漁港 Potou":                    (24.8920, 120.9590),
    # ── 苗栗縣 Miaoli ──
    "龍鳳漁港 Longfeng":                 (24.6870, 120.8540),
    "塭仔頭漁港 Wenzaitou":              (24.6910, 120.8460),
    "外埔漁港 Waipu":                    (24.6150, 120.7610),
    "公司寮漁港 Gongsiliao":             (24.6040, 120.7430),
    "福寧漁港 Funing":                   (24.5900, 120.7230),
    "南港漁港 Nangang_ML":               (24.5780, 120.7070),
    "白沙屯漁港 Baishatun":              (24.5410, 120.6830),
    "新埔漁港 Xinpu":                    (24.5120, 120.6730),
    "通霄漁港 Tongxiao":                 (24.4920, 120.6610),
    "苑港漁港 Yuangang":                 (24.4330, 120.6320),
    "苑裡漁港 Yuanli":                   (24.4110, 120.6260),
    # ── 臺中市 Taichung ──
    "梧棲漁港 Wuqi":                     (24.2950, 120.5180),
    "松柏漁港 Songbai":                  (24.3790, 120.5830),
    "五甲漁港 Wujia":                    (24.3530, 120.5540),
    "北汕漁港 Beishan":                  (24.3640, 120.5660),
    "塭寮漁港 Wenliao":                  (24.3440, 120.5430),
    "麗水漁港 Lishui":                   (24.2560, 120.4990),
    # ── 彰化縣 Changhua ──
    "崙尾灣漁港 Lunweiwan":              (24.0750, 120.4070),
    "王功漁港 Wanggong":                 (23.9620, 120.3200),
    "彰化漁港 Changhua":                 (24.0820, 120.4100),
    # ── 雲林縣 Yunlin ──
    "五條港漁港 Wutiaogang":             (23.6820, 120.1930),
    "台西漁港 Taixi":                    (23.6950, 120.1890),
    "三條崙漁港 Santiaolun":             (23.6200, 120.1630),
    "萡子寮漁港 Boziliao":               (23.5930, 120.1480),
    "金湖漁港 Jinhu":                    (23.5520, 120.1230),
    "台子村漁港 Taizicun":               (23.5440, 120.1140),
    # ── 嘉義縣 Chiayi ──
    "鰲鼓漁港 Aogu":                     (23.5050, 120.1210),
    "副瀨漁港 Fulai":                    (23.4840, 120.1080),
    "塭港漁港 Wengang":                  (23.4710, 120.1020),
    "下莊漁港 Xiazhuang":                (23.4620, 120.0950),
    "東石漁港 Dongshi":                  (23.4510, 120.0870),
    "網寮漁港 Wangliao":                 (23.4400, 120.0780),
    "白水湖漁港 Baishuihu":              (23.4310, 120.0720),
    "布袋漁港 Budai":                    (23.3730, 120.1600),
    "好美里漁港 Haomeili":               (23.3570, 120.1450),
    # ── 台南市 Tainan ──
    "安平漁港 Anping":                   (22.9972, 120.1600),
    "蚵寮漁港 Keliao_TN":               (23.2960, 120.0820),
    "北門漁港 Beimen":                   (23.2780, 120.0760),
    "將軍漁港 Jiangjun":                 (23.2050, 120.0900),
    "青山漁港 Qingshan":                 (23.1740, 120.0710),
    "下山漁港 Xiashan":                  (23.1490, 120.0620),
    "四草漁港 Sicao":                    (23.0180, 120.1640),
    # ── 高雄市 Kaohsiung ──
    "前鎮漁港 Qianzhen":                (22.5930, 120.3070),
    "白砂崙漁港 Baishalun":              (22.8880, 120.2240),
    "興達漁港 Xingda":                   (22.8580, 120.2130),
    "永新漁港 Yongxin":                  (22.8200, 120.2310),
    "彌陀漁港 Mituo":                    (22.7700, 120.2380),
    "蚵子寮漁港 Keziliao":               (22.7350, 120.2530),
    "鼓山漁港 Gushan":                   (22.6290, 120.2650),
    "旗后漁港 Qihou":                    (22.6110, 120.2630),
    "旗津漁港 Qijin":                    (22.6010, 120.2640),
    "上竹里漁港 Shangzhuli":             (22.5860, 120.2710),
    "中洲漁港 Zhongzhou":                (22.5780, 120.2790),
    "小港臨海新村漁港 Xiaogang":         (22.5590, 120.3060),
    "鳳鼻頭漁港 Fengbitou":              (22.5210, 120.3250),
    "港埔漁港 Gangpu":                   (22.4960, 120.3590),
    "中芸漁港 Zhongyun":                 (22.4810, 120.3780),
    "汕尾漁港 Shanwei":                  (22.4750, 120.3930),
    # ── 屏東縣 Pingtung ──
    "東港鹽埔漁港 Donggang":             (22.4640, 120.4410),
    "水利村漁港 Shuilicun":              (22.4330, 120.4660),
    "塭豐漁港 Wenfeng":                  (22.4200, 120.4890),
    "枋寮漁港 Fangliao":                 (22.3630, 120.5710),
    "楓港漁港 Fenggang":                 (22.2470, 120.6350),
    "海口漁港 Haikou":                   (22.0790, 120.6980),
    "後灣漁港 Houwan":                   (22.0590, 120.6910),
    "山海漁港 Shanhai":                  (21.9620, 120.7310),
    "紅柴坑漁港 Hongchaikeng":           (21.9490, 120.7360),
    "後壁湖漁港 Houbihu":                (21.9460, 120.7440),
    "潭仔漁港 Tanzai":                   (21.9430, 120.7590),
    "香蕉灣漁港 Xiangjiaowan":           (21.9490, 120.7810),
    "鼻頭漁港 Bitou_PT":                 (21.9590, 120.8100),
    "興海漁港 Xinghai":                  (22.0380, 120.8480),
    "中山漁港 Zhongshan":                (22.0530, 120.8550),
    "旭海漁港 Xuhai":                    (22.1560, 120.8780),
    "小琉球漁港 Xiaoliuqiu":             (22.3410, 120.3680),
    "漁福漁港 Yufu":                     (22.3450, 120.3810),
    "琉球新漁港 Liuqiuxin":              (22.3420, 120.3750),
    "天福漁港 Tianfu":                   (22.3350, 120.3640),
    "杉福漁港 Shanfu":                   (22.3380, 120.3590),
    # ── 宜蘭縣 Yilan ──
    "烏石漁港 Wushi":                    (24.8810, 121.8430),
    "南方澳漁港 Nanfangao":             (24.5850, 121.8700),
    "石城漁港 Shicheng":                 (24.9830, 121.9440),
    "桶盤堀漁港 Tongpanku":              (24.9690, 121.9370),
    "大里漁港 Dali":                     (24.9700, 121.9330),
    "蕃薯寮漁港 Fanshuliao":             (24.9520, 121.9200),
    "大溪漁港 Daxi":                     (24.9380, 121.9000),
    "梗枋漁港 Gengfang":                 (24.8820, 121.8520),
    "粉鳥林漁港 Fenniaolin":             (24.5670, 121.8700),
    "南澳漁港 Nanao":                   (24.4490, 121.8080),
    # ── 花蓮縣 Hualien ──
    "花蓮漁港 Hualien":                  (23.9820, 121.6280),
    "鹽寮漁港 Yanliao":                  (23.8910, 121.5590),
    "石梯漁港 Shiti":                    (23.4950, 121.5070),
    # ── 台東縣 Taitung ──
    "長濱漁港 Changbin":                 (23.3150, 121.4500),
    "烏石鼻漁港 Wushibi":               (23.2620, 121.4270),
    "小港漁港 Xiaogang_TT":             (23.1200, 121.3910),
    "新港漁港 Xingang":                  (23.0990, 121.3810),
    "金樽漁港 Jinzun":                   (22.9710, 121.2650),
    "新蘭漁港 Xinlan":                   (22.9450, 121.2350),
    "伽藍漁港 Fugang":                   (22.7920, 121.1740),
    "大武漁港 Dawu":                     (22.3560, 120.9110),
    "南寮漁港 Nanliao_LD":               (22.6610, 121.4700),
    "中寮漁港 Zhongliao":                (22.6710, 121.4860),
    "公館漁港 Gongguan":                 (22.6580, 121.4790),
    "溫泉漁港 Wenquan":                  (22.6570, 121.4640),
    "開元漁港 Kaiyuan":                  (22.0540, 121.5370),
    "朗島漁港 Langdao":                  (22.0740, 121.5550),
    # ── 澎湖縣 Penghu ──
    "合界漁港 Hejie":                    (23.5970, 119.5010),
    "橫礁漁港 Hengjiao":                 (23.5970, 119.4980),
    "竹灣漁港 Zhuwan":                   (23.5900, 119.5000),
    "二崁漁港 Erkan":                    (23.5870, 119.5060),
    "大菓葉漁港 Daguoye":               (23.5830, 119.5100),
    "赤馬漁港 Chima":                    (23.5750, 119.5070),
    "內垵南漁港 Neian_S":                (23.5620, 119.4770),
    "外垵漁港 Waian":                    (23.5570, 119.4720),
    "內垵北漁港 Neian_N":                (23.5650, 119.4810),
    "池西漁港 Chixi":                    (23.5790, 119.5130),
    "大池漁港 Dachi":                    (23.5810, 119.5190),
    "小門漁港 Xiaomen":                  (23.5920, 119.5030),
    "後寮漁港 Houliao":                  (23.6230, 119.5580),
    "赤崁漁港 Chikan":                   (23.6370, 119.5640),
    "岐頭漁港 Qitou":                    (23.6310, 119.5900),
    "港子漁港 Gangzi":                   (23.6240, 119.5960),
    "鎮海漁港 Zhenhai":                  (23.6640, 119.5970),
    "講美漁港 Jiangmei":                 (23.6460, 119.5670),
    "城前漁港 Chengqian":                (23.6370, 119.5540),
    "瓦硐漁港 Wadong":                   (23.6500, 119.5710),
    "通樑漁港 Tongliang":                (23.6590, 119.5550),
    "大倉漁港 Dacang":                   (23.5910, 119.5350),
    "員貝漁港 Yuanbei":                  (23.6260, 119.6110),
    "鳥嶼漁港 Niaoyu":                   (23.6410, 119.6280),
    "吉貝漁港 Jibei":                    (23.7280, 119.6090),
    "中西漁港 Zhongxi":                  (23.5950, 119.6290),
    "沙港西漁港 Shagang_W":              (23.5990, 119.6190),
    "沙港中漁港 Shagang_M":              (23.6010, 119.6230),
    "沙港東漁港 Shagang_E":              (23.6030, 119.6260),
    "成功漁港 Chenggong_PH":             (23.6060, 119.6360),
    "西溪漁港 Xixi":                     (23.5930, 119.6340),
    "紅羅漁港 Hongluo":                  (23.5890, 119.6370),
    "青螺漁港 Qingluo":                  (23.5850, 119.6330),
    "白坑漁港 Baikeng":                  (23.5770, 119.6390),
    "南北寮漁港 Nanbeiliao":             (23.5720, 119.6410),
    "菓葉漁港 Guoye":                    (23.5680, 119.6450),
    "龍門漁港 Longmen_PH":               (23.5610, 119.6430),
    "尖山漁港 Jianshan":                 (23.5700, 119.6340),
    "烏崁漁港 Wukan":                    (23.5530, 119.5820),
    "鎖港漁港 Suogang":                  (23.5350, 119.5770),
    "山水漁港 Shanshui":                 (23.5270, 119.5720),
    "風櫃西漁港 Fenggui_W":              (23.5300, 119.5440),
    "風櫃東漁港 Fenggui_E":              (23.5310, 119.5500),
    "蒔裡漁港 Shili":                    (23.5240, 119.5530),
    "井垵漁港 Jingan":                   (23.5290, 119.5580),
    "五德漁港 Wude":                     (23.5370, 119.5650),
    "鐵線漁港 Tiexian":                  (23.5430, 119.5630),
    "菜園漁港 Caiyuan":                  (23.5550, 119.5640),
    "石泉漁港 Shiquan":                  (23.5590, 119.5640),
    "前寮漁港 Qianliao":                 (23.5770, 119.5690),
    "案山漁港 Anshan":                   (23.5710, 119.5520),
    "馬公漁港 Magong_FP":                (23.5640, 119.5630),
    "重光漁港 Chongguang":               (23.5690, 119.5680),
    "西衛漁港 Xiwei":                    (23.5710, 119.5590),
    "安宅漁港 Anzhai":                   (23.5760, 119.5660),
    "桶盤漁港 Tongpan":                  (23.5320, 119.5280),
    "虎井漁港 Hujing":                   (23.5070, 119.5250),
    "水垵漁港 Shuian":                   (23.3700, 119.5000),
    "中社漁港 Zhongshe":                 (23.3730, 119.5050),
    "潭門漁港 Tanmen":                   (23.3680, 119.5100),
    "將軍南漁港 Jiangjun_S":             (23.3620, 119.5130),
    "將軍北漁港 Jiangjun_N":             (23.3660, 119.5160),
    "花嶼漁港 Huayu":                    (23.4050, 119.3220),
    "東嶼坪漁港 Dongyuping":             (23.2510, 119.4940),
    "東吉漁港 Dongji":                   (23.2590, 119.6660),
    "潭子漁港 Tanzi_PH":                 (23.2120, 119.4320),
    "七美漁港 Qimei":                    (23.2020, 119.4260),
    # ── 金門縣 Kinmen ──
    "復國墩漁港 Fuguodun":               (24.4120, 118.4270),
    "新湖漁港 Xinhu":                    (24.4230, 118.4150),
    "羅厝漁港 Luocuo":                   (24.4320, 118.2340),
    # ── 連江縣 Matsu ──
    "中柱漁港 Zhongzhu":                 (26.3620, 120.4830),
    "白沙漁港 Baisha":                   (26.2240, 119.9760),
    "福澳漁港 Fuao":                    (26.1608, 119.9490),
    "青帆漁港 Qingfan":                  (25.9580, 119.9380),
    "猛澳漁港 Mengao":                   (25.9540, 119.9450),
    # ── 麥寮港（工業港）──
    "麥寮港 Mailiao":                    (23.7500, 120.2500),
}

# ── 大陸沿岸主要港口/錨地（監測範圍內）──────────────────
# 大陸漁船多在自家港口/錨地/灣內旁靠補給、整補，屬例行作業而非對台灰色地帶活動，
# 應與台灣港口一併排除。值為 (lat, lon) 預設 8km，或 (lat, lon, 半徑km) 指定半徑。
# 涵蓋福建沿岸（正對台灣）為主，並含粵東、浙南鄰近海域；所有區域皆位於海峽中線
# 以西，半徑經過驗證不會觸及澎湖或海峽中央的開放海域。
CN_PORTS = {
    # ── 浙江南部 South Zhejiang ──
    "溫州 Wenzhou":          (27.9000, 120.8500),
    "蒼南 Cangnan":          (27.5000, 120.6000),
    # ── 福建 Fujian（正對台灣海峽）──
    "寧德三都澳 Ningde":      (26.6600, 119.5500),
    "福州馬尾 Fuzhou-Mawei":  (26.0500, 119.4500),
    "福州江陰 Fuzhou-Jiangyin":(25.9500, 119.6200),
    "福清灣/海壇 Fuqing":     (25.9200, 119.4000, 12.0),
    "平潭 Pingtan":           (25.5000, 119.7900),
    "莆田湄洲灣 Putian":      (25.0800, 119.1000),
    "湄洲灣泉港 Meizhouwan":  (24.9600, 119.0200, 9.0),
    "泉州 Quanzhou":          (24.8100, 118.6900),
    "廈門 Xiamen":            (24.4500, 118.0700),
    "同安灣/廈門灣北 Tongan": (24.5800, 118.1200, 11.0),
    "大嶝/圍頭灣 Weitou":     (24.5500, 118.2500, 10.0),
    "深滬灣/圍頭東 Shenhu":   (24.5900, 118.4100, 10.0),
    "漳州東山 Dongshan":      (23.7000, 117.5000),
    # ── 廣東東部 East Guangdong ──
    "汕頭 Shantou":           (23.3500, 116.6800),
    "汕頭港外 Shantou-appr":  (23.2600, 116.8000, 8.0),
    "惠來 Huilai":            (23.0300, 116.3000),
}

def is_in_port(lat, lon):
    """檢查是否在任何港口排除區域內（台灣港口 2km；大陸沿岸港口/灣內預設 8km，
    部分大型灣澳以 CN_PORTS 第三元素指定半徑）"""
    for name, (plat, plon) in PORTS.items():
        if haversine_km(lat, lon, plat, plon) < PORT_EXCLUSION_KM:
            return name
    for name, coords in CN_PORTS.items():
        plat, plon = coords[0], coords[1]
        radius = coords[2] if len(coords) > 2 else CN_PORT_EXCLUSION_KM
        if haversine_km(lat, lon, plat, plon) < radius:
            return name
    return None


_in_port_cache = {}


def is_in_port_cached(lat, lon):
    """is_in_port() 的 memo 版本 — 以 ~100m 網格 (round 3位) 快取。
    威脅評分引擎需對數萬個航跡點呼叫，錨泊/靠泊點高度重複。"""
    key = (round(lat, 3), round(lon, 3))
    hit = _in_port_cache.get(key, -1)
    if hit != -1:
        return hit
    result = is_in_port(lat, lon)
    _in_port_cache[key] = result
    return result


def _main(argv):
    if len(argv) != 2:
        print("usage: python src/geofence.py <lat> <lon>")
        return 1
    lat, lon = float(argv[0]), float(argv[1])
    result = annotate(lat, lon)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

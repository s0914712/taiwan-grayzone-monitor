#!/usr/bin/env python3
"""
================================================================================
海上旁靠偵測 — Ship-to-Ship Transfer Detection
Detect vessels alongside each other (< 10m) for 1+ hour, excluding ports.
Classify as pair trawling vs. suspicious transfer.
================================================================================
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
TRACK_HISTORY_FILE = DATA_DIR / "ais_track_history.json"
SNAPSHOT_FILE = DATA_DIR / "ais_snapshot.json"
OUTPUT_FILE = DATA_DIR / "ship_transfers.json"

# ── 門檻設定 ────────────────────────────────────────────
ALONGSIDE_DISTANCE_KM = 0.01       # 10 公尺
MAX_SPEED_KN = 5.0                 # 旁靠時速度 < 5 knots
PORT_EXCLUSION_KM = 2.0            # 港口排除半徑 2 公里
MIN_DURATION_HOURS = 1.0           # 至少旁靠 1 小時
PARALLEL_HEADING_DEG = 15          # 雙拖判定：航向差 < 15°
PAIR_TRAWL_SPEED_MIN = 2.0        # 雙拖速度下限
PAIR_TRAWL_SPEED_MAX = 6.0        # 雙拖速度上限

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

# ── 漁場定義（與 fetch_ais_data.py 一致）────────────────
FISHING_HOTSPOTS = {
    'taiwan_bank':   [[22.0, 117.0], [23.5, 119.5]],
    'penghu':        [[23.0, 119.0], [24.0, 120.0]],
    'kuroshio_east': [[22.5, 121.0], [24.5, 122.0]],
    'northeast':     [[24.8, 121.5], [25.8, 123.0]],
    'southwest':     [[22.0, 120.0], [23.0, 120.8]],
}


# ── 工具函式 ────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    """兩點間距離（公里）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def is_in_port(lat, lon):
    """檢查是否在任何港口排除區域內"""
    for name, (plat, plon) in PORTS.items():
        if haversine_km(lat, lon, plat, plon) < PORT_EXCLUSION_KM:
            return name
    return None


def is_in_fishing_hotspot(lat, lon):
    """檢查是否在漁場範圍內"""
    for name, bounds in FISHING_HOTSPOTS.items():
        if (bounds[0][0] <= lat <= bounds[1][0] and
                bounds[0][1] <= lon <= bounds[1][1]):
            return name
    return None


def heading_diff(h1, h2):
    """兩航向差的絕對值（0-180°）"""
    if h1 is None or h2 is None:
        return 180
    d = abs(h1 - h2) % 360
    return d if d <= 180 else 360 - d


def classify_transfer(v1, v2, duration_hours, in_hotspot):
    """
    分類旁靠事件並計算風險分數
    回傳: (classification, risk_score, risk_factors)
    """
    score = 0
    factors = []

    type1 = v1.get("type_name", "unknown")
    type2 = v2.get("type_name", "unknown")
    speed1 = v1.get("speed", 0) or 0
    speed2 = v2.get("speed", 0) or 0
    heading1 = v1.get("heading")
    heading2 = v2.get("heading")
    mmsi1 = str(v1.get("mmsi", ""))
    mmsi2 = str(v2.get("mmsi", ""))

    # 不同船型
    if type1 != type2:
        score += 30
        factors.append("different_types")

    # 雙方近乎靜止
    if speed1 < 1 and speed2 < 1:
        score += 15
        factors.append("stationary")

    # 非漁場內
    if not in_hotspot:
        score += 15
        factors.append("outside_hotspot")

    # 旁靠超過 3 小時
    if duration_hours > 3:
        score += 10
        factors.append("long_duration")

    # 外國籍船舶（台灣 MMSI 以 416 開頭）
    tw_flag = mmsi1.startswith("416") or mmsi1.startswith("419")
    foreign1 = not (mmsi1.startswith("416") or mmsi1.startswith("419"))
    foreign2 = not (mmsi2.startswith("416") or mmsi2.startswith("419"))
    if foreign1 or foreign2:
        score += 10
        factors.append("foreign_flag")

    # 雙拖減分：雙方都是漁船、平行航向、在漁場內、速度 2-6kn
    both_fishing = type1 == "fishing" and type2 == "fishing"
    parallel = heading_diff(heading1, heading2) < PARALLEL_HEADING_DEG
    both_moving = (PAIR_TRAWL_SPEED_MIN <= speed1 <= PAIR_TRAWL_SPEED_MAX and
                   PAIR_TRAWL_SPEED_MIN <= speed2 <= PAIR_TRAWL_SPEED_MAX)
    if both_fishing and parallel and in_hotspot and both_moving:
        score -= 30
        factors.append("pair_trawling_pattern")

    score = max(0, min(100, score))

    if score >= 40:
        classification = "suspicious"
    elif score < 20 and both_fishing:
        classification = "pair_trawling"
    else:
        classification = "normal"

    return classification, score, factors


def find_pairs_in_snapshot(vessels):
    """
    在單一快照中找出所有距離 < 10m 且速度 < 5kn 的船對
    使用 bounding box 預篩加速
    """
    pairs = []
    n = len(vessels)
    # 建立索引（排除港內船隻、無效座標、浮標/漁具）
    valid = []
    for v in vessels:
        lat = v.get("lat")
        lon = v.get("lon")
        if lat is None or lon is None:
            continue
        # 排除浮標與漁具（名稱含 % 或 BUOY）
        vname = v.get("name", "") or ""
        if "%" in vname or "BUOY" in vname.upper():
            continue
        speed = v.get("speed", 0) or 0
        if speed > MAX_SPEED_KN:
            continue
        if is_in_port(lat, lon):
            continue
        valid.append(v)

    # 按緯度排序後用 bounding box 快速篩選
    valid.sort(key=lambda v: v["lat"])
    deg_threshold = 0.001  # ~110m 的緯度，寬鬆篩選

    for i in range(len(valid)):
        v1 = valid[i]
        lat1, lon1 = v1["lat"], v1["lon"]
        for j in range(i + 1, len(valid)):
            v2 = valid[j]
            lat2, lon2 = v2["lat"], v2["lon"]
            # 緯度快速排除
            if lat2 - lat1 > deg_threshold:
                break
            # 經度快速排除
            if abs(lon2 - lon1) > deg_threshold:
                continue
            # 精確距離
            dist = haversine_km(lat1, lon1, lat2, lon2)
            if dist < ALONGSIDE_DISTANCE_KM:
                pair_key = tuple(sorted([str(v1.get("mmsi", "")), str(v2.get("mmsi", ""))]))
                pairs.append((pair_key, v1, v2, dist))

    return pairs


def process_track_history():
    """
    掃描 14 天 AIS 軌跡歷史，偵測持續旁靠事件
    """
    if not TRACK_HISTORY_FILE.exists():
        print("⚠️ 找不到 ais_track_history.json，跳過歷史分析")
        return []

    with open(TRACK_HISTORY_FILE, 'r', encoding='utf-8') as f:
        history = json.load(f)

    # Track history can be a list of snapshots or a dict with "snapshots" key
    if isinstance(history, list):
        snapshots = history
    else:
        snapshots = history.get("snapshots", [])
    if not snapshots:
        print("⚠️ ais_track_history.json 無快照資料")
        return []

    print(f"📊 載入 {len(snapshots)} 個歷史快照...")

    # 追蹤每對船的連續旁靠
    # active_pairs: {pair_key: {first_seen, last_seen, snapshots, v1_last, v2_last, min_dist}}
    active_pairs = {}
    completed_events = []

    for snap_idx, snap in enumerate(snapshots):
        ts = snap.get("timestamp", "")
        vessels = snap.get("vessels", [])
        if not vessels:
            continue

        current_pairs = {}
        for pair_key, v1, v2, dist in find_pairs_in_snapshot(vessels):
            current_pairs[pair_key] = (v1, v2, dist)

        # 更新追蹤中的 pairs
        for pk in list(active_pairs.keys()):
            if pk in current_pairs:
                v1, v2, dist = current_pairs[pk]
                active_pairs[pk]["last_seen"] = ts
                active_pairs[pk]["snapshot_count"] += 1
                active_pairs[pk]["v1_last"] = v1
                active_pairs[pk]["v2_last"] = v2
                active_pairs[pk]["min_dist"] = min(active_pairs[pk]["min_dist"], dist)
            else:
                # 旁靠結束
                ev = active_pairs.pop(pk)
                completed_events.append(ev)

        # 新增新 pairs
        for pk, (v1, v2, dist) in current_pairs.items():
            if pk not in active_pairs:
                active_pairs[pk] = {
                    "pair_key": pk,
                    "first_seen": ts,
                    "last_seen": ts,
                    "snapshot_count": 1,
                    "v1_first": v1,
                    "v2_first": v2,
                    "v1_last": v1,
                    "v2_last": v2,
                    "min_dist": dist,
                }

    # 將仍在進行中的 pairs 也加入（標記為 active）
    for pk, ev in active_pairs.items():
        ev["active"] = True
        completed_events.append(ev)

    return completed_events


def estimate_duration_hours(first_seen, last_seen, snapshot_count):
    """估算旁靠持續時間"""
    try:
        t1 = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
        t2 = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
        diff = (t2 - t1).total_seconds() / 3600
        if diff > 0:
            return round(diff, 1)
    except (ValueError, TypeError):
        pass
    # 無法解析時間時，用快照數量估算（每快照約 2 小時）
    return round(max(0, (snapshot_count - 1)) * 2, 1)


def build_vessel_info(v):
    """提取船舶資訊"""
    return {
        "mmsi": str(v.get("mmsi", "")),
        "name": v.get("name", ""),
        "type_name": v.get("type_name", "unknown"),
        "lat": v.get("lat"),
        "lon": v.get("lon"),
        "speed": v.get("speed", 0),
        "heading": v.get("heading"),
    }


def main():
    print("🚢 海上旁靠偵測開始...")

    events = process_track_history()
    print(f"📋 偵測到 {len(events)} 組旁靠事件（含不足 1 小時）")

    # 過濾 & 分類
    active_transfers = []
    history_transfers = []

    for ev in events:
        duration = estimate_duration_hours(
            ev["first_seen"], ev["last_seen"], ev["snapshot_count"]
        )
        if duration < MIN_DURATION_HOURS:
            continue

        v1 = build_vessel_info(ev["v1_last"])
        v2 = build_vessel_info(ev["v2_last"])
        avg_lat = (v1["lat"] + v2["lat"]) / 2 if v1["lat"] and v2["lat"] else None
        avg_lon = (v1["lon"] + v2["lon"]) / 2 if v1["lon"] and v2["lon"] else None

        in_hotspot = is_in_fishing_hotspot(avg_lat, avg_lon) if avg_lat else None
        classification, risk_score, risk_factors = classify_transfer(
            ev["v1_last"], ev["v2_last"], duration, in_hotspot
        )

        record = {
            "first_seen": ev["first_seen"],
            "last_seen": ev["last_seen"],
            "duration_hours": duration,
            "vessel1": v1,
            "vessel2": v2,
            "min_distance_m": round(ev["min_dist"] * 1000, 1),
            "location": {"lat": avg_lat, "lon": avg_lon},
            "classification": classification,
            "risk_score": risk_score,
            "risk_factors": risk_factors,
        }

        if ev.get("active"):
            active_transfers.append(record)
        else:
            history_transfers.append(record)

    # 也檢查當前快照
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            snap = json.load(f)
        vessels = snap.get("vessels", [])
        current_pairs = find_pairs_in_snapshot(vessels)
        # 標記當前快照中的 pairs（即時狀態，不需 1h 門檻）
        for pair_key, v1, v2, dist in current_pairs:
            pk = tuple(sorted([str(v1.get("mmsi", "")), str(v2.get("mmsi", ""))]))
            # 檢查是否已在 active_transfers 中
            already = any(
                tuple(sorted([t["vessel1"]["mmsi"], t["vessel2"]["mmsi"]])) == pk
                for t in active_transfers
            )
            if not already:
                vi1 = build_vessel_info(v1)
                vi2 = build_vessel_info(v2)
                avg_lat = (vi1["lat"] + vi2["lat"]) / 2 if vi1["lat"] and vi2["lat"] else None
                avg_lon = (vi1["lon"] + vi2["lon"]) / 2 if vi1["lon"] and vi2["lon"] else None
                in_hotspot = is_in_fishing_hotspot(avg_lat, avg_lon) if avg_lat else None
                classification, risk_score, risk_factors = classify_transfer(v1, v2, 0, in_hotspot)
                active_transfers.append({
                    "first_seen": snap.get("updated_at", ""),
                    "last_seen": snap.get("updated_at", ""),
                    "duration_hours": 0,
                    "vessel1": vi1,
                    "vessel2": vi2,
                    "min_distance_m": round(dist * 1000, 1),
                    "location": {"lat": avg_lat, "lon": avg_lon},
                    "classification": classification,
                    "risk_score": risk_score,
                    "risk_factors": risk_factors,
                })

    # 排序：可疑優先，再按風險分數
    active_transfers.sort(key=lambda x: (-x["risk_score"], x["first_seen"]))
    history_transfers.sort(key=lambda x: (-x["risk_score"], x["first_seen"]))

    # 統計
    all_events = active_transfers + history_transfers
    unique_mmsis = set()
    for t in all_events:
        unique_mmsis.add(t["vessel1"]["mmsi"])
        unique_mmsis.add(t["vessel2"]["mmsi"])

    suspicious_count = sum(1 for t in all_events if t["classification"] == "suspicious")
    trawling_count = sum(1 for t in all_events if t["classification"] == "pair_trawling")

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "active_transfers": active_transfers,
        "history": history_transfers,
        "summary": {
            "active_count": len(active_transfers),
            "history_count": len(history_transfers),
            "suspicious_count": suspicious_count,
            "pair_trawling_count": trawling_count,
            "unique_vessels": len(unique_mmsis),
            "history_days": 14,
        }
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ 旁靠偵測完成: {len(active_transfers)} 進行中, "
          f"{len(history_transfers)} 歷史, "
          f"{suspicious_count} 可疑, {trawling_count} 雙拖")
    print(f"📁 輸出: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

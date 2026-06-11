/**
 * Taiwan Gray Zone Monitor - Map Static Data & Pure Helpers
 * Static lookup tables (MID flag states, vessel colors, fishing hotspots,
 * territorial basepoints) and pure helper functions shared by map.js.
 * Must be loaded BEFORE js/map.js on every page that uses MapModule.
 */

var MapData = (function() {
    'use strict';

    const riskColors = { critical: '#ff2d55', high: '#ff7847', medium: '#ffab2e' };

    // MMSI MID → Flag State lookup (ITU MID table)
    const MID_FLAG_TABLE = {
        '201': {en:'Albania',zh:'阿爾巴尼亞'},'211': {en:'Germany',zh:'德國'},'212': {en:'Cyprus',zh:'賽普勒斯'},
        '215': {en:'Malta',zh:'馬爾他'},'224': {en:'Spain',zh:'西班牙'},'225': {en:'Spain',zh:'西班牙'},
        '226': {en:'France',zh:'法國'},'227': {en:'France',zh:'法國'},'228': {en:'France',zh:'法國'},
        '229': {en:'Malta',zh:'馬爾他'},'230': {en:'Spain',zh:'西班牙'},'231': {en:'Spain',zh:'西班牙'},
        '232': {en:'UK',zh:'英國'},'233': {en:'UK',zh:'英國'},'234': {en:'UK',zh:'英國'},
        '235': {en:'UK',zh:'英國'},'236': {en:'UK',zh:'英國'},
        '240': {en:'Greece',zh:'希臘'},'241': {en:'Greece',zh:'希臘'},
        '244': {en:'Netherlands',zh:'荷蘭'},'245': {en:'Netherlands',zh:'荷蘭'},'246': {en:'Netherlands',zh:'荷蘭'},
        '247': {en:'Italy',zh:'義大利'},'248': {en:'Malta',zh:'馬爾他'},'249': {en:'Malta',zh:'馬爾他'},
        '256': {en:'Malta',zh:'馬爾他'},'259': {en:'Norway',zh:'挪威'},
        '261': {en:'Poland',zh:'波蘭'},'271': {en:'Turkey',zh:'土耳其'},
        '272': {en:'Ukraine',zh:'烏克蘭'},'273': {en:'Russia',zh:'俄羅斯'},
        '303': {en:'USA',zh:'美國'},'304': {en:'Antigua & Barbuda',zh:'安地卡及巴布達'},
        '305': {en:'Antigua & Barbuda',zh:'安地卡及巴布達'},
        '308': {en:'Bahamas',zh:'巴哈馬'},'309': {en:'Bahamas',zh:'巴哈馬'},
        '310': {en:'Bermuda',zh:'百慕達'},'311': {en:'Bahamas',zh:'巴哈馬'},
        '312': {en:'Belize',zh:'貝里斯'},'314': {en:'Barbados',zh:'巴貝多'},
        '316': {en:'Canada',zh:'加拿大'},
        '319': {en:'Cayman Islands',zh:'開曼群島'},
        '325': {en:'Jamaica',zh:'牙買加'},'327': {en:'Jamaica',zh:'牙買加'},
        '330': {en:'Grenada',zh:'格瑞那達'},
        '338': {en:'USA',zh:'美國'},'339': {en:'USA',zh:'美國'},
        '341': {en:'St Kitts & Nevis',zh:'聖克里斯多福'},'343': {en:'St Lucia',zh:'聖露西亞'},
        '345': {en:'Mexico',zh:'墨西哥'},'347': {en:'Martinique',zh:'馬丁尼克'},
        '351': {en:'Panama',zh:'巴拿馬'},'352': {en:'Panama',zh:'巴拿馬'},'353': {en:'Panama',zh:'巴拿馬'},
        '354': {en:'Panama',zh:'巴拿馬'},'355': {en:'Panama',zh:'巴拿馬'},'356': {en:'Panama',zh:'巴拿馬'},
        '357': {en:'Panama',zh:'巴拿馬'},
        '370': {en:'Panama',zh:'巴拿馬'},'371': {en:'Panama',zh:'巴拿馬'},'372': {en:'Panama',zh:'巴拿馬'},
        '373': {en:'Panama',zh:'巴拿馬'},'374': {en:'Panama',zh:'巴拿馬'},
        '375': {en:'St Vincent',zh:'聖文森'},'376': {en:'St Vincent',zh:'聯邦'},
        '377': {en:'St Vincent',zh:'聖文森'},
        '378': {en:'British Virgin Islands',zh:'英屬維京群島'},
        '403': {en:'Saudi Arabia',zh:'沙烏地阿拉伯'},
        '405': {en:'Bangladesh',zh:'孟加拉'},'412': {en:'China',zh:'中國'},
        '413': {en:'China',zh:'中國'},'414': {en:'China',zh:'中國'},
        '416': {en:'Taiwan',zh:'台灣'},
        '417': {en:'Sri Lanka',zh:'斯里蘭卡'},
        '419': {en:'India',zh:'印度'},'422': {en:'Iran',zh:'伊朗'},
        '431': {en:'Japan',zh:'日本'},'432': {en:'Japan',zh:'日本'},
        '440': {en:'South Korea',zh:'韓國'},'441': {en:'South Korea',zh:'韓國'},
        '443': {en:'Palestine',zh:'巴勒斯坦'},
        '445': {en:'North Korea',zh:'北韓'},
        '447': {en:'Kuwait',zh:'科威特'},
        '450': {en:'Lebanon',zh:'黎巴嫩'},
        '455': {en:'Maldives',zh:'馬爾地夫'},
        '457': {en:'Jordan',zh:'約旦'},
        '459': {en:'Myanmar',zh:'緬甸'},
        '461': {en:'Oman',zh:'阿曼'},'463': {en:'Pakistan',zh:'巴基斯坦'},
        '466': {en:'Qatar',zh:'卡達'},
        '468': {en:'Syria',zh:'敘利亞'},
        '470': {en:'UAE',zh:'阿聯酋'},
        '472': {en:'Tajikistan',zh:'塔吉克'},
        '473': {en:'Yemen',zh:'葉門'},
        '477': {en:'Hong Kong',zh:'香港'},
        '478': {en:'Bosnia & Herzegovina',zh:'波赫'},
        '501': {en:'Adelie Land',zh:'法屬南方領地'},
        '503': {en:'Australia',zh:'澳洲'},'506': {en:'Myanmar',zh:'緬甸'},
        '508': {en:'Brunei',zh:'汶萊'},
        '510': {en:'Micronesia',zh:'密克羅尼西亞'},
        '511': {en:'Palau',zh:'帛琉'},'512': {en:'New Zealand',zh:'紐西蘭'},
        '514': {en:'Cambodia',zh:'柬埔寨'},
        '515': {en:'Cambodia',zh:'柬埔寨'},
        '516': {en:'Christmas Island',zh:'聖誕島'},
        '518': {en:'Cook Islands',zh:'庫克群島'},
        '520': {en:'Fiji',zh:'斐濟'},
        '523': {en:'Cocos Islands',zh:'科科斯群島'},
        '525': {en:'Indonesia',zh:'印尼'},
        '529': {en:'Kiribati',zh:'吉里巴斯'},
        '531': {en:'Laos',zh:'寮國'},'533': {en:'Malaysia',zh:'馬來西亞'},
        '536': {en:'Micronesia',zh:'密克羅尼西亞'},
        '538': {en:'Marshall Islands',zh:'馬紹爾群島'},
        '540': {en:'New Caledonia',zh:'新喀里多尼亞'},
        '542': {en:'Niue',zh:'紐埃'},'544': {en:'Nauru',zh:'諾魯'},
        '546': {en:'French Polynesia',zh:'法屬玻里尼西亞'},
        '548': {en:'Philippines',zh:'菲律賓'},
        '553': {en:'Papua New Guinea',zh:'巴布亞紐幾內亞'},
        '555': {en:'Pitcairn',zh:'皮特肯群島'},
        '557': {en:'Solomon Islands',zh:'所羅門群島'},
        '559': {en:'American Samoa',zh:'美屬薩摩亞'},
        '561': {en:'Samoa',zh:'薩摩亞'},
        '563': {en:'Singapore',zh:'新加坡'},'564': {en:'Singapore',zh:'新加坡'},
        '565': {en:'Singapore',zh:'新加坡'},'566': {en:'Singapore',zh:'新加坡'},
        '567': {en:'Thailand',zh:'泰國'},
        '570': {en:'Tonga',zh:'東加'},
        '572': {en:'Tuvalu',zh:'吐瓦魯'},'574': {en:'Vietnam',zh:'越南'},
        '576': {en:'Vanuatu',zh:'萬那杜'},
        '577': {en:'Vanuatu',zh:'萬那杜'},
        '578': {en:'Wallis & Futuna',zh:'瓦利斯群島'},
        '601': {en:'South Africa',zh:'南非'},
        '603': {en:'Angola',zh:'安哥拉'},
        '605': {en:'Algeria',zh:'阿爾及利亞'},
        '610': {en:'Cameroon',zh:'喀麥隆'},
        '612': {en:'Comoros',zh:'乘摩洛'},
        '613': {en:'Comoros',zh:'葛摩'},
        '616': {en:'Comoros',zh:'葛摩'},
        '618': {en:'Cote d\'Ivoire',zh:'象牙海岸'},
        '619': {en:'Cote d\'Ivoire',zh:'象牙海岸'},
        '620': {en:'Comoros',zh:'葛摩'},
        '621': {en:'Djibouti',zh:'吉布地'},
        '622': {en:'Egypt',zh:'埃及'},
        '624': {en:'Ethiopia',zh:'衣索比亞'},
        '625': {en:'Eritrea',zh:'厄利垂亞'},
        '626': {en:'Gabon',zh:'加彭'},
        '627': {en:'Ghana',zh:'迦納'},
        '629': {en:'Gambia',zh:'乘比亞'},
        '630': {en:'Guinea-Bissau',zh:'幾內亞比索'},
        '631': {en:'Equatorial Guinea',zh:'赤道幾內亞'},
        '632': {en:'Guinea',zh:'幾內亞'},
        '633': {en:'Burkina Faso',zh:'布吉納法索'},
        '634': {en:'Kenya',zh:'肯亞'},
        '636': {en:'Liberia',zh:'賴比瑞亞'},'637': {en:'Liberia',zh:'賴比瑞亞'},
        '642': {en:'Libya',zh:'利比亞'},
        '644': {en:'Lesotho',zh:'賴索托'},
        '645': {en:'Mauritius',zh:'模里西斯'},
        '647': {en:'Madagascar',zh:'馬達加斯加'},
        '649': {en:'Mali',zh:'馬利'},
        '650': {en:'Mozambique',zh:'莫三比克'},
        '654': {en:'Mauritania',zh:'茅利塔尼亞'},
        '655': {en:'Malawi',zh:'馬拉威'},
        '656': {en:'Niger',zh:'尼日'},
        '657': {en:'Nigeria',zh:'奈及利亞'},
        '659': {en:'Namibia',zh:'納米比亞'},
        '660': {en:'Reunion',zh:'留尼旺'},
        '661': {en:'Rwanda',zh:'盧安達'},
        '662': {en:'Sudan',zh:'蘇丹'},
        '663': {en:'Senegal',zh:'塞內加爾'},
        '664': {en:'Seychelles',zh:'塞乘爾'},
        '665': {en:'St Helena',zh:'聖乘勒拿'},
        '666': {en:'Somalia',zh:'索馬利亞'},
        '667': {en:'Sierra Leone',zh:'獅子山'},
        '668': {en:'Sao Tome',zh:'聖多美'},
        '669': {en:'Eswatini',zh:'史瓦帝尼'},
        '670': {en:'Chad',zh:'查德'},
        '671': {en:'Togo',zh:'多哥'},
        '672': {en:'Tunisia',zh:'突尼西亞'},
        '674': {en:'Tanzania',zh:'坦尚尼亞'},
        '675': {en:'Uganda',zh:'烏干達'},
        '676': {en:'DR Congo',zh:'民主剛果'},
        '677': {en:'Tanzania',zh:'坦尚尼亞'},
        '678': {en:'Zambia',zh:'乘比亞'},
        '679': {en:'Zimbabwe',zh:'辛巴威'},
        '701': {en:'Argentina',zh:'阿根廷'},
        '710': {en:'Brazil',zh:'巴西'},'720': {en:'Bolivia',zh:'玻利維亞'},
        '725': {en:'Chile',zh:'智利'},'730': {en:'Colombia',zh:'哥倫比亞'},
        '735': {en:'Ecuador',zh:'厄瓜多'},'740': {en:'UK (Falklands)',zh:'福克蘭群島'},
        '745': {en:'Guiana',zh:'法屬圭亞那'},
        '750': {en:'Guyana',zh:'蓋亞那'},
        '755': {en:'Paraguay',zh:'巴拉圭'},'760': {en:'Peru',zh:'秘魯'},
        '765': {en:'Suriname',zh:'蘇利南'},'770': {en:'Uruguay',zh:'烏拉圭'},
        '775': {en:'Venezuela',zh:'委內瑞拉'},
    };

    /**
     * Get flag state from MMSI (first 3 digits = MID)
     * @param {string} mmsi
     * @returns {string|null} localized flag name
     */
    function getMidFlag(mmsi) {
        if (!mmsi || mmsi.length < 3) return null;
        const mid = mmsi.substring(0, 3);
        const entry = MID_FLAG_TABLE[mid];
        if (!entry) return null;
        const lang = (typeof i18n !== 'undefined' && typeof i18n.getLang === 'function') ? i18n.getLang() : 'zh';
        return entry[lang] || entry.en;
    }

    // Zoom threshold: <= this shows clusters, > this shows individual markers
    const CLUSTER_ZOOM_THRESHOLD = 8;

    // Cluster region centers for aggregate display
    const CLUSTER_CENTERS = {
        taiwan_bank:   { center: [22.75, 118.25], name: '台灣灘', zoom: 9 },
        penghu:        { center: [23.5, 119.5],   name: '澎湖',   zoom: 10 },
        kuroshio_east: { center: [23.5, 121.5],   name: '東部',   zoom: 9 },
        northeast:     { center: [25.3, 122.25],  name: '東北',   zoom: 9 },
        southwest:     { center: [22.5, 120.4],   name: '西南',   zoom: 10 },
        other:         { center: [23.5, 119.5],   name: '其他海域', zoom: 8 }
    };

    // Fishing hotspots
    const FISHING_HOTSPOTS = {
        taiwan_bank: {
            name: '台灣灘漁場',
            coords: [[22.0, 117.0], [22.0, 119.5], [23.5, 119.5], [23.5, 117.0]]
        },
        penghu: {
            name: '澎湖漁場',
            coords: [[23.0, 119.0], [23.0, 120.0], [24.0, 120.0], [24.0, 119.0]]
        },
        kuroshio_east: {
            name: '東部黑潮漁場',
            coords: [[22.5, 121.0], [22.5, 122.0], [24.5, 122.0], [24.5, 121.0]]
        },
        northeast: {
            name: '東北漁場',
            coords: [[24.8, 121.5], [24.8, 123.0], [25.8, 123.0], [25.8, 121.5]]
        },
        southwest: {
            name: '西南沿岸漁場',
            coords: [[22.0, 120.0], [22.0, 120.8], [23.0, 120.8], [23.0, 120.0]]
        }
    };

    // Vessel type colors
    const VESSEL_COLORS = {
        fishing: '#00ff88',
        cargo: '#00f5ff',
        tanker: '#ff5e8a',    // Rose — kept off the warm severity ramp
        lng: '#c8ff3d',       // Lime for LNG/gas vessels (clears cable yellow)
        coastguard: '#ffffff', // White — China Coast Guard (海警) hulls
        msa: '#4d9fff',        // Blue — China MSA patrol (海巡 / 海事局)
        rescue: '#ff9500',     // Orange — China Rescue & Salvage (海救 / 救助局)
        research: '#c77dff',   // Purple — China research / intel vessels (科研/情報船)
        other: '#ff3366',
        unknown: '#888888'
    };

    // China government / special-interest vessel detection (海警/海巡/海救/科研).
    // Mirrors classify_gov_vessel() in src/fetch_ais_data.py — backend sets
    // v.gov_type / type_name; the name regex is a frontend fallback.
    const GOV_REGEX = {
        coastguard: /COAST\s*GUARD|\bCCG\d*\b|HAI\s*JING|海警/i,
        msa: /HAI\s*XUN|海巡/i,
        rescue: /(DONG|NAN|BEI)\s*HAI\s*JIU|[东東南北]海救|海救/i,
        research: /XIANG\s*YANG\s*HONG|DONG\s*FANG\s*HONG|\bTONG\s*JI\b|\bKE\s*XUE\b|\bSHI\s*YAN|\bTAN\s*SUO|ZHU\s*HAI\s*YUN|向阳红|向陽紅|东方红|東方紅|同济|同濟|科学考察|科考|勘探|海洋调查|海洋調查|实验|實驗|探索/i
    };
    const GOV_TYPES = ['coastguard', 'msa', 'rescue', 'research'];
    const GOV_BADGE_ICON = { coastguard: '🛡️', msa: '⚓', rescue: '🛟', research: '🔬' };

    function getGovType(v) {
        if (v.gov_type) return v.gov_type;
        if (GOV_TYPES.indexOf(v.type_name) !== -1) return v.type_name;
        const n = v.name || '';
        for (let i = 0; i < GOV_TYPES.length; i++) {
            if (GOV_REGEX[GOV_TYPES[i]].test(n)) return GOV_TYPES[i];
        }
        return null;
    }

    function govLabel(t) {
        const tr = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        if (t === 'coastguard') return tr('app.coastguard');
        if (t === 'msa') return tr('app.msa');
        if (t === 'rescue') return tr('app.rescue');
        if (t === 'research') return tr('app.research');
        return '';
    }

    // Flag of Convenience (FOC) MID prefixes - top flag states with strict commercial regulation
    // These are MMSI Maritime Identification Digits for major open-registry states
    const FOC_MIDS = new Set([
        '636', '637',                               // Liberia
        '351', '352', '353', '354', '355', '356',   // Panama
        '357', '370', '371', '372', '373', '374',   // Panama (cont.)
        '538',                                       // Marshall Islands
        '477',                                       // Hong Kong
        '563', '564', '565', '566',                  // Singapore
        '215', '248', '249',                         // Malta
        '308', '309', '311',                         // Bahamas
        '237', '239', '240', '241',                  // Greece
        '431', '432'                                  // Japan
    ]);
    const FOC_COMMERCIAL_TYPES = new Set(['cargo', 'tanker', 'passenger']);

    // Region colors for dark vessels
    // `taiwan_region` = single full-bbox region from fetch_gfw_data.py.
    // The older sub-region keys are retained for backward-compat with any
    // cached/legacy data.json snapshots still in the wild.
    const REGION_COLORS = {
        taiwan_region: '#ff3366',
        taiwan_strait: '#ff3366',
        east_taiwan: '#ff6b35',
        south_china_sea: '#ffd700',
        east_china_sea: '#9b59b6'
    };

    const REGION_NAMES = {
        taiwan_region: '台灣周邊海域',
        taiwan_strait: '台灣海峽',
        east_taiwan: '台灣東部',
        south_china_sea: '南海北部',
        east_china_sea: '東海'
    };

    // ── 領海基點標記 Territorial Sea Basepoint markers (內政部公告) ─────────
    var TERRITORIAL_BASEPOINT_MARKERS = {
        taiwan: [
            { id: 'T1',  name: '三貂角',   nameE: 'Sandiaojiao',    lon: 122.0078, lat: 25.0083 },
            { id: 'T2',  name: '棉花嶼',   nameE: 'Mianhuayu',      lon: 122.1091, lat: 25.4839 },
            { id: 'T3',  name: '彭佳嶼1',  nameE: 'Pengjiayu I',    lon: 122.086,  lat: 25.6299 },
            { id: 'T4',  name: '彭佳嶼2',  nameE: 'Pengjiayu II',   lon: 122.0734, lat: 25.6326 },
            { id: 'T5',  name: '麟山鼻',   nameE: 'Linshanbi',      lon: 121.5094, lat: 25.2915 },
            { id: 'T6',  name: '大崛溪',   nameE: 'Dajuexi',        lon: 121.0982, lat: 25.0682 },
            { id: 'T7',  name: '大潭',     nameE: 'Datan',          lon: 121.0329, lat: 25.0326 },
            { id: 'T8',  name: '翁公石',   nameE: 'Wenggongshi',    lon: 119.5409, lat: 23.7876 },
            { id: 'T9',  name: '花嶼1',    nameE: 'Huayu I',        lon: 119.3186, lat: 23.4119 },
            { id: 'T10', name: '花嶼3',    nameE: 'Huayu III',      lon: 119.3145, lat: 23.4035 },
            { id: 'T11', name: '花嶼2',    nameE: 'Huayu II',       lon: 119.3136, lat: 23.3993 },
            { id: 'T12', name: '貓嶼',     nameE: 'Maoyu',          lon: 119.3183, lat: 23.3247 },
            { id: 'T13', name: '七美嶼',   nameE: 'Qimeiyu',        lon: 119.4161, lat: 23.1933 },
            { id: 'T14', name: '琉球嶼',   nameE: 'Liuqiuyu',       lon: 120.3536, lat: 22.3238 },
            { id: 'T15', name: '七星岩',   nameE: 'Qixingyan',      lon: 120.8264, lat: 21.7563 },
            { id: 'T16', name: '小蘭嶼1',  nameE: 'Xiaolanyu I',    lon: 121.6135, lat: 21.9384 },
            { id: 'T17', name: '小蘭嶼2',  nameE: 'Xiaolanyu II',   lon: 121.6173, lat: 21.9497 },
            { id: 'T18', name: '飛岩',     nameE: 'Feiyan',         lon: 121.5225, lat: 22.6854 },
            { id: 'T19', name: '石梯鼻',   nameE: 'Shitibi',        lon: 121.5166, lat: 23.4833 },
            { id: 'T20', name: '烏石鼻',   nameE: 'Wushibi',        lon: 121.8621, lat: 24.4805 },
            { id: 'T21', name: '米島',     nameE: 'Midao',          lon: 121.9031, lat: 24.5994 },
            { id: 'T22', name: '龜頭岸',   nameE: "Guitou'an",      lon: 121.9647, lat: 24.8395 }
        ],
        dongsha: [
            { id: 'D1', name: '西北角',   nameE: 'Xibeijiao',       lon: 116.7655, lat: 20.7678 },
            { id: 'D2', name: '東沙北角', nameE: 'Dongshabeijiao',  lon: 116.7102, lat: 20.7344 },
            { id: 'D3', name: '東沙南角', nameE: 'Dongshananjiao',  lon: 116.6963, lat: 20.6987 },
            { id: 'D4', name: '西南角',   nameE: 'Xinanjiao',       lon: 116.7547, lat: 20.5948 }
        ]
    };

    /**
     * Offset a closed polygon outward by a given distance in nautical miles.
     * Uses radial offset from a reference center point to avoid artifacts
     * with highly concave polygons (like Taiwan's baseline wrapping around the island).
     * Each point is pushed exactly `nm` nautical miles further from the center.
     * @param {Array} pts - Array of {lat, lon} objects forming a closed polygon
     * @param {number} nm - Offset distance in nautical miles
     * @param {number} [refLat] - Reference center latitude (auto-computed if omitted)
     * @param {number} [refLon] - Reference center longitude (auto-computed if omitted)
     * @returns {Array} Array of [lat, lon] pairs (closed)
     */
    function offsetPolygonNm(pts, nm, refLat, refLon) {
        var n = pts.length;
        if (n < 3) return [];

        var kmPerNm = 1.852;
        var distKm = nm * kmPerNm;

        // Use provided reference center or compute centroid
        if (refLat === undefined || refLon === undefined) {
            refLat = 0; refLon = 0;
            for (var i = 0; i < n; i++) { refLat += pts[i].lat; refLon += pts[i].lon; }
            refLat /= n; refLon /= n;
        }

        var result = [];
        for (var i = 0; i < n; i++) {
            var cosLat = Math.cos(pts[i].lat * Math.PI / 180);
            var dx = (pts[i].lon - refLon) * cosLat * 111.32;
            var dy = (pts[i].lat - refLat) * 111.32;
            var dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 0.1) {
                result.push([pts[i].lat, pts[i].lon]);
                continue;
            }
            // Unit direction from center to this point
            var ux = dx / dist;
            var uy = dy / dist;
            // Push outward by distKm
            var offsetLat = pts[i].lat + (uy * distKm) / 111.32;
            var offsetLon = pts[i].lon + (ux * distKm) / (111.32 * cosLat);
            result.push([offsetLat, offsetLon]);
        }
        result.push(result[0]); // Close polygon
        return result;
    }

    // AIS navigational status codes → human-readable labels
    var _NAV_STATUS = {
        '0': '航行中(主機)', '1': '錨泊', '2': '失控', '3': '操縱受限',
        '4': '吃水受限', '5': '繫泊', '6': '擱淺', '7': '捕魚中',
        '8': '帆行中', '11': '拖帶中', '12': '推頂中', '14': 'AIS-SART', '15': ''
    };
    function _decodeNavStatus(code) {
        if (code === undefined || code === null || code === '') return '';
        return _NAV_STATUS[String(code)] || '';
    }

    /**
     * Simple trailing-edge debounce
     */
    function debounce(fn, ms) {
        var timer = null;
        return function() {
            var args = arguments;
            clearTimeout(timer);
            timer = setTimeout(function() { fn.apply(null, args); }, ms);
        };
    }

    /**
     * Create a MarineTraffic-style triangle SVG icon
     * @param {string} [label] - accessible name for the marker (defaults to 'vessel')
     */
    function createVesselIcon(color, isSuspicious, heading, label) {
        const w = isSuspicious ? 10 : 7;
        const h = isSuspicious ? 20 : 16;
        const rotation = heading !== null && heading !== undefined ? heading : 0;
        const opacity = isSuspicious ? 0.85 : 0.7;
        const sw = isSuspicious ? 1.5 : 0.8;

        let shape;
        if (heading !== null && heading !== undefined) {
            // Narrow arrow with notch — shows heading direction clearly
            const cx = w / 2;
            const notch = h * 0.7;
            shape = '<polygon points="' + cx + ',0 ' + w + ',' + h + ' ' + cx + ',' + notch + ' 0,' + h + '" ' +
                    'fill="' + color + '" fill-opacity="' + opacity + '" ' +
                    'stroke="' + color + '" stroke-width="' + sw + '" stroke-opacity="0.9"/>';
        } else {
            // Diamond for unknown heading
            const cx = w / 2;
            const cy = h / 2;
            shape = '<polygon points="' + cx + ',0 ' + w + ',' + cy + ' ' + cx + ',' + h + ' 0,' + cy + '" ' +
                    'fill="' + color + '" fill-opacity="' + opacity + '" ' +
                    'stroke="' + color + '" stroke-width="' + sw + '" stroke-opacity="0.9"/>';
        }

        const ariaLabel = (label || 'vessel').replace(/"/g, '&quot;');
        const svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" ' +
                    'xmlns="http://www.w3.org/2000/svg" ' +
                    'role="img" aria-label="' + ariaLabel + '" ' +
                    'style="transform:rotate(' + rotation + 'deg)">' +
                    shape + '</svg>';

        return L.divIcon({
            html: svg,
            className: 'vessel-icon',
            iconSize: [w, h],
            iconAnchor: [w / 2, h / 2],
            popupAnchor: [0, -h / 2]
        });
    }

    return {
        riskColors,
        MID_FLAG_TABLE,
        getMidFlag,
        CLUSTER_ZOOM_THRESHOLD,
        CLUSTER_CENTERS,
        FISHING_HOTSPOTS,
        VESSEL_COLORS,
        GOV_REGEX,
        GOV_TYPES,
        GOV_BADGE_ICON,
        getGovType,
        govLabel,
        FOC_MIDS,
        FOC_COMMERCIAL_TYPES,
        REGION_COLORS,
        REGION_NAMES,
        TERRITORIAL_BASEPOINT_MARKERS,
        offsetPolygonNm,
        _NAV_STATUS,
        _decodeNavStatus,
        debounce,
        createVesselIcon,
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapData;
}

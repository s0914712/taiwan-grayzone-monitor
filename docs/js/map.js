/**
 * Taiwan Gray Zone Monitor - Map Module
 * Handles Leaflet map initialization and vessel/zone rendering
 */

const MapModule = (function() {
    'use strict';

    let map;
    let layers = {
        fishingHotspots: null,
        vessels: null,
        suspiciousVessels: null,
        darkVessels: null,
        submarineCables: null,
        vesselRoutes: null,
        territorialBaseline: null
    };
    let vesselMarkers = {};

    // Cached vessel data for zoom-based re-rendering
    let cachedVesselList = [];
    let cachedVessels = new Map();
    let cachedStats = { total: 0, fishing: 0, cargo: 0, tanker: 0, suspicious: 0 };

    // UN sanctions lookup (loaded on init)
    var sanctionsNameSet = new Set();
    var sanctionsImoSet = new Set();
    var sanctionsByName = {};  // uppercase name -> sanction entry

    // Suspicious vessel data reference (set by app.js)
    let _suspiciousData = null;

    // Risk level colors (used in suspicious markers + info cards)
    const riskColors = { critical: '#ff3366', high: '#ff6b35', medium: '#ffd700' };

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
        tanker: '#ff6b35',
        lng: '#f0e130',       // Yellow for LNG/gas vessels
        other: '#ff3366',
        unknown: '#888888'
    };

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

    let filterFocEnabled = false;

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

    /**
     * Initialize the Leaflet map
     */
    function init(containerId = 'map', options = {}) {
        const defaultOptions = {
            center: [24.0, 121.0],
            zoom: 7,
            zoomControl: true,
            attributionControl: false
        };

        map = L.map(containerId, { ...defaultOptions, ...options });

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 18,
            opacity: 0.9
        }).addTo(map);

        // Create layer groups
        layers.fishingHotspots = L.layerGroup().addTo(map);
        layers.vessels = L.layerGroup().addTo(map);
        layers.suspiciousVessels = L.layerGroup().addTo(map);
        layers.darkVessels = L.layerGroup().addTo(map);
        layers.submarineCables = L.layerGroup();
        layers.vesselRoutes = L.layerGroup().addTo(map);
        layers.territorialBaseline = L.layerGroup();

        // Draw Taiwan outline


        // Zoom/move events for cluster <-> detail transitions
        map.on('zoomend', () => {
            if (cachedVesselList.length > 0) renderVesselsForZoom();
        });
        map.on('moveend', () => {
            if (cachedVesselList.length > 0 && map.getZoom() > CLUSTER_ZOOM_THRESHOLD) {
                renderVesselsForZoom();
            }
        });

        // Bind Enter key on MMSI search input
        var mmsiInput = document.getElementById('mmsiSearchInput');
        if (mmsiInput) {
            mmsiInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') searchVesselRoute();
            });
        }

        // Load UN sanctions list for vessel warnings
        loadSanctionsList();

        return map;
    }


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

    // ── 詳細領海基線座標（從內政部 SHP 檔案轉換，存於 data/territorial_baseline.json）──
    var TERRITORIAL_BASELINE_DETAILED = null; // Loaded async

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

    /**
     * Draw territorial sea baseline, 12nm territorial sea limit,
     * and 24nm contiguous zone limit (領海基線 + 領海外界線 + 鄰接區外界線)
     * Uses detailed baseline from SHP data (territorial_baseline.json)
     */
    function drawTerritorialBaseline() {
        var layer = layers.territorialBaseline;
        layer.clearLayers();

        // Load detailed baseline from SHP-derived JSON, then draw
        if (TERRITORIAL_BASELINE_DETAILED) {
            _drawBaselineLayers(layer);
        } else {
            fetch('data/territorial_baseline.json')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    TERRITORIAL_BASELINE_DETAILED = data;
                    _drawBaselineLayers(layer);
                })
                .catch(function(err) {
                    console.warn('Failed to load territorial_baseline.json, using basepoints:', err);
                    _drawBaselineFromPoints(layer);
                });
        }
    }

    // Reference center points for radial offset (approximate geographic center)
    var REGION_CENTERS = {
        taiwan:  { lat: 23.65, lon: 120.90 },  // Central Taiwan
        dongsha: { lat: 20.70, lon: 116.72 }   // Dongsha Atoll center
    };

    /**
     * Draw baseline layers using detailed SHP coordinates
     */
    function _drawBaselineLayers(layer) {
        var lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';

        ['taiwan', 'dongsha'].forEach(function(region) {
            var markers = TERRITORIAL_BASEPOINT_MARKERS[region];
            var detailed = TERRITORIAL_BASELINE_DETAILED[region];
            var center = REGION_CENTERS[region];
            var regionLabel = lang === 'en'
                ? (region === 'taiwan' ? 'Taiwan' : 'Dongsha')
                : (region === 'taiwan' ? '台灣本島及附屬島嶼' : '東沙群島');

            // ── 1. Baseline 領海基線 (purple dashed) — from detailed SHP data ──
            var baseLatLngs = detailed.map(function(p) { return [p[1], p[0]]; });

            L.polyline(baseLatLngs, {
                color: '#e040fb',
                weight: 2,
                opacity: 0.8,
                dashArray: '8,5'
            }).addTo(layer).bindTooltip(
                (lang === 'en' ? 'Territorial Baseline — ' : '領海基線 — ') + regionLabel,
                { sticky: true }
            );

            // Basepoint markers
            markers.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {
                    radius: 3.5,
                    fillColor: '#e040fb',
                    color: '#fff',
                    weight: 1,
                    fillOpacity: 0.9
                }).addTo(layer).bindTooltip(
                    p.id + ' ' + (lang === 'en' ? p.nameE : p.name),
                    { permanent: false, direction: 'top', offset: [0, -6] }
                );
            });

            // Use basepoint markers for offset (uniform spacing, avoids
            // 125-point Pengjia cluster skewing the offset calculation)
            var offsetPts = markers.map(function(p) { return { lat: p.lat, lon: p.lon }; });

            // ── 2. Territorial Sea Limit 領海外界線 12nm (cyan dashed) ──
            var ts12 = offsetPolygonNm(offsetPts, 12, center.lat, center.lon);
            if (ts12.length > 0) {
                L.polyline(ts12, {
                    color: '#00f5ff',
                    weight: 1.8,
                    opacity: 0.6,
                    dashArray: '12,6'
                }).addTo(layer).bindTooltip(
                    (lang === 'en' ? 'Territorial Sea 12nm — ' : '領海外界線 12 浬 — ') + regionLabel,
                    { sticky: true }
                );
            }

            // ── 3. Contiguous Zone Limit 鄰接區外界線 24nm (yellow dashed) ──
            var cz24 = offsetPolygonNm(offsetPts, 24, center.lat, center.lon);
            if (cz24.length > 0) {
                L.polyline(cz24, {
                    color: '#ffd700',
                    weight: 1.5,
                    opacity: 0.45,
                    dashArray: '10,8'
                }).addTo(layer).bindTooltip(
                    (lang === 'en' ? 'Contiguous Zone 24nm — ' : '鄰接區外界線 24 浬 — ') + regionLabel,
                    { sticky: true }
                );
            }
        });
    }

    /**
     * Fallback: draw baseline from basepoints only (if JSON load fails)
     */
    function _drawBaselineFromPoints(layer) {
        var lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';

        ['taiwan', 'dongsha'].forEach(function(region) {
            var pts = TERRITORIAL_BASEPOINT_MARKERS[region];
            var center = REGION_CENTERS[region];
            var regionLabel = lang === 'en'
                ? (region === 'taiwan' ? 'Taiwan' : 'Dongsha')
                : (region === 'taiwan' ? '台灣本島及附屬島嶼' : '東沙群島');

            var baseLatLngs = pts.map(function(p) { return [p.lat, p.lon]; });
            baseLatLngs.push(baseLatLngs[0]);

            L.polyline(baseLatLngs, {
                color: '#e040fb', weight: 2, opacity: 0.8, dashArray: '8,5'
            }).addTo(layer).bindTooltip(
                (lang === 'en' ? 'Territorial Baseline — ' : '領海基線 — ') + regionLabel,
                { sticky: true }
            );

            pts.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {
                    radius: 3.5, fillColor: '#e040fb', color: '#fff', weight: 1, fillOpacity: 0.9
                }).addTo(layer).bindTooltip(
                    p.id + ' ' + (lang === 'en' ? p.nameE : p.name),
                    { permanent: false, direction: 'top', offset: [0, -6] }
                );
            });

            var ts12 = offsetPolygonNm(pts, 12, center.lat, center.lon);
            if (ts12.length > 0) {
                L.polyline(ts12, {
                    color: '#00f5ff', weight: 1.8, opacity: 0.6, dashArray: '12,6'
                }).addTo(layer);
            }
            var cz24 = offsetPolygonNm(pts, 24, center.lat, center.lon);
            if (cz24.length > 0) {
                L.polyline(cz24, {
                    color: '#ffd700', weight: 1.5, opacity: 0.45, dashArray: '10,8'
                }).addTo(layer);
            }
        });
    }

    /**
     * Draw fishing hotspots on the map
     */
    function drawFishingHotspots() {
        layers.fishingHotspots.clearLayers();

        Object.entries(FISHING_HOTSPOTS).forEach(([key, hotspot]) => {
            const polygon = L.polygon(hotspot.coords, {
                color: '#00ff88',
                weight: 1,
                opacity: 0.4,
                fillColor: '#00ff88',
                fillOpacity: 0.06,
                dashArray: '3, 6'
            }).addTo(layers.fishingHotspots);

            polygon.bindTooltip(hotspot.name, { permanent: false, direction: 'center' });
        });
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
     * Create a MarineTraffic-style triangle SVG icon
     */
    function createVesselIcon(color, isSuspicious, heading) {
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

        const svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" ' +
                    'xmlns="http://www.w3.org/2000/svg" ' +
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

    /**
     * Load UN sanctions vessel list for matching
     */
    function loadSanctionsList() {
        fetch('un_sanctions_vessels.json?' + Date.now())
            .then(function(res) { return res.ok ? res.json() : null; })
            .then(function(data) {
                if (!data || !data.vessels) return;
                data.vessels.forEach(function(v) {
                    var name = (v.name || '').toUpperCase().trim();
                    if (name) {
                        sanctionsNameSet.add(name);
                        sanctionsByName[name] = v;
                    }
                    if (v.imo) sanctionsImoSet.add(v.imo);
                });
                console.log('UN sanctions loaded:', sanctionsNameSet.size, 'vessels');
            })
            .catch(function() { /* sanctions file not available */ });
    }

    /**
     * Check if a vessel matches sanctions list (by name)
     */
    function getSanctionMatch(vesselName) {
        if (!vesselName) return null;
        var upper = vesselName.toUpperCase().trim();
        if (sanctionsNameSet.has(upper)) return sanctionsByName[upper];
        return null;
    }

    /**
     * Display vessels on the map
     */
    function displayVessels(vesselList, vessels = new Map()) {
        layers.vessels.clearLayers();
        vesselMarkers = {};

        let stats = { total: 0, fishing: 0, cargo: 0, tanker: 0, suspicious: 0 };

        vesselList.forEach(v => {
            // Filter out FOC commercial vessels if enabled
            if (filterFocEnabled) {
                const mid = (v.mmsi || '').substring(0, 3);
                if (FOC_MIDS.has(mid) && FOC_COMMERCIAL_TYPES.has(v.type_name)) {
                    vessels.set(v.mmsi, v);
                    return; // skip rendering but keep in data
                }
            }

            vessels.set(v.mmsi, v);
            stats.total++;

            const isSuspicious = v.suspicious;
            const isLng = v.is_lng || /\b(LNG|LPG|FSRU|GAS)\b/i.test(v.name || '');
            const color = isSuspicious ? '#ff3366' : isLng ? VESSEL_COLORS.lng : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.other);

            // Add glow effect for suspicious vessels
            if (isSuspicious) {
                stats.suspicious++;
                L.circleMarker([v.lat, v.lon], {
                    radius: 12,
                    fillColor: '#ff3366',
                    color: '#ff3366',
                    weight: 1,
                    opacity: 0.3,
                    fillOpacity: 0.15
                }).addTo(layers.vessels);
            }

            // Add glow for LNG vessels
            if (isLng) {
                L.circleMarker([v.lat, v.lon], {
                    radius: 14,
                    fillColor: VESSEL_COLORS.lng,
                    color: VESSEL_COLORS.lng,
                    weight: 1,
                    opacity: 0.25,
                    fillOpacity: 0.10
                }).addTo(layers.vessels);
            }

            const heading = v.heading !== undefined && v.heading !== null ? v.heading : null;
            const icon = createVesselIcon(color, isSuspicious || isLng, heading);
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const suspiciousInfo = isSuspicious
                ? `<br><b style="color:#ff3366">${t('app.csis_suspicious')}</b>`
                : '';
            const sanctionHit = getSanctionMatch(v.name);
            const sanctionInfo = sanctionHit
                ? `<br><span class="sanction-warning">${t('app.sanctioned')} (${t('app.sanction_res')} ${sanctionHit.resolution || '1718'})</span>`
                : '';
            const destInfo = v.destination
                ? '<br>📍 Dest: ' + v.destination
                : '';
            const navLabel = _decodeNavStatus(v.nav_status);
            const navInfo = navLabel ? '<br>狀態: ' + navLabel : '';
            const imoInfo = v.imo && v.imo !== '0' ? '<br>IMO: ' + v.imo : '';
            const lngBadge = isLng
                ? '<br><b style="color:' + VESSEL_COLORS.lng + '">⛽ LNG/Gas Carrier</b>'
                : '';

            // External lookup: MarineTraffic by MMSI for from/destination details
            const mtLink = '<br><a class="mt-lookup-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' +
                v.mmsi + '" target="_blank" rel="noopener">🔎 From / Dest 查詢</a>';

            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';
            const netMarkerNote = (v.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
            const flagName = getMidFlag(v.mmsi);
            const flagLine = flagName ? '<br>' + t('app.flag') + ' ' + flagName : '';

            marker.bindPopup(`
                <b>${v.name || 'Unknown'}</b><br>
                ${t('app.mmsi')} ${v.mmsi}${imoInfo}<br>
                ${t('app.type')} ${v.type_name || t('common.unknown')}${flagLine}<br>
                ${t('app.speed')} ${(v.speed || 0).toFixed(1)} kn<br>
                航向: ${headingText}${navInfo}${lngBadge}${destInfo}${suspiciousInfo}${sanctionInfo}${routeLink}${mtLink}${netMarkerNote}
            `);

            vesselMarkers[v.mmsi] = marker;

            // Count by type
            if (v.type_name === 'fishing') stats.fishing++;
            if (v.type_name === 'cargo') stats.cargo++;
            if (v.type_name === 'tanker') stats.tanker++;
        });

        return { stats, vessels };
    }

    /**
     * Compute stats from full vessel list (independent of what is rendered)
     */
    function computeVesselStats(vesselList) {
        let stats = { total: 0, fishing: 0, cargo: 0, tanker: 0, suspicious: 0 };
        vesselList.forEach(v => {
            if (filterFocEnabled) {
                const mid = (v.mmsi || '').substring(0, 3);
                if (FOC_MIDS.has(mid) && FOC_COMMERCIAL_TYPES.has(v.type_name)) return;
            }
            stats.total++;
            if (v.suspicious) stats.suspicious++;
            if (v.type_name === 'fishing') stats.fishing++;
            if (v.type_name === 'cargo') stats.cargo++;
            if (v.type_name === 'tanker') stats.tanker++;
        });
        return stats;
    }

    /**
     * Display cluster markers (zoom <= threshold)
     */
    function displayVesselClusters(vesselList) {
        layers.vessels.clearLayers();
        vesselMarkers = {};

        // Group by in_fishing_hotspot
        const groups = {};
        Object.keys(CLUSTER_CENTERS).forEach(k => { groups[k] = { total: 0, fishing: 0, cargo: 0, suspicious: 0 }; });

        vesselList.forEach(v => {
            if (filterFocEnabled) {
                const mid = (v.mmsi || '').substring(0, 3);
                if (FOC_MIDS.has(mid) && FOC_COMMERCIAL_TYPES.has(v.type_name)) return;
            }
            const region = v.in_fishing_hotspot || 'other';
            if (!groups[region]) groups[region] = { total: 0, fishing: 0, cargo: 0, suspicious: 0 };
            groups[region].total++;
            if (v.type_name === 'fishing') groups[region].fishing++;
            if (v.type_name === 'cargo') groups[region].cargo++;
            if (v.suspicious) groups[region].suspicious++;
        });

        Object.entries(groups).forEach(([region, g]) => {
            if (g.total === 0) return;
            const info = CLUSTER_CENTERS[region];
            if (!info) return;

            // Size proportional to count
            const r = Math.max(28, Math.min(55, 20 + Math.sqrt(g.total) * 2));
            const suspBadge = g.suspicious > 0
                ? '<div style="color:#ff3366;font-size:9px;font-weight:700">' + g.suspicious + ' suspicious</div>'
                : '';

            const icon = L.divIcon({
                className: 'vessel-cluster-wrapper',
                iconSize: [r, r],
                iconAnchor: [r / 2, r / 2],
                html: '<div class="vessel-cluster" style="width:' + r + 'px;height:' + r + 'px">' +
                      '<span class="cluster-count">' + g.total + '</span>' +
                      '<span class="cluster-label">' + info.name + '</span>' +
                      suspBadge + '</div>'
            });

            L.marker(info.center, { icon: icon })
                .addTo(layers.vessels)
                .on('click', () => { map.flyTo(info.center, info.zoom); });
        });
    }

    /**
     * Display individual vessels only within current viewport (zoom > threshold)
     */
    function displayVesselsInBounds(vesselList, vessels) {
        layers.vessels.clearLayers();
        vesselMarkers = {};

        const bounds = map.getBounds();

        vesselList.forEach(v => {
            if (filterFocEnabled) {
                const mid = (v.mmsi || '').substring(0, 3);
                if (FOC_MIDS.has(mid) && FOC_COMMERCIAL_TYPES.has(v.type_name)) return;
            }

            // Viewport culling
            if (!bounds.contains([v.lat, v.lon])) {
                vessels.set(v.mmsi, v);
                return;
            }

            vessels.set(v.mmsi, v);

            const isSuspicious = v.suspicious;
            const color = isSuspicious ? '#ff3366' : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.other);

            if (isSuspicious) {
                L.circleMarker([v.lat, v.lon], {
                    radius: 12, fillColor: '#ff3366', color: '#ff3366',
                    weight: 1, opacity: 0.3, fillOpacity: 0.15
                }).addTo(layers.vessels);
            }

            const heading = v.heading !== undefined && v.heading !== null ? v.heading : null;
            const icon = createVesselIcon(color, isSuspicious, heading);
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const suspiciousInfo = isSuspicious
                ? '<br><b style="color:#ff3366">' + t('app.csis_suspicious') + '</b>' : '';
            var sanctionHit2 = getSanctionMatch(v.name);
            var sanctionInfo2 = sanctionHit2
                ? '<br><span class="sanction-warning">' + t('app.sanctioned') + ' (' + t('app.sanction_res') + ' ' + (sanctionHit2.resolution || '1718') + ')</span>'
                : '';
            var destInfo2 = v.destination ? '<br>📍 Dest: ' + v.destination : '';
            var navInfo2 = _decodeNavStatus(v.nav_status);
            navInfo2 = navInfo2 ? '<br>狀態: ' + navInfo2 : '';
            var imoInfo2 = v.imo && v.imo !== '0' ? '<br>IMO: ' + v.imo : '';
            var mtLink2 = '<br><a class="mt-lookup-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' +
                v.mmsi + '" target="_blank" rel="noopener">🔎 From / Dest 查詢</a>';
            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';
            var netMarkerNote2 = (v.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
            var flagName2 = getMidFlag(v.mmsi);
            var flagLine2 = flagName2 ? '<br>' + t('app.flag') + ' ' + flagName2 : '';

            marker.bindPopup(
                '<b>' + (v.name || 'Unknown') + '</b><br>' +
                t('app.mmsi') + ' ' + v.mmsi + imoInfo2 + '<br>' +
                t('app.type') + ' ' + (v.type_name || t('common.unknown')) + flagLine2 + '<br>' +
                t('app.speed') + ' ' + (v.speed || 0).toFixed(1) + ' kn<br>' +
                '航向: ' + headingText + navInfo2 + destInfo2 + suspiciousInfo + sanctionInfo2 + routeLink + mtLink2 + netMarkerNote2
            );

            vesselMarkers[v.mmsi] = marker;
        });
    }

    /**
     * Main render function — decides cluster vs detail based on zoom
     * Returns stats (always computed from full list)
     */
    function renderVesselsForZoom(vesselList, vessels) {
        // Update cache if new data provided
        if (vesselList) {
            cachedVesselList = vesselList;
            cachedVessels = vessels || new Map();
            cachedStats = computeVesselStats(vesselList);
        }

        if (cachedVesselList.length === 0) return { stats: cachedStats, vessels: cachedVessels };

        if (map.getZoom() <= CLUSTER_ZOOM_THRESHOLD) {
            displayVesselClusters(cachedVesselList);
        } else {
            displayVesselsInBounds(cachedVesselList, cachedVessels);
        }

        return { stats: cachedStats, vessels: cachedVessels };
    }

    /**
     * Show/hide route loading spinner
     */
    function showRouteLoading(show, msg) {
        var spinner = document.getElementById('routeLoadingSpinner');
        if (show) {
            if (!spinner) {
                spinner = document.createElement('div');
                spinner.id = 'routeLoadingSpinner';
                document.body.appendChild(spinner);
            }
            var t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
            spinner.innerHTML = '<div class="route-spinner"></div><span>' +
                (msg || t('app.loading_track')) + '</span>';
            spinner.className = 'active';
        } else {
            if (spinner) spinner.className = '';
        }
    }

    /**
     * Update loading spinner message text
     */
    function updateRouteLoadingMsg(msg) {
        var spinner = document.getElementById('routeLoadingSpinner');
        if (spinner) {
            var span = spinner.querySelector('span');
            if (span) span.textContent = msg;
        }
    }

    /**
     * Show track info panel on the map
     */
    function showTrackInfoPanel(data, source) {
        var panel = document.getElementById('trackInfoPanel');
        var mapEl = document.getElementById('map');
        if (!panel && mapEl) {
            panel = document.createElement('div');
            panel.id = 'trackInfoPanel';
            mapEl.appendChild(panel);
        }
        if (!panel) return;

        var t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
        var first = data.track[0];
        var last = data.track[data.track.length - 1];
        var startDate = new Date(first.t).toLocaleDateString();
        var endDate = new Date(last.t).toLocaleDateString();
        var points = data.track.length;
        var sourceName = source === 'history' ? t('app.track_source_live') : t('app.track_source_pre');

        panel.innerHTML =
            '<div class="track-info-header">' + (data.name || 'Unknown') + '</div>' +
            '<div class="track-info-body">' +
                '<div>MMSI ' + data.mmsi + '</div>' +
                ((data.mmsi || '').startsWith('898') ? '<div style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</div>' : '') +
                '<div>' + startDate + ' ~ ' + endDate + ' (' + points + 'pts)</div>' +
            '</div>' +
            '<div class="track-action-row">' +
                '<button class="track-snapshot-btn" onclick="MapModule.snapshotMap(); return false;">' +
                    t('app.snapshot') + '</button>' +
                '<button class="track-clear-btn" onclick="MapModule.clearVesselRoute(); return false;">' +
                    t('app.clear_track') + '</button>' +
            '</div>';
        panel.className = 'active';
    }

    /**
     * Hide track info panel
     */
    function hideTrackInfoPanel() {
        var panel = document.getElementById('trackInfoPanel');
        if (panel) panel.className = '';
    }

    /**
     * Snapshot map to clipboard (uses html2canvas)
     */
    async function snapshotMap() {
        var t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
        var mapEl = document.getElementById('map');
        if (!mapEl) return;

        // Temporarily hide track info panel so it doesn't appear in snapshot
        var panel = document.getElementById('trackInfoPanel');
        if (panel) panel.style.visibility = 'hidden';

        try {
            if (typeof html2canvas === 'undefined') {
                showRouteToast(t('app.snapshot_fail'));
                return;
            }
            var canvas = await html2canvas(mapEl, {
                useCORS: true,
                allowTaint: true,
                backgroundColor: '#0a1628',
                scale: 2
            });
            canvas.toBlob(async function(blob) {
                if (blob && navigator.clipboard && window.ClipboardItem) {
                    await navigator.clipboard.write([
                        new ClipboardItem({ 'image/png': blob })
                    ]);
                    showRouteToast(t('app.snapshot_ok'));
                } else {
                    // Fallback: download
                    var url = canvas.toDataURL('image/png');
                    var a = document.createElement('a');
                    a.href = url;
                    a.download = 'map-snapshot.png';
                    a.click();
                    showRouteToast(t('app.snapshot_saved'));
                }
            }, 'image/png');
        } catch (e) {
            console.error('Snapshot failed:', e);
            showRouteToast(t('app.snapshot_fail'));
        } finally {
            if (panel) panel.style.visibility = '';
        }
    }

    /**
     * Fallback: extract vessel route from ais_track_history.json
     */
    var cachedTrackHistory = null;
    async function extractRouteFromHistory(mmsi) {
        var t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
        updateRouteLoadingMsg(t('app.extracting_track'));

        if (!cachedTrackHistory) {
            var res = await fetch('ais_track_history.json?' + Date.now());
            if (!res.ok) return null;
            cachedTrackHistory = await res.json();
        }
        if (!Array.isArray(cachedTrackHistory)) return null;

        var track = [];
        var vesselName = '';
        var vesselType = '';

        for (var i = 0; i < cachedTrackHistory.length; i++) {
            var snapshot = cachedTrackHistory[i];
            var vessels = snapshot.vessels || [];
            for (var j = 0; j < vessels.length; j++) {
                var v = vessels[j];
                if (String(v.mmsi) === String(mmsi)) {
                    if (!vesselName && v.name) vesselName = v.name;
                    if (!vesselType && v.type_name) vesselType = v.type_name;
                    track.push({
                        t: snapshot.timestamp,
                        lat: v.lat,
                        lon: v.lon,
                        speed: v.speed || 0,
                        heading: v.heading || 0
                    });
                }
            }
        }

        if (track.length < 2) return null;

        // Sort by timestamp
        track.sort(function(a, b) { return new Date(a.t) - new Date(b.t); });

        // Deduplicate consecutive identical positions
        var deduped = [track[0]];
        for (var k = 1; k < track.length; k++) {
            if (track[k].lat !== deduped[deduped.length - 1].lat ||
                track[k].lon !== deduped[deduped.length - 1].lon) {
                deduped.push(track[k]);
            } else if (k === track.length - 1) {
                deduped.push(track[k]);
            }
        }

        if (deduped.length < 2) return null;

        return {
            mmsi: mmsi,
            name: vesselName || 'MMSI ' + mmsi,
            type: vesselType,
            source: 'history',
            track: deduped
        };
    }

    /**
     * Render route polyline + markers on the map
     */
    function renderRoute(data) {
        var points = data.track.map(function(p) { return [p.lat, p.lon]; });
        var tooltipLabel = data.name + (data.source === 'history' ? ' — AIS 歷史航跡' : ' — 14 日航跡');

        // Draw route polyline
        L.polyline(points, {
            color: '#ffd700',
            weight: 2.5,
            opacity: 0.7,
            dashArray: '6,4'
        }).addTo(layers.vesselRoutes)
          .bindTooltip(tooltipLabel, { sticky: true });

        // Start marker (green)
        var first = data.track[0];
        L.circleMarker([first.lat, first.lon], {
            radius: 5, fillColor: '#00ff88', color: '#fff', weight: 1.5, fillOpacity: 0.9
        }).addTo(layers.vesselRoutes)
          .bindTooltip('起點 ' + new Date(first.t).toLocaleDateString(), { permanent: false });

        // End marker (red)
        var last = data.track[data.track.length - 1];
        L.circleMarker([last.lat, last.lon], {
            radius: 5, fillColor: '#ff3366', color: '#fff', weight: 1.5, fillOpacity: 0.9
        }).addTo(layers.vesselRoutes)
          .bindTooltip('終點 ' + new Date(last.t).toLocaleDateString(), { permanent: false });

        // Intermediate time markers (every 12 points ≈ once per day)
        data.track.forEach(function(p, i) {
            if (i > 0 && i < data.track.length - 1 && i % 12 === 0) {
                L.circleMarker([p.lat, p.lon], {
                    radius: 3, fillColor: '#ffd700', color: '#ffd700', weight: 1, fillOpacity: 0.6
                }).addTo(layers.vesselRoutes)
                  .bindTooltip(new Date(p.t).toLocaleDateString(), { permanent: false });
            }
        });

        // Zoom to route
        map.fitBounds(L.polyline(points).getBounds().pad(0.2));
    }

    /**
     * Load and display vessel route (with fallback to history extraction)
     */
    async function loadVesselRoute(mmsi) {
        layers.vesselRoutes.clearLayers();
        hideTrackInfoPanel();
        showRouteLoading(true);

        try {
            var data = null;
            var source = 'pre';

            // Try pre-generated route file first
            var res = await fetch('vessel_routes/' + mmsi + '.json?' + Date.now());
            if (res.ok) {
                data = await res.json();
                if (!data.track || data.track.length === 0) data = null;
            }

            // Fallback: extract from ais_track_history.json
            if (!data) {
                data = await extractRouteFromHistory(mmsi);
                source = 'history';
            }

            showRouteLoading(false);

            if (!data) {
                var t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
                showRouteToast(t('app.no_track_data') + ' (MMSI ' + mmsi + ')');
                return;
            }

            renderRoute(data);
            showTrackInfoPanel(data, source);

        } catch (e) {
            console.error('Load vessel route failed:', e);
            showRouteLoading(false);
            var t2 = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : function(k) { return k; };
            showRouteToast(t2('app.track_load_fail'));
        }
    }

    function clearVesselRoute() {
        layers.vesselRoutes.clearLayers();
        hideTrackInfoPanel();
    }

    /**
     * Show a brief toast message for route operations
     */
    function showRouteToast(msg) {
        var toast = document.getElementById('routeToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'routeToast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.style.opacity = '1';
        clearTimeout(toast._timer);
        toast._timer = setTimeout(function() { toast.style.opacity = '0'; }, 3000);
    }

    /**
     * Search vessel route by MMSI from the search box
     */
    function searchVesselRoute() {
        var input = document.getElementById('mmsiSearchInput');
        if (!input) return;
        var mmsi = input.value.trim();
        if (!mmsi || !/^\d{5,9}$/.test(mmsi)) {
            showRouteToast('請輸入有效的 MMSI (5-9 位數字)');
            return;
        }
        loadVesselRoute(mmsi);
    }

    /**
     * Display dark vessels on the map
     */
    function displayDarkVessels(darkData) {
        layers.darkVessels.clearLayers();
        let totalPlotted = 0;

        Object.entries(darkData.regions).forEach(([regionKey, region]) => {
            if (!region.dark_details) return;
            const color = REGION_COLORS[regionKey] || '#ff3366';
            const name = REGION_NAMES[regionKey] || regionKey;

            region.dark_details.forEach(d => {
                if (!d.lat || !d.lon) return;
                const count = d.detections || 1;
                const radius = Math.min(3 + Math.log2(count) * 2, 8);

                const t2 = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
                L.circleMarker([d.lat, d.lon], {
                    radius: radius,
                    fillColor: color,
                    color: color,
                    weight: 1,
                    opacity: 0.6,
                    fillOpacity: 0.35
                }).addTo(layers.darkVessels).bindPopup(
                    `<b style="color:${color}">${t2('map.sar_dark')}</b><br>` +
                    `${t2('dv.popup_region')} ${name}<br>` +
                    `${t2('dv.popup_date')} ${d.date}<br>` +
                    `${t2('dv.popup_det')} ${count}`
                );
                totalPlotted++;
            });
        });

        return totalPlotted;
    }

    /**
     * Display suspicious vessels from CSIS analysis
     */
    function displaySuspiciousVessels(suspiciousData) {
        if (!suspiciousData.suspicious_vessels) return;

        // Clear previous suspicious markers (separate layer so they survive zoom/pan)
        layers.suspiciousVessels.clearLayers();

        suspiciousData.suspicious_vessels.forEach(sv => {
            if (sv.last_lat && sv.last_lon) {
                L.circleMarker([sv.last_lat, sv.last_lon], {
                    radius: 8,
                    fillColor: riskColors[sv.risk_level] || '#ff3366',
                    color: '#ffffff',
                    weight: 2,
                    opacity: 0.9,
                    fillOpacity: 0.9
                }).addTo(layers.suspiciousVessels).bindPopup(() => {
                    const t3 = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
                    var sanctionHit = getSanctionMatch((sv.names && sv.names[0]) || '');
                    var sanctionLine = sanctionHit
                        ? '<br><span class="sanction-warning">' + t3('app.sanctioned') + ' (' + t3('app.sanction_res') + ' ' + (sanctionHit.resolution || '1718') + ')</span>'
                        : '';
                    var netMarkerNote3 = (sv.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
                    var flagName3 = getMidFlag(sv.mmsi);
                    var flagLine3 = flagName3 ? '<br>' + t3('app.flag') + ' ' + flagName3 : '';
                    return '<b style="color:' + (riskColors[sv.risk_level] || '#ff3366') + '">' + ((sv.names && sv.names[0]) || sv.mmsi) + '</b><br>' +
                        t3('app.mmsi') + ' ' + sv.mmsi + flagLine3 + '<br>' +
                        '<b>' + t3('app.risk') + ' ' + sv.risk_level.toUpperCase() + '</b> (' + t3('app.score') + ' ' + sv.risk_score + ')<br>' +
                        (sv.flags || []).map(function(f) { return '- ' + f; }).join('<br>') +
                        sanctionLine + netMarkerNote3 +
                        '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + sv.mmsi + '\'); return false;">' + t3('app.show_track') + '</button>' +
                        '<br><button class="route-lookup-btn vic-detail-btn" onclick="MapModule.showVesselInfoCard(\'' + sv.mmsi + '\'); return false;">ℹ️ ' + t3('vic.detail') + '</button>';
                });
            }
        });
    }

    /**
     * Toggle layer visibility
     */
    function toggleLayer(layerName, visible) {
        if (visible) {
            map.addLayer(layers[layerName]);
        } else {
            map.removeLayer(layers[layerName]);
        }
        // Suspicious vessels follow the vessels layer toggle
        if (layerName === 'vessels' && layers.suspiciousVessels) {
            if (visible) {
                map.addLayer(layers.suspiciousVessels);
            } else {
                map.removeLayer(layers.suspiciousVessels);
            }
        }
    }

    /**
     * Focus on a specific vessel
     */
    function focusVessel(mmsi, vessels) {
        const v = vessels.get(mmsi);
        if (v && vesselMarkers[mmsi]) {
            map.flyTo([v.lat, v.lon], 10);
            vesselMarkers[mmsi].openPopup();
        }
    }

    /**
     * Focus on coordinates
     */
    function focusPosition(lat, lon, zoom = 10) {
        if (lat && lon) {
            map.flyTo([lat, lon], zoom);
        }
    }

    /**
     * Load and display submarine cable layer
     */
    // Cable fault status cache
    let cableFaults = null; // { faultedSlugs: Set, faultsBySlug: Map }

    async function loadCableFaultStatus() {
        if (cableFaults) return cableFaults;
        try {
            const res = await fetch('cable_status.json?' + Date.now());
            if (!res.ok) return null;
            const data = await res.json();
            const faultedSlugs = new Set();
            const faultsBySlug = {};
            (data.faults || []).forEach(f => {
                if (f.status === 'fault') {
                    faultedSlugs.add(f.slug);
                    if (!faultsBySlug[f.slug]) faultsBySlug[f.slug] = [];
                    faultsBySlug[f.slug].push(f);
                }
            });
            cableFaults = { faultedSlugs, faultsBySlug, raw: data };
            return cableFaults;
        } catch (e) {
            console.error('Cable status load failed:', e);
            return null;
        }
    }

    async function loadSubmarineCables() {
        if (layers.submarineCables.getLayers().length > 0) return; // already loaded
        try {
            const [cableRes, faultStatus] = await Promise.all([
                fetch('taiwan_cables.json?' + Date.now()),
                loadCableFaultStatus()
            ]);
            if (!cableRes.ok) return;
            const geoData = await cableRes.json();
            const faulted = faultStatus ? faultStatus.faultedSlugs : new Set();
            const faultDetails = faultStatus ? faultStatus.faultsBySlug : {};

            L.geoJSON(geoData, {
                style: f => {
                    const slug = f.properties.slug || '';
                    const isFaulted = faulted.has(slug);
                    return {
                        color: isFaulted ? '#ff0000' : '#' + (f.properties.color || 'ffd700'),
                        weight: isFaulted ? 3 : 2,
                        opacity: isFaulted ? 0.9 : 0.7
                    };
                },
                onEachFeature: (f, layer) => {
                    const slug = f.properties.slug || '';
                    const name = slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    const faults = faultDetails[slug];
                    let tip = name;
                    if (faults && faults.length > 0) {
                        const details = faults.map(ft =>
                            '\u26a0 ' + ft.segment + ': ' + (ft.description_zh || ft.description_en)
                        ).join('<br>');
                        tip = '<b style="color:#ff0000">' + name + '</b><br>' + details;
                    }
                    layer.bindTooltip(tip, { sticky: true });
                }
            }).addTo(layers.submarineCables);
        } catch (e) {
            console.error('Cable data load failed:', e);
        }
    }

    function getCableFaultStatus() {
        return cableFaults;
    }

    // Public API
    function setFilterFoc(enabled) {
        filterFocEnabled = enabled;
    }

    /**
     * Locate and zoom to vessels of a specific type
     * @param {string} type - 'fishing', 'cargo', 'tanker', 'lng', 'suspicious', 'other'
     */
    /**
     * Show a floating vessel list panel near the legend.
     * Clicking an item zooms to that vessel and opens its popup.
     */
    function showVesselListPanel(vessels, color) {
        // Remove any existing panel
        dismissVesselListPanel();

        const panel = document.createElement('div');
        panel.className = 'vessel-list-panel';
        panel.innerHTML = '<div class="vlp-title">⛽ LNG/Gas (' + vessels.length + ')</div>';

        vessels.slice(0, 5).forEach((v, i) => {
            const row = document.createElement('div');
            row.className = 'vlp-item';
            row.innerHTML = '<span class="vlp-num">' + (i + 1) + '</span>' +
                '<span class="vlp-name">' + (v.name || 'Unknown') + '</span>' +
                '<span class="vlp-speed">' + (v.speed || 0).toFixed(1) + ' kn</span>';
            row.addEventListener('click', function () {
                map.setView([v.lat, v.lon], 13);
                if (vesselMarkers[v.mmsi]) {
                    vesselMarkers[v.mmsi].openPopup();
                }
                // Flash this vessel
                var flash = L.circleMarker([v.lat, v.lon], {
                    radius: 22, fillColor: color, color: color,
                    weight: 2, opacity: 0.9, fillOpacity: 0.35
                }).addTo(layers.vessels);
                setTimeout(function () { layers.vessels.removeLayer(flash); }, 2500);
            });
            panel.appendChild(row);
        });

        document.querySelector('.map-legend').appendChild(panel);

        // Close panel when clicking elsewhere on the map
        setTimeout(function () {
            map.once('click', dismissVesselListPanel);
        }, 100);
    }

    function dismissVesselListPanel() {
        var old = document.querySelector('.vessel-list-panel');
        if (old) old.remove();
    }

    function locateVesselType(type) {
        if (!map || cachedVesselList.length === 0) return;

        const matched = cachedVesselList.filter(v => {
            if (type === 'lng') {
                return v.is_lng || /\b(LNG|LPG|FSRU|GAS)\b/i.test(v.name || '');
            }
            if (type === 'suspicious') {
                return v.suspicious;
            }
            if (type === 'other') {
                const isLng = v.is_lng || /\b(LNG|LPG|FSRU|GAS)\b/i.test(v.name || '');
                return !isLng && !v.suspicious && !['fishing', 'cargo', 'tanker'].includes(v.type_name);
            }
            return v.type_name === type;
        });

        if (matched.length === 0) return;

        // LNG: show a clickable list panel instead of just zooming
        if (type === 'lng') {
            showVesselListPanel(matched, VESSEL_COLORS.lng);
            return;
        }

        // If only one vessel, zoom to it directly
        if (matched.length === 1) {
            map.setView([matched[0].lat, matched[0].lon], 12);
            // Open popup if marker exists
            if (vesselMarkers[matched[0].mmsi]) {
                vesselMarkers[matched[0].mmsi].openPopup();
            }
            return;
        }

        // Fit bounds to show all matching vessels
        const bounds = L.latLngBounds(matched.map(v => [v.lat, v.lon]));
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });

        // Flash matching markers briefly
        const color = type === 'suspicious' ? '#ff3366'
            : (VESSEL_COLORS[type] || VESSEL_COLORS.other);

        const flashMarkers = matched.map(v => {
            return L.circleMarker([v.lat, v.lon], {
                radius: 18,
                fillColor: color,
                color: color,
                weight: 2,
                opacity: 0.8,
                fillOpacity: 0.3
            }).addTo(layers.vessels);
        });

        setTimeout(() => {
            flashMarkers.forEach(m => layers.vessels.removeLayer(m));
        }, 2000);
    }

    /**
     * Set suspicious data reference for info card lookups
     */
    function setSuspiciousData(data) {
        _suspiciousData = data;
    }

    /**
     * Show vessel deep-dive info card overlay
     * @param {string} mmsi - vessel MMSI to look up in suspicious data
     */
    function showVesselInfoCard(mmsi) {
        if (!_suspiciousData || !_suspiciousData.suspicious_vessels) {
            _buildFallbackCard(mmsi);
            return;
        }
        const sv = _suspiciousData.suspicious_vessels.find(v => v.mmsi === mmsi);
        if (sv) {
            _buildInfoCard(sv);
            return;
        }
        // Also check all_classifications
        const ac = (_suspiciousData.all_classifications || []).find(v => v.mmsi === mmsi);
        if (ac) {
            _buildInfoCard(ac);
            return;
        }
        _buildFallbackCard(mmsi);
    }

    function _buildFallbackCard(mmsi) {
        const existing = document.getElementById('vesselInfoOverlay');
        if (existing) existing.remove();

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const flagName = getMidFlag(mmsi);
        const overlay = document.createElement('div');
        overlay.id = 'vesselInfoOverlay';
        overlay.className = 'vessel-info-overlay';
        overlay.innerHTML = `
            <div class="vessel-info-card">
                <button class="vic-close" onclick="document.getElementById('vesselInfoOverlay').remove()" title="${t('vic.close')}">✕</button>
                <div class="vic-header">
                    <div class="vic-header-left">
                        <div class="vic-vessel-name">${mmsi}</div>
                        <div class="vic-vessel-meta">MMSI: ${mmsi}${flagName ? ' | ' + t('app.flag') + ' ' + flagName : ''}</div>
                    </div>
                </div>
                <div class="vic-section">
                    <div class="vic-empty">${t('vic.no_data')}</div>
                </div>
                <div class="vic-section">
                    <div class="vic-links-row">
                        <a class="vic-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=${mmsi}" target="_blank" rel="noopener">MarineTraffic</a>
                        <a class="vic-link" href="https://www.vesselfinder.com/vessels/details/${mmsi}" target="_blank" rel="noopener">VesselFinder</a>
                        <button class="route-lookup-btn" onclick="MapModule.loadVesselRoute('${mmsi}'); document.getElementById('vesselInfoOverlay').remove(); return false;">${t('vic.show_track')}</button>
                    </div>
                </div>
            </div>`;
        overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
        document.body.appendChild(overlay);
        document.addEventListener('keydown', function handler(e) {
            if (e.key === 'Escape') { var el = document.getElementById('vesselInfoOverlay'); if (el) el.remove(); document.removeEventListener('keydown', handler); }
        });
    }

    function _buildInfoCard(sv) {
        // Remove any existing card
        const existing = document.getElementById('vesselInfoOverlay');
        if (existing) existing.remove();

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const flagName = getMidFlag(sv.mmsi);
        const vesselName = (sv.names && sv.names[0]) || sv.mmsi;
        const riskColor = riskColors[sv.risk_level] || '#ff3366';

        // ── Header ──
        const headerHtml = `
            <div class="vic-header">
                <div class="vic-header-left">
                    <div class="vic-vessel-name" style="color:${riskColor}">${vesselName}</div>
                    <div class="vic-vessel-meta">
                        MMSI: ${sv.mmsi}
                        ${flagName ? ' | ' + t('app.flag') + ' ' + flagName : ''}
                        ${sv.vessel_type ? ' | ' + t('vic.vessel_type') + ': ' + sv.vessel_type : ''}
                    </div>
                </div>
                <div class="vic-header-right">
                    <span class="risk-badge risk-${sv.risk_level}" style="font-size:11px;padding:3px 8px">${(sv.risk_level || '').toUpperCase()}</span>
                </div>
            </div>`;

        // ── ITU MARS Registry ──
        let marsHtml = '';
        const marsDetails = sv.itu_mars_details || {};
        const marsRec = marsDetails.mars_record;
        const mismatches = marsDetails.mismatches || [];
        const mismatchFields = new Set(mismatches.map(m => m.field));

        if (marsRec && marsRec.found !== false) {
            const rows = [
                { label: t('vic.mars_name'), value: marsRec.ship_name || '-', field: 'ship_name' },
                { label: t('vic.mars_cs'), value: marsRec.call_sign || '-', field: 'call_sign' },
                { label: t('vic.mars_imo'), value: marsRec.imo_number || '-', field: 'imo_number' },
                { label: t('vic.mars_flag'), value: marsRec.administration || '-', field: 'administration' },
                { label: t('vic.mars_updated'), value: marsRec.update_date || '-', field: null },
            ];
            let rowsHtml = rows.map(r => {
                const isMismatch = r.field && mismatchFields.has(r.field);
                const mismatchInfo = isMismatch
                    ? mismatches.find(m => m.field === r.field)
                    : null;
                const mismatchNote = mismatchInfo
                    ? `<span class="vic-mismatch">${t('vic.mismatch')}: ${t('vic.ais_vs_mars')} ${Array.isArray(mismatchInfo.ais) ? mismatchInfo.ais.join(', ') : mismatchInfo.ais}</span>`
                    : '';
                return `<div class="vic-row${isMismatch ? ' vic-row-warn' : ''}">
                    <span class="vic-label">${r.label}</span>
                    <span class="vic-value">${r.value}${mismatchNote}</span>
                </div>`;
            }).join('');
            marsHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.registry')}</div>
                ${rowsHtml}
            </div>`;
        } else {
            marsHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.registry')}</div>
                <div class="vic-empty">${t('vic.no_mars')}</div>
            </div>`;
        }

        // ── Risk Score Breakdown ──
        const score = sv.risk_score || 0;
        const maxScore = 20;
        const pct = Math.min(100, Math.round((score / maxScore) * 100));
        const flagsList = (sv.flags || []).map(f => `<div class="vic-flag-item">• ${f}</div>`).join('');
        const scoreHtml = `<div class="vic-section">
            <div class="vic-section-title">${t('vic.risk_score')}: ${score} / ${(sv.risk_level || '').toUpperCase()}</div>
            <div class="vic-score-bar-wrap">
                <div class="vic-score-bar" style="width:${pct}%;background:${riskColor}"></div>
            </div>
            ${sv.type_multiplier != null ? `<div class="vic-row"><span class="vic-label">${t('vic.multiplier')}</span><span class="vic-value">×${sv.type_multiplier}</span></div>` : ''}
            <div class="vic-flags-list">${flagsList || '-'}</div>
        </div>`;

        // ── AIS Anomalies ──
        let anomaliesHtml = '';
        const anomalies = sv.ais_anomalies || [];
        if (anomalies.length > 0) {
            const items = anomalies.map(a => {
                const desc = a.description || a.type || '';
                const severity = a.severity ? ` <span class="vic-severity-${a.severity}">[${a.severity}]</span>` : '';
                return `<div class="vic-anomaly-item">${desc}${severity}</div>`;
            }).join('');
            anomaliesHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.anomalies')}</div>
                ${items}
            </div>`;
        } else {
            anomaliesHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.anomalies')}</div>
                <div class="vic-empty">${t('vic.no_anomalies')}</div>
            </div>`;
        }

        // ── Cable Activity ──
        let cableHtml = '';
        const cd = sv.cable_details;
        if (cd && (sv.cable_proximity || sv.cable_loitering)) {
            cableHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.cable')}</div>
                <div class="vic-row"><span class="vic-label">${t('vic.nearest_cable')}</span><span class="vic-value">${cd.nearest_cable || '-'}</span></div>
                <div class="vic-row"><span class="vic-label">${t('vic.min_dist')}</span><span class="vic-value">${cd.min_distance_km != null ? cd.min_distance_km.toFixed(1) + ' km' : '-'}</span></div>
                <div class="vic-row"><span class="vic-label">${t('vic.loiter_hrs')}</span><span class="vic-value">${cd.loiter_hours != null ? cd.loiter_hours.toFixed(1) + ' h' : '-'}</span></div>
            </div>`;
        }

        // ── External Links ──
        const mtUrl = 'https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' + sv.mmsi;
        const vfUrl = 'https://www.vesselfinder.com/vessels/details/' + sv.mmsi;
        const linksHtml = `<div class="vic-section">
            <div class="vic-section-title">${t('vic.links')}</div>
            <div class="vic-links-row">
                <a class="vic-link" href="${mtUrl}" target="_blank" rel="noopener">MarineTraffic</a>
                <a class="vic-link" href="${vfUrl}" target="_blank" rel="noopener">VesselFinder</a>
                <button class="route-lookup-btn" onclick="MapModule.loadVesselRoute('${sv.mmsi}'); document.getElementById('vesselInfoOverlay').remove(); return false;">${t('vic.show_track')}</button>
            </div>
        </div>`;

        // ── Assemble card ──
        const overlay = document.createElement('div');
        overlay.id = 'vesselInfoOverlay';
        overlay.className = 'vessel-info-overlay';
        overlay.innerHTML = `
            <div class="vessel-info-card">
                <button class="vic-close" onclick="document.getElementById('vesselInfoOverlay').remove()" title="${t('vic.close')}">✕</button>
                ${headerHtml}
                ${marsHtml}
                ${scoreHtml}
                ${anomaliesHtml}
                ${cableHtml}
                ${linksHtml}
            </div>`;

        // Click overlay background to close
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) overlay.remove();
        });

        document.body.appendChild(overlay);

        // ESC to close
        const escHandler = function(e) {
            if (e.key === 'Escape') {
                const el = document.getElementById('vesselInfoOverlay');
                if (el) el.remove();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    return {
        init,
        drawFishingHotspots,
        displayVessels,
        renderVesselsForZoom,
        displayDarkVessels,
        displaySuspiciousVessels,
        toggleLayer,
        focusVessel,
        focusPosition,
        loadSubmarineCables,
        loadCableFaultStatus,
        getCableFaultStatus,
        loadVesselRoute,
        clearVesselRoute,
        searchVesselRoute,
        snapshotMap,
        setFilterFoc,
        locateVesselType,
        drawTerritorialBaseline,
        setSuspiciousData,
        showVesselInfoCard,
        getMidFlag,
        FISHING_HOTSPOTS,
        VESSEL_COLORS,
        REGION_COLORS,
        REGION_NAMES
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapModule;
}

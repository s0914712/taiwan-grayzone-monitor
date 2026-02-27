/**
 * ============================================================================
 * i18n 多語系模組 - 中文 / English
 * Taiwan Gray Zone Monitor - Internationalization
 * ============================================================================
 *
 * 使用方式：
 *   HTML: <span data-i18n="nav.dark_vessels">暗船偵測</span>
 *   JS:   i18n.t('nav.dark_vessels')  →  "暗船偵測" 或 "Dark Vessel Detection"
 *
 * 自動偵測瀏覽器語言，使用者可手動切換，偏好儲存於 localStorage。
 * ============================================================================
 */

const i18n = (function () {

    // ========================================================================
    // 翻譯字典
    // ========================================================================
    const dict = {

        // ── 導航 Navigation ──
        'nav.grayzone':          { zh: '灰色地帶監測', en: 'Gray Zone Monitor' },
        'nav.dark_vessels':      { zh: '暗船偵測', en: 'Dark Vessels' },
        'nav.statistics':        { zh: '統計分析', en: 'Statistics' },
        'nav.animation':         { zh: '暗船動畫', en: 'Animation' },
        'nav.mob_monitor':       { zh: '監測', en: 'Monitor' },
        'nav.mob_dark':          { zh: '暗船', en: 'Dark' },
        'nav.mob_stats':         { zh: '統計', en: 'Stats' },
        'nav.mob_anim':          { zh: '動畫', en: 'Anim' },
        'nav.identity':          { zh: '身分追蹤', en: 'Identity' },
        'nav.mob_identity':      { zh: '身分', en: 'ID' },

        // ── 通用 Common ──
        'common.loading':        { zh: '載入中...', en: 'Loading...' },
        'common.no_data':        { zh: '無資料', en: 'No data' },
        'common.update_time':    { zh: '資料更新時間:', en: 'Data updated:' },
        'common.updated':        { zh: '更新:', en: 'Updated:' },
        'common.error_load':     { zh: '❌ 資料載入失敗', en: '❌ Data loading failed' },
        'common.unknown':        { zh: '未知', en: 'Unknown' },

        // ── 頁面標題 Page Titles ──
        'title.index':           { zh: '🛰️ 台灣灰色地帶監測', en: '🛰️ Taiwan Gray Zone Monitor' },
        'title.dark':            { zh: '🔦 暗船偵測 Dark Vessel Detection', en: '🔦 Dark Vessel Detection' },
        'title.stats':           { zh: '📊 統計分析 Statistics', en: '📊 Statistical Analysis' },
        'title.anim':            { zh: '🎬 暗船動畫 Dark Vessel Animation', en: '🎬 Dark Vessel Animation' },
        'title.identity':        { zh: '🔄 AIS 身分變更追蹤', en: '🔄 AIS Identity Tracking' },

        // ── 區域名稱 Region Names ──
        'region.taiwan_strait':  { zh: '台灣海峽', en: 'Taiwan Strait' },
        'region.east_taiwan':    { zh: '台灣東部海域', en: 'East Taiwan Waters' },
        'region.east_taiwan_s':  { zh: '台灣東部', en: 'East Taiwan' },
        'region.south_china_sea':{ zh: '南海北部', en: 'Northern South China Sea' },
        'region.east_china_sea': { zh: '東海', en: 'East China Sea' },

        // ── 船隻類型 Vessel Types ──
        'vessel.fishing':        { zh: '漁船', en: 'Fishing' },
        'vessel.cargo':          { zh: '貨船', en: 'Cargo' },
        'vessel.tanker':         { zh: '油輪', en: 'Tanker' },
        'vessel.other':          { zh: '其他', en: 'Other' },
        'vessel.suspicious':     { zh: '可疑', en: 'Suspicious' },

        // ── 暗船通用標籤 Dark Vessel Labels ──
        'dark.count':            { zh: '暗船數量', en: 'Dark Vessels' },
        'dark.total_detect':     { zh: 'SAR 總偵測數', en: 'Total SAR Detections' },
        'dark.total_detect_s':   { zh: 'SAR 總偵測', en: 'SAR Detections' },
        'dark.ais_match':        { zh: '有 AIS 匹配', en: 'AIS Matched' },
        'dark.ratio':            { zh: '暗船比例', en: 'Dark Ratio' },
        'dark.dark_vessel':      { zh: '暗船', en: 'Dark Vessels' },
        'dark.dark_total':       { zh: '暗船總數', en: 'Total Dark Vessels' },
        'dark.avg_daily':        { zh: '日均暗船', en: 'Avg Daily Dark' },

        // ── Index 頁面 ──
        'idx.vessel_count':      { zh: '船隻數', en: 'Vessels' },
        'idx.drill_zones':       { zh: '軍演區', en: 'Drill Zones' },
        'idx.suspicious':        { zh: '可疑船隻', en: 'Suspicious' },
        'idx.layer_drills':      { zh: '軍演區域', en: 'Drill Zones' },
        'idx.layer_fishing':     { zh: '漁撈熱點', en: 'Fishing Hotspots' },
        'idx.layer_vessels':     { zh: '船隻', en: 'Vessels' },
        'idx.legend_type':       { zh: '船隻類型', en: 'Vessel Types' },
        'idx.legend_sat':        { zh: '衛星偵測', en: 'Satellite Detection' },
        'idx.legend_sar':        { zh: 'SAR 暗船', en: 'SAR Dark Vessels' },
        'idx.legend_region':     { zh: '區域', en: 'Regions' },
        'idx.legend_hotspot':    { zh: '漁撈熱點', en: 'Fishing Hotspots' },

        // ── Index 側邊欄 ──
        'idx.ais_title':         { zh: '📊 AIS 即時統計', en: '📊 AIS Real-time Stats' },
        'idx.total_vessels':     { zh: '總船隻', en: 'Total Vessels' },
        'idx.gfw_title':         { zh: '🛰️ GFW 衛星監測 (30天)', en: '🛰️ GFW Satellite (30 days)' },
        'idx.avg_dark':          { zh: '日均暗船數', en: 'Avg Daily Dark' },
        'idx.trend_7d':          { zh: '7日趨勢', en: '7-day Trend' },
        'idx.chn_hours':         { zh: '中國船時(萬)', en: 'CHN Hours (10k)' },
        'idx.drill_records':     { zh: '軍演區記錄', en: 'Drill Records' },
        'idx.fishing_hours':     { zh: '漁撈時數(萬)', en: 'Fishing Hrs (10k)' },
        'idx.data_days':         { zh: '資料天數', en: 'Data Days' },
        'idx.sparkline':         { zh: '每日 SAR 偵測量', en: 'Daily SAR Detections' },
        'idx.suspicious_title':  { zh: '🔍 可疑船隻 (CSIS 方法論)', en: '🔍 Suspicious Vessels (CSIS)' },
        'idx.suspicious_wait':   { zh: '累積觀測資料中...', en: 'Accumulating data...' },
        'idx.identity_title':    { zh: '🔄 AIS 身分變更', en: '🔄 AIS Identity Changes' },
        'idx.identity_24h':      { zh: '24h 事件', en: '24h Events' },
        'idx.identity_7d':       { zh: '7d 事件', en: '7d Events' },
        'idx.identity_vessels':  { zh: '涉及船隻', en: 'Vessels' },
        'idx.identity_no_events':{ zh: '近期無身分變更事件', en: 'No recent identity changes' },
        'idx.identity_name':     { zh: '船名變更', en: 'Name Change' },
        'idx.identity_callsign': { zh: '呼號變更', en: 'Call Sign Change' },
        'idx.identity_imo':      { zh: 'IMO 變更', en: 'IMO Change' },
        'idx.identity_multi':    { zh: '多欄位', en: 'Multi-field' },
        'idx.identity_in_drill': { zh: '軍演區', en: 'Drill Zone' },
        'idx.identity_ago_h':    { zh: '{0}小時前', en: '{0}h ago' },
        'idx.identity_ago_d':    { zh: '{0}天前', en: '{0}d ago' },
        'idx.identity_just_now': { zh: '剛才', en: 'Just now' },

        // ── Identity History 頁面 ──
        'id.total_events':       { zh: '總事件數', en: 'Total Events' },
        'id.unique_vessels':     { zh: '涉及船隻', en: 'Unique Vessels' },
        'id.events_7d':          { zh: '7 天事件', en: '7-day Events' },
        'id.events_24h':         { zh: '24h 事件', en: '24h Events' },
        'id.map_title':          { zh: '📍 身分變更發生位置', en: '📍 Identity Change Locations' },
        'id.freq_title':         { zh: '🏴 頻繁變更船隻排行', en: '🏴 Frequent Identity Changers' },
        'id.timeline_title':     { zh: '📋 變更事件時間軸', en: '📋 Event Timeline' },
        'id.th_mmsi':            { zh: 'MMSI', en: 'MMSI' },
        'id.th_name':            { zh: '目前船名', en: 'Current Name' },
        'id.th_changes':         { zh: '變更次數', en: 'Changes' },
        'id.th_last_change':     { zh: '最近變更', en: 'Last Change' },
        'id.th_drill_events':    { zh: '軍演區事件', en: 'Drill Zone' },
        'id.th_time':            { zh: '時間', en: 'Time' },
        'id.th_field':           { zh: '欄位', en: 'Field' },
        'id.th_old':             { zh: '變更前', en: 'Before' },
        'id.th_new':             { zh: '變更後', en: 'After' },
        'id.th_location':        { zh: '位置', en: 'Location' },
        'id.no_events':          { zh: '尚無身分變更事件紀錄。資料會在 AIS 排程執行後自動累積。', en: 'No identity change events yet. Data will accumulate after AIS scheduled runs.' },

        'idx.drill_title':       { zh: '⚠️ 軍演監測區', en: '⚠️ Drill Monitoring Zones' },
        'idx.recent_title':      { zh: '🚢 近期船隻', en: '🚢 Recent Vessels' },

        // ── Index 關於 ──
        'idx.about_purpose':     { zh: '📡 專案目的 Project Purpose', en: '📡 Project Purpose' },
        'idx.about_objective':   { zh: '監測目標', en: 'Monitoring Objective' },
        'idx.about_obj_text':    { zh: '運用 SAR 合成孔徑雷達衛星偵測與 AIS 即時船舶資料，自動化監測台灣周邊海域的灰色地帶活動。專注偵測「暗船」（關閉 AIS 的船隻）在台灣海峽、東部海域及南海北部的分布與趨勢。', en: 'Automated monitoring of gray zone activities around Taiwan using SAR satellite detection and real-time AIS vessel data. Focused on detecting "dark vessels" (vessels with AIS turned off) across the Taiwan Strait, Eastern Waters, and Northern South China Sea.' },
        'idx.about_why':         { zh: '為什麼關注暗船？', en: 'Why Focus on Dark Vessels?' },
        'idx.about_why_text':    { zh: '根據 CSIS「蟲群信號」研究，中國在台灣周邊的灰色地帶行動中，大量使用關閉 AIS 的船隻。這些暗船可能包括海上民兵、偵察船或軍事支援船隻，其活動模式常與軍事演習高度相關。', en: 'According to CSIS "Signals in the Swarm" research, China extensively uses vessels with AIS turned off in gray zone operations around Taiwan. These dark vessels may include maritime militia, reconnaissance ships, or military support vessels, whose activity patterns often correlate highly with military exercises.' },
        'idx.about_method':      { zh: '🔬 研究方法 Methodology', en: '🔬 Research Methodology' },
        'idx.about_sar':         { zh: '合成孔徑雷達 (SAR) 衛星偵測', en: 'Synthetic Aperture Radar (SAR) Detection' },
        'idx.about_sar_text':    { zh: '使用 Global Fishing Watch 提供的 SAR 衛星資料，可在任何天氣條件下偵測海面船隻。將偵測結果與 AIS 資料比對，無法匹配的即為「暗船」。', en: 'Using SAR satellite data from Global Fishing Watch, capable of detecting vessels in any weather. Detection results are cross-referenced with AIS data — unmatched targets are "dark vessels".' },
        'idx.about_csis':        { zh: 'CSIS「蟲群信號」方法論', en: 'CSIS "Signals in the Swarm" Methodology' },
        'idx.about_csis_1':      { zh: '行為閾值分析：軍演區停留 >30% + 漁場停留 <10%', en: 'Behavioral threshold: Drill zone dwell >30% + Fishing area dwell <10%' },
        'idx.about_csis_2':      { zh: 'AIS 異常偵測：船名變更、AIS 中斷、船型變更', en: 'AIS anomaly: Name changes, AIS interruptions, vessel type changes' },
        'idx.about_csis_3':      { zh: '時間序列相關性：暗船活動與軍事架次的滯後關係', en: 'Time-series correlation: Dark vessel activity vs military sortie lag' },
        'idx.about_multi':       { zh: '多源數據整合', en: 'Multi-Source Data Integration' },
        'idx.about_multi_1':     { zh: 'Global Fishing Watch API：SAR 暗船偵測、漁撈努力量', en: 'Global Fishing Watch API: SAR dark vessels, fishing effort' },
        'idx.about_multi_2':     { zh: 'AISStream.io：即時 AIS 船舶追蹤', en: 'AISStream.io: Real-time AIS vessel tracking' },
        'idx.about_multi_3':     { zh: '解放軍軍事出動記錄：MND 每日國防消息', en: 'PLA military sortie records: MND daily defense reports' },
        'idx.about_refs':        { zh: '參考文獻', en: 'References' },

        // ── Dark Vessels 頁面 ──
        'dv.map_title':          { zh: '📍 暗船位置分布（SAR 衛星偵測、無 AIS 匹配）', en: '📍 Dark Vessel Locations (SAR Detection, No AIS Match)' },
        'dv.daily_title':        { zh: '📊 每日暗船數量趨勢', en: '📊 Daily Dark Vessel Trend' },
        'dv.region_title':       { zh: '🗺️ 各區域暗船比較', en: '🗺️ Regional Dark Vessel Comparison' },
        'dv.flag_title':         { zh: '🚩 有 AIS 船隻國旗分布（台灣海峽）', en: '🚩 AIS Vessel Flag Distribution (Taiwan Strait)' },
        'dv.no_dark_data':       { zh: '⚠️ 尚無暗船資料', en: '⚠️ No dark vessel data yet' },
        'dv.popup_title':        { zh: '暗船偵測', en: 'Dark Vessel Detection' },
        'dv.popup_region':       { zh: '區域:', en: 'Region:' },
        'dv.popup_date':         { zh: '日期:', en: 'Date:' },
        'dv.popup_pos':          { zh: '位置:', en: 'Position:' },
        'dv.popup_det':          { zh: '偵測數:', en: 'Detections:' },

        // ── 表格標頭 Table Headers ──
        'th.region':             { zh: '區域', en: 'Region' },
        'th.total':              { zh: '總偵測', en: 'Total' },
        'th.dark':               { zh: '暗船', en: 'Dark' },
        'th.ratio':              { zh: '比例', en: 'Ratio' },
        'th.date':               { zh: '日期', en: 'Date' },
        'th.sar_detect':         { zh: 'SAR 偵測', en: 'SAR' },
        'th.dark_ratio':         { zh: '暗船比例', en: 'Dark %' },
        'th.coords':             { zh: '座標點', en: 'Points' },

        // ── Statistics 頁面 ──
        'st.days_7':             { zh: '7 天', en: '7 Days' },
        'st.days_30':            { zh: '30 天', en: '30 Days' },
        'st.days_90':            { zh: '90 天', en: '90 Days' },
        'st.predict_title':      { zh: '🎯 軍演預測指標', en: '🎯 Exercise Prediction Indicator' },
        'st.predict_7d':         { zh: '7日預測風險等級', en: '7-day Forecast Risk Level' },
        'st.predict_desc_high':  { zh: '暗船活動顯著高於平均，需密切關注', en: 'Dark vessel activity significantly above average, requires close attention' },
        'st.predict_desc_mid':   { zh: '暗船活動略高於平均，持續觀察中', en: 'Dark vessel activity slightly above average, monitoring' },
        'st.predict_desc_low':   { zh: '暗船活動處於正常範圍', en: 'Dark vessel activity within normal range' },
        'st.predict_high':       { zh: '高', en: 'HIGH' },
        'st.predict_mid':        { zh: '中', en: 'MED' },
        'st.predict_low':        { zh: '低', en: 'LOW' },
        'st.corr_lag1':          { zh: '暗船→架次 (1日滯後)', en: 'Dark→Sortie (1-day lag)' },
        'st.corr_lag3':          { zh: '暗船→架次 (3日滯後)', en: 'Dark→Sortie (3-day lag)' },
        'st.corr_lag7':          { zh: '暗船→架次 (7日滯後)', en: 'Dark→Sortie (7-day lag)' },
        'st.trend_title':        { zh: '📈 暗船數量趨勢', en: '📈 Dark Vessel Trend' },
        'st.region_title':       { zh: '🗺️ 區域暗船分布', en: '🗺️ Regional Distribution' },
        'st.flag_title':         { zh: '🚩 有 AIS 船隻國旗分布', en: '🚩 AIS Vessel Flag Distribution' },
        'st.daily_title':        { zh: '📋 每日數據', en: '📋 Daily Data' },
        'st.data_loaded':        { zh: '✅ 資料已載入', en: '✅ Data loaded' },

        // ── Animation 頁面 ──
        'anim.range':            { zh: '範圍', en: 'Range' },
        'anim.days_30':          { zh: '30 天', en: '30 Days' },
        'anim.days_60':          { zh: '60 天', en: '60 Days' },
        'anim.days_90':          { zh: '90 天', en: '90 Days' },
        'anim.prev':             { zh: '上一天', en: 'Previous Day' },
        'anim.play':             { zh: '播放/暫停', en: 'Play/Pause' },
        'anim.next':             { zh: '下一天', en: 'Next Day' },
        'anim.speed':            { zh: '速度', en: 'Speed' },
        'anim.change':           { zh: '較上次變化', en: 'Change' },
        'anim.map_title':        { zh: '📍 暗船位置動畫（SAR 衛星偵測 / 每偵測日一幀）', en: '📍 Dark Vessel Animation (SAR / One frame per detection day)' },
        'anim.chart_title':      { zh: '📊 各偵測日暗船數量', en: '📊 Dark Vessels by Detection Day' },
        'anim.region_title':     { zh: '🗺️ 本日各區域分布', en: '🗺️ Regional Distribution (Current Day)' },
        'anim.no_data':          { zh: '⚠️ 尚無動畫資料', en: '⚠️ No animation data yet' },
        'anim.no_data_msg':      { zh: '尚無資料，請等待下次資料更新', en: 'No data, please wait for next update' },
        'anim.load_fail':        { zh: '❌ 載入失敗', en: '❌ Loading failed' },
        'anim.load_fail_msg':    { zh: '資料載入失敗，請稍後再試', en: 'Loading failed, please try again later' },
        'anim.no_detect':        { zh: '本日無偵測資料', en: 'No detections for this day' },
        'anim.frame':            { zh: '第 {0} / {1} 天', en: 'Day {0} of {1}' },

        // ── Map 模組 ──
        'map.zone_north':        { zh: '北部海域', en: 'Northern Waters' },
        'map.zone_east':         { zh: '東部海域', en: 'Eastern Waters' },
        'map.zone_south':        { zh: '南部海域', en: 'Southern Waters' },
        'map.zone_west':         { zh: '西部海域', en: 'Western Waters' },
        'map.hot_taiwan_bank':   { zh: '台灣灘漁場', en: 'Taiwan Bank' },
        'map.hot_penghu':        { zh: '澎湖漁場', en: 'Penghu Fishing Ground' },
        'map.hot_kuroshio':      { zh: '東部黑潮漁場', en: 'Kuroshio E. Fishing Ground' },
        'map.hot_northeast':     { zh: '東北漁場', en: 'NE Fishing Ground' },
        'map.hot_southwest':     { zh: '西南沿岸漁場', en: 'SW Coastal Fishing Ground' },
        'map.sar_dark':          { zh: 'SAR 暗船偵測', en: 'SAR Dark Vessel' },

        // ── App 模組 ──
        'app.ais_sat_loaded':    { zh: '✅ AIS + 衛星資料已載入', en: '✅ AIS + Satellite data loaded' },
        'app.sat_loaded':        { zh: '🛰️ 衛星資料已載入', en: '🛰️ Satellite data loaded' },
        'app.no_data':           { zh: '⚠️ 尚無資料', en: '⚠️ No data yet' },
        'app.load_fail_msg':     { zh: '請確認 data.json 是否存在', en: 'Please check if data.json exists' },
        'app.analyzed':          { zh: '已分析 {0} 艘，暫無達到門檻的可疑船隻', en: 'Analyzed {0} vessels, no suspicious vessels above threshold' },
        'app.csis_suspicious':   { zh: 'CSIS 可疑：漁船在軍演區但不在漁場', en: 'CSIS Suspicious: Fishing vessel in drill zone, not in fishing area' },
        'app.mmsi':              { zh: 'MMSI:', en: 'MMSI:' },
        'app.type':              { zh: '類型:', en: 'Type:' },
        'app.speed':             { zh: '航速:', en: 'Speed:' },
        'app.risk':              { zh: '風險:', en: 'Risk:' },
        'app.score':             { zh: '分數:', en: 'Score:' },

        // ── Charts 模組 ──
        'chart.detect':          { zh: '偵測', en: 'det' },
        'chart.dark':            { zh: '暗船', en: 'dark' },
        'chart.unit_wan':        { zh: '萬', en: '0k' },
    };

    // ========================================================================
    // 狀態
    // ========================================================================
    let currentLang = 'zh';

    // ========================================================================
    // 核心函數
    // ========================================================================

    /**
     * 翻譯函數
     * @param {string} key - 翻譯 key
     * @param {...string} args - 替換 {0}, {1} 等佔位符
     * @returns {string}
     */
    function t(key, ...args) {
        const entry = dict[key];
        if (!entry) return key;
        let text = entry[currentLang] || entry['zh'] || key;
        // 替換佔位符 {0}, {1}, ...
        args.forEach((arg, i) => {
            text = text.replace(new RegExp('\\{' + i + '\\}', 'g'), arg);
        });
        return text;
    }

    /**
     * 偵測瀏覽器語言
     */
    function detectLanguage() {
        const saved = localStorage.getItem('lang');
        if (saved && (saved === 'zh' || saved === 'en')) return saved;
        const nav = navigator.language || navigator.userLanguage || '';
        return nav.startsWith('zh') ? 'zh' : 'en';
    }

    /**
     * 套用翻譯到所有 [data-i18n] 元素
     */
    function applyAll() {
        // 設定 body class 供 CSS 控制語言可見性
        document.body.classList.remove('lang-zh', 'lang-en');
        document.body.classList.add('lang-' + currentLang);

        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const text = t(key);
            if (el.hasAttribute('data-i18n-placeholder')) {
                el.placeholder = text;
            } else {
                el.textContent = text;
            }
        });
        // data-i18n-html: 設定 innerHTML（用於含 HTML 標籤的翻譯）
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            const key = el.getAttribute('data-i18n-html');
            el.innerHTML = t(key);
        });
        // data-i18n-title: 設定 title 屬性
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            el.title = t(el.getAttribute('data-i18n-title'));
        });
        // 更新 toggle 按鈕狀態
        const toggle = document.getElementById('langToggle');
        if (toggle) {
            toggle.textContent = currentLang === 'zh' ? 'EN' : '中';
            toggle.title = currentLang === 'zh' ? 'Switch to English' : '切換為中文';
        }
    }

    /**
     * 切換語言
     */
    function toggle() {
        currentLang = currentLang === 'zh' ? 'en' : 'zh';
        localStorage.setItem('lang', currentLang);
        applyAll();
        // 觸發自訂事件讓各頁面 JS 可以更新動態文字
        window.dispatchEvent(new CustomEvent('langchange', { detail: { lang: currentLang } }));
    }

    /**
     * 設定語言
     */
    function setLang(lang) {
        currentLang = lang;
        localStorage.setItem('lang', currentLang);
        applyAll();
        window.dispatchEvent(new CustomEvent('langchange', { detail: { lang: currentLang } }));
    }

    /**
     * 取得目前語言
     */
    function getLang() {
        return currentLang;
    }

    // ========================================================================
    // 初始化
    // ========================================================================
    currentLang = detectLanguage();

    // DOM ready 時自動套用
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyAll);
    } else {
        // 延遲一個 tick 以確保 HTML 已渲染
        setTimeout(applyAll, 0);
    }

    // ========================================================================
    // 公開 API
    // ========================================================================
    return { t, toggle, setLang, getLang, applyAll };

})();

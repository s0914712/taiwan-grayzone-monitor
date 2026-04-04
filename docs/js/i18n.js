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
        'nav.animation':         { zh: '軌跡動畫', en: 'Trail Animation' },
        'nav.mob_monitor':       { zh: '監測', en: 'Monitor' },
        'nav.mob_dark':          { zh: '暗船', en: 'Dark' },
        'nav.mob_stats':         { zh: '統計', en: 'Stats' },
        'nav.mob_anim':          { zh: '動畫', en: 'Anim' },
        'nav.mob_tools':         { zh: '工具', en: 'Tools' },
        'nav.identity':          { zh: '身分追蹤', en: 'Identity' },
        'nav.mob_identity':      { zh: '身分', en: 'ID' },
        'nav.transfers':         { zh: '旁靠偵測', en: 'STS Transfer' },
        'nav.mob_transfers':     { zh: '旁靠', en: 'STS' },

        // ── Bottom Sheet ──
        'bs.route_search':       { zh: '航跡查詢', en: 'Route Search' },
        'bs.layers':             { zh: '圖層控制', en: 'Layer Controls' },
        'bs.realtime_stats':     { zh: '即時統計', en: 'Live Stats' },
        'bs.suspicious':         { zh: '可疑船隻', en: 'Suspicious Vessels' },

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
        'title.transfers':       { zh: '🚢 旁靠偵測 Ship-to-Ship Transfer', en: '🚢 Ship-to-Ship Transfer Detection' },

        // ── 旁靠偵測 Ship-to-Ship Transfer ──
        'sts.active':            { zh: '進行中旁靠', en: 'Active Transfers' },
        'sts.suspicious':        { zh: '可疑旁靠 (14日)', en: 'Suspicious (14d)' },
        'sts.pair_trawling':     { zh: '雙拖作業 (14日)', en: 'Pair Trawling (14d)' },
        'sts.unique_vessels':    { zh: '涉及船隻數', en: 'Unique Vessels' },
        'sts.map_title':         { zh: '📍 旁靠事件位置分布（距離 < 10m、持續 ≥ 1 小時、排除港內）', en: '📍 Transfer Locations (< 10m, ≥ 1h, excl. ports)' },
        'sts.chart_title':       { zh: '📊 每日旁靠事件統計', en: '📊 Daily Transfer Events' },
        'sts.table_title':       { zh: '📋 旁靠事件列表', en: '📋 Transfer Event List' },
        'sts.filter_all':        { zh: '全部', en: 'All' },
        'sts.filter_suspicious': { zh: '可疑', en: 'Suspicious' },
        'sts.filter_trawling':   { zh: '雙拖', en: 'Trawling' },
        'sts.th_class':          { zh: '分類', en: 'Class' },
        'sts.th_vessels':        { zh: '船舶', en: 'Vessels' },
        'sts.th_dist':           { zh: '距離', en: 'Dist.' },
        'sts.th_duration':       { zh: '時長', en: 'Duration' },
        'sts.th_risk':           { zh: '風險', en: 'Risk' },
        'sts.th_time':           { zh: '時間', en: 'Time' },
        'sts.events_found':      { zh: '筆旁靠事件', en: 'transfer events' },
        'sts.no_data':           { zh: '尚無旁靠資料', en: 'No transfer data available' },
        'sts.normal_label':      { zh: '一般旁靠', en: 'Normal' },

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
        'vessel.unknown':        { zh: '未知', en: 'Unknown' },
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
        'idx.top10pct':          { zh: 'Top 10% 高風險', en: 'Top 10% High Risk' },
        'idx.suspicious':        { zh: '可疑船隻', en: 'Suspicious' },
        'idx.layer_fishing':     { zh: '漁撈熱點', en: 'Fishing Hotspots' },
        'idx.layer_vessels':     { zh: '船隻', en: 'Vessels' },
        'idx.layer_baseline':    { zh: '領海基線/領海/鄰接區', en: 'Baseline / Territorial Sea / CZ' },
        'idx.filter_foc':        { zh: '過濾權宜船', en: 'Hide FOC Merchant' },
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
        'idx.fishing_hours':     { zh: '漁撈時數(萬)', en: 'Fishing Hrs (10k)' },
        'idx.data_days':         { zh: '資料天數', en: 'Data Days' },
        'idx.sparkline':         { zh: '每日 SAR 偵測量', en: 'Daily SAR Detections' },
        'idx.suspicious_title':  { zh: '🔍 可疑船隻（海纜威脅偵測）', en: '🔍 Suspicious Vessels (Cable Threat)' },
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
        'id.th_time':            { zh: '時間', en: 'Time' },
        'id.th_field':           { zh: '欄位', en: 'Field' },
        'id.th_old':             { zh: '變更前', en: 'Before' },
        'id.th_new':             { zh: '變更後', en: 'After' },
        'id.th_location':        { zh: '位置', en: 'Location' },
        'id.no_events':          { zh: '尚無身分變更事件紀錄。資料會在 AIS 排程執行後自動累積。', en: 'No identity change events yet. Data will accumulate after AIS scheduled runs.' },

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
        'idx.about_csis':        { zh: '海底電纜威脅偵測', en: 'Submarine Cable Threat Detection' },
        'idx.about_csis_1':      { zh: '海纜鄰近活動：航跡經過海纜路線 5km 內', en: 'Cable proximity: Vessel tracks within 5km of cable routes' },
        'idx.about_csis_2':      { zh: 'Z字型移動模式：頻繁大幅轉向（疑似拖錨破壞）', en: 'Zigzag pattern: Frequent large heading changes (potential anchor dragging)' },
        'idx.about_csis_3':      { zh: '200m等深線活動 + AIS 身分變更偵測', en: '200m depth contour activity + AIS identity change detection' },
        'idx.about_multi':       { zh: '多源數據整合', en: 'Multi-Source Data Integration' },
        'idx.about_multi_1':     { zh: 'Global Fishing Watch API：SAR 暗船偵測、漁撈努力量', en: 'Global Fishing Watch API: SAR dark vessels, fishing effort' },
        'idx.about_multi_2':     { zh: 'AISStream.io：即時 AIS 船舶追蹤', en: 'AISStream.io: Real-time AIS vessel tracking' },
        'idx.about_multi_3':     { zh: '解放軍軍事出動記錄：MND 每日國防消息', en: 'PLA military sortie records: MND daily defense reports' },
        'idx.about_refs':        { zh: '參考文獻', en: 'References' },

        // ── Introduction 頁面 ──
        'intro.hero_title':      { zh: '台灣周邊海域灰色地帶監測', en: 'Gray Zone Activity Monitor around Taiwan' },
        'intro.hero_subtitle':   { zh: '開源衛星數據 • 即時船舶追蹤 • 海纜安全', en: 'Open Satellite Data • Real-time Vessel Tracking • Cable Security' },
        'intro.gz_title':        { zh: '🌊 什麼是灰色地帶活動？', en: '🌊 What is Gray Zone Activity?' },
        'intro.gz_p1':           { zh: '「灰色地帶」是指介於和平與戰爭之間的模糊狀態。在台灣周邊海域，這包括：大量漁船越界作業、船隻關閉 AIS 定位訊號成為「暗船」、海上民兵偽裝為漁船活動，以及與軍事演習高度相關的異常船舶行為。', en: 'The "gray zone" refers to the ambiguous space between peace and war. Around Taiwan, this includes: fishing fleets crossing maritime boundaries, vessels disabling AIS transponders to become "dark ships," maritime militia disguised as fishing boats, and anomalous vessel behavior closely correlated with military exercises.' },
        'intro.gz_p2':           { zh: '這些活動不構成傳統意義上的軍事衝突，卻持續侵蝕海上秩序、消耗監測資源，並可能為進一步行動創造條件。透過衛星與 AIS 數據的交叉比對，我們能追蹤這些隱蔽的海上活動。', en: 'These activities fall short of traditional military conflict but continuously erode maritime order, drain monitoring resources, and may create conditions for further action. By cross-referencing satellite imagery and AIS data, we can track these covert maritime activities.' },
        'intro.cable_title':     { zh: '🔌 海底電纜與灰色地帶', en: '🔌 Submarine Cables & the Gray Zone' },
        'intro.cable_p1':        { zh: '台灣超過 95% 的對外網路通訊依賴海底電纜。這些電纜集中通過台灣海峽與周邊海域——正是灰色地帶活動最頻繁的區域。', en: 'Over 95% of Taiwan\'s international internet traffic relies on submarine cables. These cables pass through the Taiwan Strait and surrounding waters — precisely where gray zone activity is most intense.' },
        'intro.cable_p2':        { zh: '拖網漁船的錨泊與作業是電纜損壞的主因之一。當大量不明船隻在電纜路線附近活動，便構成潛在威脅。本站地圖圖層可疊加顯示海底電纜路線與船隻分布，協助識別風險。', en: 'Anchor dragging by fishing trawlers is a leading cause of cable damage. When unidentified vessels operate near cable routes, they pose a potential threat. Our map layers overlay submarine cable routes with vessel positions to help identify risks.' },
        'intro.tools_title':     { zh: '🧰 監測工具', en: '🧰 Monitoring Tools' },
        'intro.cta':             { zh: '開始監測 →', en: 'Start Monitoring →' },

        // ── Dark Vessels 頁面 ──
        'dv.map_title':          { zh: '📍 暗船位置分布（SAR 衛星偵測、無 AIS 匹配）', en: '📍 Dark Vessel Locations (SAR Detection, No AIS Match)' },
        'dv.daily_title':        { zh: '📊 每日暗船數量趨勢', en: '📊 Daily Dark Vessel Trend' },
        'dv.region_title':       { zh: '🗺️ 各區域暗船比較', en: '🗺️ Regional Dark Vessel Comparison' },
        'dv.flag_title':         { zh: '🚩 有 AIS 船隻國旗分布（台灣海峽）▸ 點擊展開', en: '🚩 AIS Vessel Flag Distribution (Taiwan Strait) ▸ tap to expand' },
        'dv.flag_ref_title':     { zh: '📋 全球主要船旗國參考', en: '📋 Top Flag States Reference' },
        'dv.flag_ref_lr':        { zh: '連續蟬聯全球最大船旗註冊國', en: 'Largest flag state globally' },
        'dv.flag_ref_pa':        { zh: '長期佔據榜首，目前位居第二', en: 'Long-time #1, now #2' },
        'dv.flag_ref_mh':        { zh: '持續穩定增長，維持前三', en: 'Steady growth, top 3' },
        'dv.flag_ref_hk':        { zh: '2025 年初被新加坡超越', en: 'Overtaken by Singapore in early 2025' },
        'dv.flag_ref_sg':        { zh: '增長強勁，2024 年穩居前五', en: 'Strong growth, top 5 since 2024' },
        'dv.flag_ref_mt':        { zh: '歐洲最大船旗國之一', en: 'Largest European flag state' },
        'dv.flag_ref_bs':        { zh: '傳統航運強國，郵輪註冊量大', en: 'Major cruise ship registry' },
        'dv.flag_ref_gr':        { zh: '傳統海運大國', en: 'Traditional maritime power' },
        'dv.flag_ref_jp':        { zh: '2024 年表現穩定', en: 'Stable performance in 2024' },
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
        'st.flag_title':         { zh: '🚩 有 AIS 船隻國旗分布 ▸ 點擊展開', en: '🚩 AIS Vessel Flag Distribution ▸ tap to expand' },
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

        // ── AIS 船位動畫 AIS Track Animation ──
        'nav.ais_anim':          { zh: '船位動畫', en: 'AIS Animation' },
        'nav.mob_ais_anim':      { zh: '船位', en: 'Track' },
        'title.ais_anim':        { zh: '🚢 可疑船位動畫 Suspicious Vessel Animation', en: '🚢 Suspicious Vessel Animation' },
        'ais_anim.range':        { zh: '範圍', en: 'Range' },
        'ais_anim.days_7':       { zh: '7 天', en: '7 Days' },
        'ais_anim.days_14':      { zh: '14 天', en: '14 Days' },
        'ais_anim.speed':        { zh: '速度', en: 'Speed' },
        'ais_anim.map_title':    { zh: '📍 可疑船隻 AIS 軌跡動畫', en: '📍 Suspicious Vessel AIS Track Animation' },
        'ais_anim.chart_title':  { zh: '📊 各時段可疑船數量', en: '📊 Suspicious Vessels by Time Period' },
        'ais_anim.no_data':      { zh: '⚠️ 尚無軌跡資料', en: '⚠️ No track data yet' },
        'ais_anim.no_data_msg':  { zh: '尚無資料，請等待 AIS 資料累積', en: 'No data yet, please wait for AIS data accumulation' },
        'ais_anim.load_fail':    { zh: '❌ 載入失敗', en: '❌ Loading failed' },
        'ais_anim.frame':        { zh: '第 {0} / {1} 幀', en: 'Frame {0} of {1}' },
        'ais_anim.suspicious':   { zh: '可疑船', en: 'Suspicious' },
        'ais_anim.total_track':  { zh: '追蹤船隻', en: 'Tracked Vessels' },
        'ais_anim.show_trails':  { zh: '顯示軌跡', en: 'Show Trails' },
        'ais_anim.popup_name':   { zh: '船名', en: 'Name' },
        'ais_anim.popup_mmsi':   { zh: 'MMSI', en: 'MMSI' },
        'ais_anim.popup_type':   { zh: '類型', en: 'Type' },
        'ais_anim.popup_speed':  { zh: '航速', en: 'Speed' },

        // ── 大陸漁船 CN Fishing ──
        'nav.cn_fishing':        { zh: '大陸漁船', en: 'CN Fishing' },
        'nav.mob_cn_fishing':    { zh: '大陸', en: 'CN' },
        'title.cn_fishing':      { zh: '🇨🇳 大陸漁船動畫 CN Fishing Vessel Animation', en: '🇨🇳 CN Fishing Vessel Animation' },

        // ── 海纜與偵測 Cable & Detection ──
        'ais_anim.filter_all':       { zh: '全部船隻', en: 'All Vessels' },
        'ais_anim.filter_cn':        { zh: '大陸漁船', en: 'CN Fishing' },
        'ais_anim.filter_suspicious':{ zh: '可疑船', en: 'Suspicious' },
        'ais_anim.filter_loiter':    { zh: '滯留偵測', en: 'Loitering' },
        'ais_anim.filter_zigzag':    { zh: 'Z字型航行', en: 'Zigzag' },
        'ais_anim.filter_near_cable':{ zh: '電纜周邊', en: 'Near Cable' },
        'ais_anim.layer_cables':     { zh: '海底電纜', en: 'Submarine Cables' },
        'ais_anim.cable_buffer':     { zh: '電纜緩衝區 (海浬)', en: 'Cable Buffer (nm)' },
        'ais_anim.detect_loiter':    { zh: '滯留', en: 'Loiter' },
        'ais_anim.detect_zigzag':    { zh: 'Z字型', en: 'Zigzag' },
        'ais_anim.detect_cable':     { zh: '近電纜', en: 'Near Cable' },
        'ais_anim.loiter_hours':     { zh: '滯留 {0}h', en: 'Loiter {0}h' },

        // ── 海纜狀態 ──
        'cable.status_title':        { zh: '🔌 海纜狀態', en: '🔌 Cable Status' },
        'cable.fault':               { zh: '障礙中', en: 'Faulted' },
        'cable.repaired':            { zh: '已修復', en: 'Repaired' },
        'cable.normal':              { zh: '正常', en: 'Normal' },
        'cable.all_normal':          { zh: '所有海纜正常運作', en: 'All cables operational' },
        'cable.source_link':         { zh: '資料來源：數位發展部', en: 'Source: MODA' },

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
        'app.csis_suspicious':   { zh: '⚠️ 海纜威脅可疑', en: '⚠️ Cable Threat Suspect' },
        'app.mmsi':              { zh: 'MMSI:', en: 'MMSI:' },
        'app.type':              { zh: '類型:', en: 'Type:' },
        'app.speed':             { zh: '航速:', en: 'Speed:' },
        'app.destination':       { zh: '目的地:', en: 'Destination:' },
        'app.risk':              { zh: '風險:', en: 'Risk:' },
        'app.score':             { zh: '分數:', en: 'Score:' },
        'app.show_track':        { zh: '📍 查詢航跡', en: '📍 Show Track' },
        'app.loading_track':     { zh: '航跡載入中...', en: 'Loading track...' },
        'app.extracting_track':  { zh: '正在從歷史資料提取...', en: 'Extracting from history...' },
        'app.no_track_data':     { zh: '此船隻尚無航跡資料', en: 'No track data for this vessel' },
        'app.track_empty':       { zh: '航跡資料為空', en: 'Track data is empty' },
        'app.track_info_title':  { zh: '📍 航跡資訊', en: '📍 Track Info' },
        'app.track_points':      { zh: '航跡點數:', en: 'Track points:' },
        'app.track_source':      { zh: '來源:', en: 'Source:' },
        'app.track_source_pre':  { zh: '預產生', en: 'Pre-generated' },
        'app.track_source_live': { zh: '即時提取', en: 'Live extracted' },
        'app.clear_track':       { zh: '✕ 清除', en: '✕ Clear' },
        'app.snapshot':          { zh: '📋 截圖', en: '📋 Snap' },
        'app.snapshot_ok':       { zh: '已複製截圖到剪貼簿', en: 'Snapshot copied to clipboard' },
        'app.snapshot_saved':    { zh: '已下載截圖', en: 'Snapshot downloaded' },
        'app.snapshot_fail':     { zh: '截圖失敗', en: 'Snapshot failed' },
        'app.track_load_fail':   { zh: '航跡載入失敗', en: 'Track load failed' },
        'app.sanctioned':        { zh: '🚫 UN 制裁船舶', en: '🚫 UN Sanctioned Vessel' },
        'app.sanction_res':      { zh: 'UNSCR', en: 'UNSCR' },

        // ── Charts 模組 ──
        'chart.detect':          { zh: '偵測', en: 'det' },
        'chart.dark':            { zh: '暗船', en: 'dark' },
        'chart.unit_wan':        { zh: '萬', en: '0k' },

        // ── Onboarding 導覽 ──
        'ob.skip':               { zh: '跳過', en: 'Skip' },
        'ob.next':               { zh: '下一步', en: 'Next' },
        'ob.prev':               { zh: '上一步', en: 'Back' },
        'ob.done':               { zh: '開始使用', en: 'Get Started' },
        'ob.step':               { zh: '{0} / {1}', en: '{0} / {1}' },

        'ob.t1':                 { zh: '歡迎使用台灣灰色地帶監測', en: 'Welcome to Taiwan Gray Zone Monitor' },
        'ob.d1':                 { zh: '本系統整合 AIS 船舶資料與 SAR 衛星影像，即時監控台灣周邊海域的灰色地帶活動。以下快速介紹主要功能。', en: 'This system integrates AIS vessel data and SAR satellite imagery to monitor gray zone maritime activity around Taiwan in real time. Here\'s a quick tour of key features.' },

        'ob.t2':                 { zh: '海纜威脅偵測', en: 'Cable Threat Detection' },
        'ob.d2':                 { zh: '系統自動分析船隻行為：海纜鄰近活動、Z字型移動、低速徘徊、AIS 身分變更等，依船型加權計分，標記可疑船隻。商船（錨鍊長、噸位大）權重較高，漁船權重較低。', en: 'The system analyzes vessel behavior: cable proximity, zigzag movement, slow loitering, AIS identity changes, and more. Scores are weighted by vessel type — cargo/tanker (high cable risk) score higher, while fishing vessels score lower.' },

        'ob.t3':                 { zh: '地圖圖層', en: 'Map Layers' },
        'ob.d3':                 { zh: '左側圖層控制可切換顯示：漁撈熱點、AIS 船舶、SAR 暗船、海底電纜路線、領海基線等。點擊船舶標記可查看詳細資訊與航跡。', en: 'Use layer controls to toggle: fishing hotspots, AIS vessels, SAR dark vessels, submarine cable routes, baselines, and more. Click any vessel marker for details and track history.' },

        'ob.t4':                 { zh: '暗船與衛星偵測', en: 'Dark Vessels & SAR' },
        'ob.d4':                 { zh: '「暗船」指關閉 AIS 的船舶，僅能透過 SAR 衛星雷達偵測。系統整合 Global Fishing Watch 資料，顯示這些隱匿船舶的位置與活動趨勢。', en: '"Dark vessels" are ships with AIS off, detectable only via SAR satellite radar. The system integrates Global Fishing Watch data to reveal their positions and activity trends.' },

        'ob.t5':                 { zh: '更多功能', en: 'More Features' },
        'ob.d5':                 { zh: '統計頁面提供歷史趨勢圖表；軌跡動畫頁面播放船舶移動軌跡；身分追蹤頁面記錄 AIS 變更事件。使用底部導航列或頂部選單切換頁面。', en: 'Statistics page offers historical charts; Trail Animation plays vessel movement; Identity History tracks AIS changes. Use the bottom nav bar or top menu to switch between pages.' },
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

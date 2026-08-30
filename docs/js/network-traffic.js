/**
 * 網路流量監控頁 — Taiwan Gray Zone Monitor
 *
 * 資料來源 cf_radar.json（src/fetch_cloudflare_radar.py 產生）。異常事件與
 * 基線都是後端算好的，本檔只負責呈現；**唯一的例外是示範模式**：資料管線還沒
 * 跑過（或 token 未設定）時，用內建的合成資料展示這頁長什麼樣，並在畫面上以
 * 明顯的橫幅標示「示範資料」，避免被誤認為真實觀測。
 */
(function () {
    'use strict';

    var SEVERITY = {
        critical: { color: '#ff3366', zh: '嚴重', en: 'Critical' },
        high: { color: '#ff6b35', zh: '高', en: 'High' },
        medium: { color: '#ffd700', zh: '中', en: 'Medium' }
    };

    var REGION_META = {
        taiwan: { label_zh: '台灣本島', label_en: 'Taiwan', lat: 23.72, lon: 120.96 },
        kinmen: { label_zh: '金門', label_en: 'Kinmen', lat: 24.44, lon: 118.32 },
        lienchiang: { label_zh: '馬祖（連江）', label_en: 'Matsu (Lienchiang)', lat: 26.16, lon: 119.95 },
        penghu: { label_zh: '澎湖', label_en: 'Penghu', lat: 23.57, lon: 119.62 }
    };
    var REGION_COLORS = {
        normal: '#00ff88',
        watch: '#ffd700',
        alert: '#ff3366',
        unknown: '#8aa4c8'
    };

    var state = {
        data: null,
        islandData: null,
        countyData: null,       // cf_radar_counties.json（縣市級網速／流量指數）
        countyGeo: null,        // tw_counties.geojson（22 縣市界）
        countyMode: null,       // speed / traffic / reach
        activeCounty: null,     // 點選的縣市 ISO（趨勢圖切到該縣市）
        countyLayer: null,
        countyLayers: {},
        isDemo: false,
        countyDemo: false,
        activeId: null,
        chart: null,
        map: null,
        markerLayer: null,
        regionMarkers: {},
        sourceErrors: {}
    };

    function zh() {
        return (typeof i18n !== 'undefined' ? i18n.getLang() : 'zh') !== 'en';
    }
    function t(zhText, enText) { return zh() ? zhText : enText; }
    function el(id) { return document.getElementById(id); }
    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
        });
    }

    // ── 示範資料 ────────────────────────────────────────────────────────────
    // 合成一條有日／週週期的流量序列，注入一次 6 小時的大幅下掉（海纜中斷的典型
    // 特徵：持續性位階下移，而非瞬間尖刺）。基線＝未注入前的乾淨曲線，因此這裡
    // 不需要（也不該）在前端重做一次偵測——真實數字一律由 Python 端算。
    function buildDemo() {
        var hours = 14 * 24;
        var now = new Date();
        now.setMinutes(0, 0, 0);
        var start = new Date(now.getTime() - hours * 3600 * 1000);

        function mkSeries(id, label, direction, amp, base, cutIdx, cutHours, cutPct) {
            var timestamps = [], values = [], baseline = [];
            for (var h = 0; h < hours; h++) {
                var d = new Date(start.getTime() + h * 3600 * 1000);
                var daily = amp * Math.sin((d.getHours() - 3) / 24 * 2 * Math.PI);
                var weekly = (d.getDay() === 0 || d.getDay() === 6) ? -amp * 0.25 : 0;
                var clean = base + daily + weekly;
                // 用固定種子的偽隨機，重新整理不會跳動
                var noise = (Math.sin(h * 12.9898) * 43758.5453) % 1;
                var obs = clean * (1 + noise * 0.05);
                if (h >= cutIdx && h < cutIdx + cutHours) {
                    obs = obs * (1 - cutPct / 100);
                }
                timestamps.push(d.toISOString());
                values.push(Math.round(obs * 100) / 100);
                baseline.push(Math.round(clean * 100) / 100);
            }
            var devs = [];
            for (var i = cutIdx; i < cutIdx + cutHours; i++) {
                devs.push((values[i] - baseline[i]) / baseline[i] * 100);
            }
            var worst = direction === 'drop' ? Math.min.apply(null, devs) : Math.max.apply(null, devs);
            return {
                id: id, label: label, direction: direction,
                points: hours, baseline_coverage: 1,
                timestamps: timestamps, values: values, baseline: baseline,
                anomalies: [{
                    onset: timestamps[cutIdx],
                    end: timestamps[cutIdx + cutHours - 1],
                    duration_hours: cutHours,
                    points: cutHours,
                    direction: direction,
                    peak_z: direction === 'drop' ? -11.4 : 9.8,
                    max_deviation_pct: Math.round(worst * 10) / 10,
                    mean_deviation_pct: Math.round(worst * 10) / 10,
                    severity: 'critical',
                    baseline_at_onset: baseline[cutIdx],
                    value_at_onset: values[cutIdx],
                    candidate_summary: { commercial: 1, gov: 0, other: 4, total: 5 },
                    correlated_vessels: [
                        {
                            mmsi: '414000000', name: 'DEMO BULKER', type: 'cargo',
                            lat: 26.14, lon: 119.98, speed: 0.4,
                            nearest_cable_km: 0.31, points: 5,
                            timestamp: timestamps[Math.max(0, cutIdx - 6)]
                        },
                        {
                            mmsi: '412000000', name: 'DEMO FISHER', type: 'fishing',
                            lat: 26.09, lon: 120.03, speed: 1.8,
                            nearest_cable_km: 1.24, points: 2,
                            timestamp: timestamps[Math.max(0, cutIdx - 3)]
                        }
                    ]
                }]
            };
        }

        var cut = hours - 54;   // 大約兩天前
        return {
            generated_at: now.toISOString(),
            window_days: 14,
            agg_interval: '1h',
            series: [
                mkSeries('demo_netflows', t('台灣整體網路流量 (netflows)', 'Taiwan netflows'),
                    'drop', 30, 100, cut, 6, 45),
                mkSeries('demo_as3462', t('中華電信 HiNet (AS3462) 流量', 'HiNet AS3462 traffic'),
                    'drop', 26, 90, cut, 6, 52),
                mkSeries('demo_latency', t('台灣連線延遲 (IQI p50)', 'Taiwan latency (IQI p50)'),
                    'spike', 8, 42, cut, 6, -70)
            ],
            outage_annotations: [],
            summary: {}
        };
    }

    // 管線還沒跑過時，用縣市界圖裡的縣市合成一組頻寬值，讓地圖不是一片灰。
    // 值由 ISO 代碼決定（固定種子），重新整理不會跳動；示範橫幅照樣掛著。
    function buildDemoCounties() {
        var features = (state.countyGeo && state.countyGeo.features) || [];
        if (!features.length) return null;
        var now = new Date();
        now.setMinutes(0, 0, 0);
        return {
            generated_at: now.toISOString(),
            demo: true,
            counties: features.map(function (feature) {
                var props = feature.properties || {};
                var seed = 0;
                for (var i = 0; i < props.iso.length; i++) seed += props.iso.charCodeAt(i);
                var latest = Math.round((60 + (seed % 47) * 3.1) * 10) / 10;
                var timestamps = [], values = [];
                for (var h = 0; h < 56; h++) {
                    var d = new Date(now.getTime() - (56 - h) * 3 * 3600 * 1000);
                    timestamps.push(d.toISOString());
                    values.push(Math.round((latest * (0.9 + ((seed + h) % 7) / 35)) * 10) / 10);
                }
                return {
                    iso: props.iso, name_zh: props.name_zh, name_en: props.name_en,
                    status: 'available', metric_id: 'iqi_bandwidth',
                    metric_label_zh: '頻寬（IQI 中位數）',
                    metric_label_en: 'Bandwidth (IQI median)',
                    unit: 'Mbps', higher_is_better: true, is_speed: true,
                    latest: latest, baseline: latest, pct_vs_baseline: 0,
                    level: 'normal', anomalies: [],
                    series: { timestamps: timestamps, values: values, bucket_hours: 3 }
                };
            })
        };
    }

    function recomputeSummary(data) {
        var events = [];
        data.series.forEach(function (s) {
            (s.anomalies || []).forEach(function (e) { events.push(e); });
        });
        var bySev = {};
        var actionable = 0;
        events.forEach(function (e) {
            bySev[e.severity] = (bySev[e.severity] || 0) + 1;
            var cs = e.candidate_summary || {};
            if (cs.commercial || cs.gov) actionable++;
        });
        data.summary = {
            series_analyzed: data.series.length,
            anomaly_count: events.length,
            by_severity: bySev,
            anomalies_with_commercial_or_gov_candidates: actionable
        };
        return data;
    }

    // ── 渲染 ────────────────────────────────────────────────────────────────

    function fmtTime(iso) {
        var d = new Date(iso);
        if (isNaN(d)) return iso || '--';
        return d.toLocaleString(zh() ? 'zh-TW' : 'en-US', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
        });
    }

    function fmtAccessibleTime(iso) {
        var d = new Date(iso);
        if (isNaN(d)) return iso || '--';
        return d.toLocaleString(zh() ? 'zh-TW' : 'en-US', {
            year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
            hour: '2-digit', minute: '2-digit', timeZoneName: 'short', hour12: false
        });
    }

    function renderBanner() {
        var banner = el('demoBanner');
        banner.style.display = state.isDemo ? 'flex' : 'none';
    }

    function renderStats() {
        var s = state.data.summary || {};
        var sev = s.by_severity || {};
        var current = currentEvents();
        var regions = {};
        var sources = {};
        var maxDuration = 0;
        var leads = 0;
        current.forEach(function (item) {
            regions[t('台灣本島', 'Taiwan')] = true;
            sources[item.source] = true;
            maxDuration = Math.max(maxDuration, item.event.duration_hours || 0);
            var cs = item.event.candidate_summary || {};
            leads += (cs.commercial || 0) + (cs.gov || 0);
        });
        el('statRegions').textContent = Object.keys(regions).length;
        el('statDuration').textContent = maxDuration ? maxDuration + ' h' : '0 h';
        el('statSources').textContent = Object.keys(sources).length;
        el('statLeads').textContent = leads;
        el('historySeries').textContent = s.series_analyzed || 0;
        el('historyAnomalies').textContent = s.anomaly_count || 0;
        el('historyWindow').textContent = state.data.window_days || 28;
        el('historyWindowEn').textContent = state.data.window_days || 28;
        var worst = sev.critical ? 'critical' : (sev.high ? 'high' : (sev.medium ? 'medium' : null));
        var worstEl = el('historyWorst');
        if (worst) {
            worstEl.textContent = t(SEVERITY[worst].zh, SEVERITY[worst].en);
            worstEl.style.color = SEVERITY[worst].color;
        } else {
            worstEl.textContent = t('無', 'None');
            worstEl.style.color = 'var(--accent-green)';
        }
    }

    function currentEvents() {
        var end = new Date(state.data.generated_at || Date.now()).getTime();
        var cutoff = end - 2 * 3600 * 1000;
        var result = [];
        (state.data.series || []).forEach(function (series) {
            (series.anomalies || []).forEach(function (event) {
                if (new Date(event.end || event.onset).getTime() >= cutoff) {
                    result.push({ event: event, label: series.label, source: series.datasource || series.id });
                }
            });
        });
        return result;
    }

    function flattenAnomalies(seriesList) {
        var events = [];
        (seriesList || []).forEach(function (series) {
            (series.anomalies || []).forEach(function (event) { events.push(event); });
        });
        return events;
    }

    function regionViews() {
        var mainSeries = (state.data && state.data.series) || [];
        var mainCurrent = currentEvents();
        var mainAvailable = Boolean(state.data && state.data.generated_at && mainSeries.length);
        var meta = REGION_META.taiwan;
        var views = [{
            id: 'taiwan',
            label_zh: meta.label_zh,
            label_en: meta.label_en,
            lat: meta.lat,
            lon: meta.lon,
            level: !mainAvailable ? 'unknown' : (mainCurrent.length ? 'alert' : 'normal'),
            status_zh: !mainAvailable ? '資料不足' : (mainCurrent.length ? '目前偵測到異常' : '目前未見持續異常'),
            status_en: !mainAvailable ? 'No data' : (mainCurrent.length ? 'Ongoing anomaly detected' : 'No ongoing anomaly'),
            anomaly_count: flattenAnomalies(mainSeries).length,
            current_count: mainCurrent.length,
            signal_count: mainSeries.length,
            source_zh: state.isDemo ? '示範資料' : 'Cloudflare Radar',
            source_en: state.isDemo ? 'Demo data' : 'Cloudflare Radar',
            updated_at: state.data && state.data.generated_at
        }];

        var islandById = {};
        ((state.islandData && state.islandData.islands) || []).forEach(function (island) {
            islandById[island.id] = island;
        });
        ['kinmen', 'lienchiang', 'penghu'].forEach(function (id) {
            var fallback = REGION_META[id];
            var island = islandById[id];
            var series = (island && island.series) || [];
            var events = flattenAnomalies(series);
            var available = Boolean(island && island.status !== 'unavailable' && series.length);
            views.push({
                id: id,
                label_zh: (island && island.label_zh) || fallback.label_zh,
                label_en: (island && island.label_en) || fallback.label_en,
                lat: island && Number.isFinite(Number(island.lat)) ? Number(island.lat) : fallback.lat,
                lon: island && Number.isFinite(Number(island.lon)) ? Number(island.lon) : fallback.lon,
                level: !available ? 'unknown' : (events.length ? 'watch' : 'normal'),
                status_zh: !available ? '資料不足' : (events.length ? '近 28 天有異常' : '近 28 天未見異常'),
                status_en: !available ? 'No data' : (events.length ? 'Anomaly in the last 28 days' : 'No anomaly in the last 28 days'),
                anomaly_count: events.length,
                current_count: 0,
                signal_count: series.length,
                source_zh: 'IODA 網路中斷偵測',
                source_en: 'IODA outage detection',
                updated_at: state.islandData && state.islandData.generated_at,
                error_reason: island && island.error_reason
            });
        });
        return views;
    }

    function regionName(view) { return zh() ? view.label_zh : view.label_en; }
    function regionStatus(view) { return zh() ? view.status_zh : view.status_en; }
    function regionSource(view) { return zh() ? view.source_zh : view.source_en; }

    function regionPopupHtml(view) {
        var currentRow = view.id === 'taiwan'
            ? '<div class="region-popup-row">' + t('目前異常訊號：', 'Abnormal signals now: ') +
                '<strong>' + view.current_count + '</strong></div>'
            : '';
        return '<div class="region-popup">' +
            '<div class="region-popup-title">' + escapeHtml(regionName(view)) + '</div>' +
            '<div class="region-popup-state" style="color:' + REGION_COLORS[view.level] + '">' +
                escapeHtml(regionStatus(view)) + '</div>' +
            currentRow +
            '<div class="region-popup-row">' + t('近 28 天異常：', 'Anomalies in 28 days: ') +
                '<strong>' + view.anomaly_count + '</strong></div>' +
            '<div class="region-popup-row">' + t('可用觀測訊號：', 'Available signals: ') +
                '<strong>' + view.signal_count + '</strong></div>' +
            '<div class="region-popup-row">' + t('資料來源：', 'Source: ') +
                '<strong>' + escapeHtml(regionSource(view)) + '</strong></div>' +
            '<div class="region-popup-row">' + t('更新：', 'Updated: ') +
                '<strong>' + (view.updated_at ? escapeHtml(fmtTime(view.updated_at)) : '—') + '</strong></div>' +
            (view.level === 'unknown' ? '<div class="region-popup-row">' +
                t('資料不足不代表已確認斷網。', 'Insufficient data does not confirm an outage.') + '</div>' : '') +
            '</div>';
    }

    // ── 縣市色塊（choropleth）──────────────────────────────────────────────
    // 三種模式的資料來源、單位、甚至「高好還是低好」都不一樣，因此模式決定
    // 取值函式與色階方向，而不是在畫圖時到處 if。只有真的有資料的模式才會
    // 出現在切換列上——寧可少一顆按鈕，也不要讓使用者點進一張全灰的地圖。
    var COUNTY_MODE_META = {
        speed: { zh: '網速（Cloudflare）', en: 'Speed (Cloudflare)' },
        traffic: { zh: '流量指數（Cloudflare）', en: 'Traffic index (Cloudflare)' },
        reach: { zh: '連線可達性（IODA）', en: 'Reachability (IODA)' }
    };
    var COUNTY_MODE_ORDER = ['speed', 'traffic', 'reach'];
    // 由暗到亮的連續色階（暗＝數值差）。與站台既有的青色系一致。
    var COUNTY_RAMP = ['#16394f', '#1c6480', '#2b9fb0', '#37d0c2', '#7bf3d5'];
    var COUNTY_NO_DATA = 'rgba(138,164,200,0.18)';

    function countyRadarRecords() {
        return ((state.countyData && state.countyData.counties) || []);
    }
    function countyIodaRecords() {
        return ((state.islandData && state.islandData.counties) || []);
    }

    function countyViews() {
        var radar = {}, ioda = {};
        countyRadarRecords().forEach(function (c) { radar[c.iso] = c; });
        countyIodaRecords().forEach(function (c) { ioda[c.iso] = c; });
        var features = (state.countyGeo && state.countyGeo.features) || [];
        return features.map(function (feature) {
            var props = feature.properties || {};
            var r = radar[props.iso] || null;
            var i = ioda[props.iso] || null;
            return {
                iso: props.iso,
                label_zh: props.name_zh,
                label_en: props.name_en,
                label_point: props.label_point,
                radar: r,
                ioda: i,
                // 色塊外框用的健康狀態：兩個來源取最嚴重的那個
                level: worstLevel(r && r.level, i && i.level)
            };
        });
    }

    var LEVEL_RANK = { unknown: 0, normal: 1, watch: 2, alert: 3 };
    function worstLevel(a, b) {
        var la = LEVEL_RANK[a] || 0, lb = LEVEL_RANK[b] || 0;
        var best = Math.max(la, lb);
        return Object.keys(LEVEL_RANK).filter(function (k) {
            return LEVEL_RANK[k] === best;
        })[0];
    }

    function countyName(view) { return zh() ? view.label_zh : view.label_en; }

    // 模式 → 該縣市的數值。回 null 代表這個模式對這個縣市沒有資料（畫成灰色）。
    function countyMetric(view, mode) {
        if (mode === 'reach') {
            if (!view.ioda || view.ioda.status !== 'available') return null;
            var signals = view.ioda.signals || [];
            var reachable = signals.filter(function (s) {
                return s.latest != null;
            }).length;
            return {
                value: reachable,
                display: reachable + '/' + Math.max(signals.length, 1),
                unit: '',
                unitLabel: t('可用訊號', 'signals'),
                higherIsBetter: true,
                // 可達性用異常狀態上色，數值只是輔助說明
                colorBy: 'level'
            };
        }
        var r = view.radar;
        if (!r || r.status !== 'available' || r.latest == null) return null;
        if (mode === 'speed' && !r.is_speed) return null;
        if (mode === 'traffic' && r.is_speed) return null;
        return {
            value: r.latest,
            display: formatMetricValue(r.latest),
            unit: r.unit || '',
            unitLabel: zh() ? r.metric_label_zh : r.metric_label_en,
            higherIsBetter: r.higher_is_better !== false,
            pct: r.pct_vs_baseline,
            colorBy: 'value'
        };
    }

    function formatMetricValue(value) {
        if (value == null) return '—';
        var abs = Math.abs(value);
        if (abs >= 100) return String(Math.round(value));
        if (abs >= 10) return value.toFixed(1);
        return value.toFixed(2);
    }

    function availableCountyModes(views) {
        return COUNTY_MODE_ORDER.filter(function (mode) {
            return views.some(function (v) { return countyMetric(v, mode); });
        });
    }

    // 色階：以當下這個模式所有縣市的數值分位數切 5 級。用分位數而非等距，
    // 因為六都與離島的量級差很大，等距會讓 21 個縣市擠在同一色。
    function buildCountyScale(views, mode) {
        var entries = [];
        var higherIsBetter = true;
        var units = {};
        views.forEach(function (v) {
            var m = countyMetric(v, mode);
            if (!m || m.colorBy !== 'value') return;
            entries.push(m.value);
            higherIsBetter = m.higherIsBetter;
            units[m.unit] = true;
        });
        entries.sort(function (a, b) { return a - b; });
        var mixedUnits = Object.keys(units).length > 1;
        var thresholds = [];
        for (var k = 1; k < COUNTY_RAMP.length; k++) {
            thresholds.push(entries[Math.floor(entries.length * k / COUNTY_RAMP.length)]);
        }
        return {
            empty: !entries.length,
            // 單位混在一起（有的縣市回頻寬、有的回延遲）就不能用同一條色階
            mixedUnits: mixedUnits,
            min: entries[0],
            max: entries[entries.length - 1],
            higherIsBetter: higherIsBetter,
            color: function (value) {
                if (value == null || !entries.length || mixedUnits) return COUNTY_NO_DATA;
                var idx = 0;
                while (idx < thresholds.length && value >= thresholds[idx]) idx++;
                return COUNTY_RAMP[higherIsBetter ? idx : COUNTY_RAMP.length - 1 - idx];
            }
        };
    }

    function countyFill(view, mode, scale) {
        var metric = countyMetric(view, mode);
        if (!metric) return COUNTY_NO_DATA;
        if (metric.colorBy === 'level') return REGION_COLORS[view.level] || COUNTY_NO_DATA;
        return scale.color(metric.value);
    }

    function countyGroupLabel(view) {
        var r = view.radar;
        if (!r || !r.is_group_value) return '';
        return zh() ? (r.adm1_group_label_zh || '') : (r.adm1_group_label_en || '');
    }

    function countyPopupHtml(view, mode) {
        var metric = countyMetric(view, mode);
        var rows = ['<div class="region-popup-title">' + escapeHtml(countyName(view)) + '</div>'];
        // Radar 的台灣 ADM1 只有 4 個分區（Taipei / Takao / Fukien＝金馬 /
        // Taiwan＝其餘 18 縣市），沒有逐縣市的量測。把分區值講成縣市值就是謊報，
        // 所以每一格都要標明它實際來自哪個分區。
        var groupLabel = countyGroupLabel(view);
        if (groupLabel && (mode === 'speed' || mode === 'traffic')) {
            rows.push('<div class="region-popup-row region-popup-note">' +
                t('此數值為 Cloudflare Radar 的分區（', 'Value is for the Cloudflare Radar region (') +
                escapeHtml(groupLabel) +
                t('）值，非本縣市單獨量測。', '), not a measurement of this county alone.') +
                '</div>');
        }
        if (metric) {
            rows.push('<div class="region-popup-state" style="color:' +
                (REGION_COLORS[view.level] || '#8aa4c8') + '">' +
                escapeHtml(metric.display) + (metric.unit ? ' ' + escapeHtml(metric.unit) : '') +
                ' · ' + escapeHtml(metric.unitLabel || '') + '</div>');
            if (metric.pct != null) {
                rows.push('<div class="region-popup-row">' + t('相對季節基線：', 'vs baseline: ') +
                    '<strong>' + (metric.pct >= 0 ? '+' : '') + metric.pct + '%</strong></div>');
            }
        } else {
            rows.push('<div class="region-popup-state" style="color:#8aa4c8">' +
                t('此指標無資料', 'No data for this metric') + '</div>');
        }
        // Speed Test 是使用者實跑 speed.cloudflare.com 的中位數，和上面的 IQI
        // 指數不是同一種量測（台北實測 IQI p50 14.3 Mbps vs Speed Test 下載
        // 124.5 Mbps，差一個量級），因此另起一行並標明來源，不能混為一談。
        var speed = view.radar && view.radar.speed_test;
        if (speed && speed.bandwidth_download != null) {
            rows.push('<div class="region-popup-row">' +
                t('Speed Test 實測中位數：', 'Speed Test median: ') + '<strong>↓ ' +
                escapeHtml(formatMetricValue(speed.bandwidth_download)) + ' Mbps' +
                (speed.bandwidth_upload != null
                    ? ' / ↑ ' + escapeHtml(formatMetricValue(speed.bandwidth_upload)) + ' Mbps'
                    : '') +
                '</strong></div>');
            if (speed.latency_idle != null) {
                rows.push('<div class="region-popup-row">' +
                    t('Speed Test 閒置延遲：', 'Speed Test idle latency: ') +
                    '<strong>' + escapeHtml(formatMetricValue(speed.latency_idle)) +
                    ' ms</strong></div>');
            }
            rows.push('<div class="region-popup-row region-popup-note">' +
                t('（實測測速，與上方 IQI 品質指數不是同一種量測）',
                    '(user-run speed tests — a different measurement from the IQI index above)') +
                '</div>');
        }

        var anomalies = (view.radar && view.radar.anomalies ? view.radar.anomalies.length : 0) +
            (view.ioda && view.ioda.anomaly_count ? view.ioda.anomaly_count : 0);
        rows.push('<div class="region-popup-row">' + t('近 28 天異常：', 'Anomalies in 28 days: ') +
            '<strong>' + anomalies + '</strong></div>');
        if (view.ioda && view.ioda.latest_anomaly) {
            var la = view.ioda.latest_anomaly;
            rows.push('<div class="region-popup-row">' + t('最近事件：', 'Latest event: ') +
                '<strong>' + escapeHtml(fmtTime(la.onset)) + '</strong> · ' +
                escapeHtml((SEVERITY[la.severity] || SEVERITY.medium)[zh() ? 'zh' : 'en']) +
                '</div>');
        }
        rows.push('<div class="region-popup-row">' + t('資料來源：', 'Source: ') + '<strong>' +
            (view.radar && view.radar.status === 'available' ? 'Cloudflare Radar' : '') +
            (view.radar && view.radar.status === 'available' && view.ioda &&
                view.ioda.status === 'available' ? ' + ' : '') +
            (view.ioda && view.ioda.status === 'available' ? 'IODA' : '') +
            (!(view.radar && view.radar.status === 'available') &&
                !(view.ioda && view.ioda.status === 'available') ? '—' : '') +
            '</strong></div>');
        if (!metric) {
            rows.push('<div class="region-popup-row">' +
                t('資料不足不代表已確認斷網。', 'Insufficient data does not confirm an outage.') +
                '</div>');
        }
        if (countySeries(view)) {
            rows.push('<div class="region-popup-row"><button type="button" ' +
                'class="county-chart-btn" data-county-iso="' + escapeHtml(view.iso) + '">' +
                t('看這個縣市的趨勢圖', 'View this county\'s trend') + '</button></div>');
        }
        return '<div class="region-popup">' + rows.join('') + '</div>';
    }

    // 縣市趨勢圖用的序列。Cloudflare 的縣市序列優先（有基線可比），
    // 沒有就用 IODA 主訊號的縮圖序列。
    function countySeries(view) {
        var r = view.radar;
        if (r && r.series && (r.series.timestamps || []).length) {
            return {
                id: 'county:' + view.iso,
                label: countyName(view) + ' · ' + (zh() ? r.metric_label_zh : r.metric_label_en),
                timestamps: r.series.timestamps,
                values: r.series.values,
                baseline: (r.series.values || []).map(function () { return null; }),
                anomalies: r.anomalies || [],
                direction: r.higher_is_better === false ? 'spike' : 'drop',
                unit: r.unit
            };
        }
        var i = view.ioda;
        if (i && i.primary && (i.primary.timestamps || []).length) {
            return {
                id: 'county:' + view.iso,
                label: countyName(view) + ' · ' + i.primary.datasource,
                timestamps: i.primary.timestamps,
                values: i.primary.values,
                baseline: (i.primary.values || []).map(function () { return null; }),
                anomalies: [],
                direction: 'drop',
                unit: ''
            };
        }
        return null;
    }

    function activeCountyView() {
        if (!state.activeCounty) return null;
        return countyViews().filter(function (v) {
            return v.iso === state.activeCounty;
        })[0] || null;
    }

    function renderCountyModes(views) {
        var wrap = el('countyModes');
        if (!wrap) return;
        var modes = availableCountyModes(views);
        if (!modes.length) {
            wrap.innerHTML = '';
            wrap.style.display = 'none';
            return;
        }
        wrap.style.display = '';
        if (modes.indexOf(state.countyMode) < 0) state.countyMode = modes[0];
        wrap.innerHTML = modes.map(function (mode) {
            var meta = COUNTY_MODE_META[mode];
            return '<button type="button" class="county-mode' +
                (mode === state.countyMode ? ' active' : '') + '" data-mode="' + mode + '">' +
                escapeHtml(zh() ? meta.zh : meta.en) + '</button>';
        }).join('');
        wrap.querySelectorAll('[data-mode]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                state.countyMode = btn.dataset.mode;
                renderCountyModes(countyViews());
                renderConnectivityMap(regionViews());
            });
        });
    }

    function renderCountyLegend(views, scale) {
        var wrap = el('countyLegend');
        if (!wrap) return;
        if (!state.countyMode) { wrap.innerHTML = ''; return; }
        if (state.countyMode === 'reach') {
            wrap.innerHTML = '<span class="county-legend-label">' +
                t('IODA 可達性訊號狀態', 'IODA reachability status') + '</span>';
            return;
        }
        var unit = '';
        views.some(function (v) {
            var m = countyMetric(v, state.countyMode);
            if (m && m.unit) { unit = m.unit; return true; }
            return false;
        });
        if (scale.empty) {
            wrap.innerHTML = '<span class="county-legend-label">' +
                t('此指標目前沒有縣市資料', 'No county data for this metric') + '</span>';
            return;
        }
        if (scale.mixedUnits) {
            wrap.innerHTML = '<span class="county-legend-label">' +
                t('各縣市指標單位不一致，僅以異常狀態上色',
                    'Counties report different units; colored by anomaly status only') +
                '</span>';
            return;
        }
        var swatches = COUNTY_RAMP.map(function (color) {
            return '<i class="county-legend-swatch" style="background:' + color + '"></i>';
        }).join('');
        var lo = formatMetricValue(scale.min), hi = formatMetricValue(scale.max);
        if (!scale.higherIsBetter) { var tmp = lo; lo = hi; hi = tmp; }
        wrap.innerHTML = '<span class="county-legend-label">' +
            escapeHtml(scale.higherIsBetter ? t('低', 'Low') : t('高', 'High')) +
            ' ' + escapeHtml(lo) + escapeHtml(unit ? ' ' + unit : '') + '</span>' +
            swatches +
            '<span class="county-legend-label">' + escapeHtml(hi) +
            escapeHtml(unit ? ' ' + unit : '') + ' ' +
            escapeHtml(scale.higherIsBetter ? t('高', 'High') : t('低', 'Low')) + '</span>' +
            radarGroupLegendHtml();
    }

    // Radar 只有 4 個分區，所以地圖上最多只會出現 4 種顏色。把分區與其涵蓋範圍
    // 列出來，看的人才不會以為 22 個縣市各有一個量測。
    function radarGroupLegendHtml() {
        var groups = (state.countyData && state.countyData.adm1_groups) || [];
        if (!groups.length) return '';
        var items = groups.map(function (group) {
            var label = zh() ? group.label_zh : group.label_en;
            var value = group.latest == null ? t('無資料', 'no data')
                : formatMetricValue(group.latest) + (group.unit ? ' ' + group.unit : '');
            return '<span class="county-group-item">' + escapeHtml(label) + ' ' +
                escapeHtml(value) + '</span>';
        }).join('');
        return '<div class="county-group-note">' +
            t('Cloudflare Radar 的台灣分區只有 4 個（非 22 縣市）：',
                'Cloudflare Radar has only 4 Taiwan regions (not 22 counties): ') +
            items + '</div>';
    }

    function renderCountyLayer(views) {
        if (!state.map || typeof L === 'undefined' || !L.geoJSON) return;
        if (!state.countyGeo || !(state.countyGeo.features || []).length) return;
        var mode = state.countyMode;
        if (!mode) return;
        var scale = buildCountyScale(views, mode);
        var isGroupMode = (mode === 'speed' || mode === 'traffic');
        var byIso = {};
        views.forEach(function (v) { byIso[v.iso] = v; });

        if (state.countyLayer) {
            state.map.removeLayer(state.countyLayer);
            state.countyLayer = null;
        }
        state.countyLayers = {};
        state.countyLayer = L.geoJSON(state.countyGeo, {
            style: function (feature) {
                var view = byIso[feature.properties.iso];
                var level = view ? view.level : 'unknown';
                var hasData = Boolean(view && countyMetric(view, mode));
                return {
                    fillColor: view ? countyFill(view, mode, scale) : COUNTY_NO_DATA,
                    // 沒有資料的縣市壓低不透明度並改成虛線外框：色階最暗的那一級
                    // 是深藍灰，和灰色太像，不這樣做會讓「最慢」看起來像「沒資料」
                    fillOpacity: hasData ? 0.75 : 0.22,
                    dashArray: hasData ? null : '3,3',
                    // 外框永遠編碼「有沒有異常」（IODA 是真正的逐縣市訊號），
                    // 和填色的指標互不干擾。Radar 模式下沒有異常的縣市界再淡一級：
                    // 同一個 Radar 分區的縣市填的是同一個數值，界線畫太重會讓人
                    // 以為每個縣市各有量測。
                    color: level === 'normal' || level === 'unknown'
                        ? (isGroupMode ? 'rgba(138,164,200,0.22)' : 'rgba(138,164,200,0.45)')
                        : REGION_COLORS[level],
                    weight: level === 'alert' ? 2.4 : (level === 'watch' ? 1.8
                        : (isGroupMode ? 0.4 : 0.7))
                };
            },
            onEachFeature: function (feature, layer) {
                var view = byIso[feature.properties.iso];
                if (!view) return;
                var metric = countyMetric(view, mode);
                var groupLabel = countyGroupLabel(view);
                layer.bindTooltip(escapeHtml(countyName(view)) +
                    (groupLabel && (mode === 'speed' || mode === 'traffic')
                        ? ' · ' + escapeHtml(groupLabel) : '') +
                    (metric ? ' · ' + escapeHtml(metric.display) +
                        (metric.unit ? ' ' + escapeHtml(metric.unit) : '') : ''),
                    { sticky: true, className: 'region-map-label' });
                layer.bindPopup(countyPopupHtml(view, mode), { maxWidth: 320 });
                layer.on('popupopen', function () { bindCountyChartButtons(); });
                state.countyLayers[view.iso] = layer;
            }
        }).addTo(state.map);
        renderCountyLegend(views, scale);
    }

    function bindCountyChartButtons() {
        if (!document.querySelectorAll) return;
        document.querySelectorAll('[data-county-iso]').forEach(function (btn) {
            if (btn.dataset.bound) return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', function () {
                state.activeCounty = btn.dataset.countyIso;
                var panel = el('trendPanel');
                if (panel) panel.open = true;
                renderTabs();
                renderChart();
            });
        });
    }

    function renderConnectivityMap(views) {
        var container = el('connectivityMap');
        if (!container) return;
        if (typeof L === 'undefined') {
            container.innerHTML = '<div class="empty-state">' +
                t('互動地圖暫時無法載入', 'Interactive map unavailable') + '</div>';
            return;
        }

        var firstRender = !state.map;
        if (firstRender) {
            state.map = L.map(container, {
                zoomControl: true,
                attributionControl: true,
                scrollWheelZoom: false,
                // 預設的整數級距會讓 fitBounds 一路退到下一個整數 zoom，
                // 22 個縣市因此只占畫面中間一小塊。0.25 級距貼合得多。
                zoomSnap: 0.25
            }).setView([24.25, 120.05], 6);
            // CARTO 免費底圖已改為需 API key（無 key 會打上浮水印），改用免金鑰的 Esri 暗色底圖
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
                maxNativeZoom: 16, maxZoom: 18, opacity: 0.9,
                attribution: 'Tiles &copy; Esri'
            }).addTo(state.map);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
                maxNativeZoom: 16, maxZoom: 18, opacity: 0.9
            }).addTo(state.map);
            state.markerLayer = L.layerGroup().addTo(state.map);
        } else {
            state.markerLayer.clearLayers();
        }

        // 縣市色塊先加，點標記後加：Leaflet 同一個 pane 內按加入順序疊放，
        // 四個觀測點標記必須留在色塊之上才點得到。
        renderCountyLayer(countyViews());

        state.regionMarkers = {};
        var points = [];
        views.forEach(function (view) {
            var latlng = markerLatLng(view);
            var marker = L.circleMarker(latlng, {
                radius: view.id === 'taiwan' ? 11 : 9,
                color: '#0a0f1c',
                weight: 2,
                fillColor: REGION_COLORS[view.level],
                fillOpacity: 0.95
            });
            marker.bindTooltip(escapeHtml(regionName(view)), {
                permanent: true,
                direction: 'top',
                offset: [0, -9],
                className: 'region-map-label'
            });
            marker.bindPopup(regionPopupHtml(view), { maxWidth: 310 });
            marker.addTo(state.markerLayer);
            state.regionMarkers[view.id] = marker;
            points.push(latlng);
        });

        if (firstRender) {
            // 固定框到台灣本島＋離島，而不是四個觀測點的外接矩形：縣市色塊才是
            // 地圖的主體，照點位框會把本島擠到畫面邊緣。高雄的東沙／南沙也刻意
            // 排除在外——為了那兩個小點把視野拉到南海，其餘 21 縣市就看不清了。
            state.map.fitBounds(L.latLngBounds([[21.85, 118.05], [26.45, 122.15]]),
                { padding: [12, 12] });
        }
        setTimeout(function () {
            if (state.map) state.map.invalidateSize();
        }, 0);
    }

    // 「台灣本島」是全國級訊號，不是某個地點。畫了縣市色塊之後把它擺在本島正中
    // 央就會蓋住南投、彰化兩個縣市，所以改放到東部海面——訊號涵蓋範圍沒變，
    // 只是不再擋住它本來要說明的東西。
    var NATIONAL_MARKER_SEA = [23.35, 121.95];
    function markerLatLng(view) {
        if (view.id === 'taiwan' && state.countyLayer) return NATIONAL_MARKER_SEA;
        return [view.lat, view.lon];
    }

    function focusRegion(regionId) {
        var view = regionViews().find(function (item) { return item.id === regionId; });
        var marker = state.regionMarkers[regionId];
        if (!view || !marker || !state.map) return;
        state.map.flyTo(markerLatLng(view), Math.max(state.map.getZoom(), 8), { duration: 0.6 });
        marker.openPopup();
    }

    function renderCurrentStatus() {
        var current = currentEvents();
        var hero = el('currentStatus');
        hero.classList.toggle('is-alert', current.length > 0);
        el('statusConclusion').textContent = current.length
            ? t(current.length + ' 個監測訊號仍異常', current.length + ' monitored signal(s) remain abnormal')
            : t('目前未見持續性異常', 'No sustained anomaly detected now');
        el('statusMeta').textContent = t('依最新可用資料判定 · 更新 ', 'Based on latest available data · updated ') +
            (state.data.generated_at ? new Date(state.data.generated_at).toLocaleString() : '--');
    }

    function renderIslands() {
        var wrap = el('islandStatus');
        var islands = regionViews().filter(function (view) { return view.id !== 'taiwan'; });
        wrap.innerHTML = islands.map(function (view) {
            return '<button type="button" class="island-status is-' + view.level + '" data-region-id="' + view.id +
                '" aria-label="' + escapeHtml(regionName(view) + '：' + regionStatus(view)) + '"><strong>' +
                escapeHtml(regionName(view)) + '</strong><span class="state">' + escapeHtml(regionStatus(view)) +
                '</span><div class="status-meta">' + t('異常 ', 'Anomalies ') + view.anomaly_count +
                t(' 個／最近 28 天 · ', ' / last 28 days · ') + t('訊號 ', 'signals ') + view.signal_count +
                '</div><div class="status-meta">' + t('更新：', 'Updated: ') +
                (view.updated_at ? escapeHtml(fmtTime(view.updated_at)) : '—') + '</div></button>';
        }).join('');
        wrap.querySelectorAll('[data-region-id]').forEach(function (button) {
            button.addEventListener('click', function () { focusRegion(button.dataset.regionId); });
        });
    }

    function renderCandidates() {
        var rows = [];
        (state.data.series || []).forEach(function (series) {
            (series.anomalies || []).forEach(function (event) {
                (event.correlated_vessels || []).forEach(function (vessel) {
                    if (vessel.type !== 'fishing') rows.push(vessel);
                });
            });
        });
        el('candidateList').innerHTML = rows.length
            ? '<div class="table-scroll"><table class="region-table"><thead><tr><th>' + t('船型', 'Type') + '</th><th>' +
              t('船名', 'Name') + '</th><th>MMSI</th><th>' + t('航速', 'Speed') + '</th><th>' + t('距海纜', 'To cable') +
              '</th></tr></thead><tbody>' + rows.map(function (v) { return '<tr><td>' + (v.type || '—') + '</td><td>' + (v.name || '—') + '</td><td class="mono">' + v.mmsi + '</td><td class="mono">' + v.speed + ' kn</td><td class="mono">' + v.nearest_cable_km + ' km</td></tr>'; }).join('') + '</tbody></table></div>'
            : '<div class="empty-state">' + t('目前沒有商船／公務船候選', 'No commercial/government candidate at present') + '</div>';
    }

    function renderTabs() {
        var wrap = el('seriesTabs');
        var countyView = activeCountyView();
        var tabs = state.data.series.map(function (s) {
            var n = (s.anomalies || []).length;
            return '<button class="series-tab' +
                (!countyView && s.id === state.activeId ? ' active' : '') +
                '" data-id="' + s.id + '">' + s.label +
                (n ? ' <span class="tab-badge">' + n + '</span>' : '') + '</button>';
        });
        if (countyView) {
            // 縣市分頁只在點選後出現，點其他分頁就回到全國序列
            tabs.unshift('<button class="series-tab active" data-id="">' +
                escapeHtml(countyName(countyView)) + '</button>');
        }
        wrap.innerHTML = tabs.join('');
        wrap.querySelectorAll('.series-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (btn.dataset.id) {
                    state.activeCounty = null;
                    state.activeId = btn.dataset.id;
                }
                renderTabs();
                renderChart();
                renderAnomalies();
            });
        });
    }

    function activeSeries() {
        var county = activeCountyView();
        if (county) {
            var series = countySeries(county);
            if (series) return series;
            state.activeCounty = null;   // 該縣市沒有序列可畫，回到全國序列
        }
        var list = state.data.series || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].id === state.activeId) return list[i];
        }
        return list[0] || null;
    }

    // 事件時間 → 序列索引。縣市序列是降採樣過的（3 小時一格），逐時的 onset
    // 不會剛好落在格點上，所以完全相符找不到時取範圍內最近的一格。
    // 落在序列範圍之外（比保留天數更早）一律回 -1 —— 夾到 0 會在最左邊畫出
    // 一條根本不存在的異常帶。
    function seriesIndexForTime(timestamps, iso) {
        if (!iso || !timestamps || !timestamps.length) return -1;
        var exact = timestamps.indexOf(iso);
        if (exact >= 0) return exact;
        var target = new Date(iso).getTime();
        if (!isFinite(target)) return -1;
        var first = new Date(timestamps[0]).getTime();
        var last = new Date(timestamps[timestamps.length - 1]).getTime();
        if (!isFinite(first) || !isFinite(last) || target < first || target > last) return -1;
        var best = -1, bestDelta = Infinity;
        for (var i = 0; i < timestamps.length; i++) {
            var delta = Math.abs(new Date(timestamps[i]).getTime() - target);
            if (delta < bestDelta) { bestDelta = delta; best = i; }
        }
        return best;
    }

    // 把異常區間畫成底色帶：一眼看出「哪一段被判為異常」
    var anomalyBands = {
        id: 'anomalyBands',
        beforeDatasetsDraw: function (chart, args, opts) {
            var bands = (opts && opts.bands) || [];
            if (!bands.length) return;
            var ctx = chart.ctx, area = chart.chartArea, x = chart.scales.x;
            if (!area) return;
            ctx.save();
            bands.forEach(function (b) {
                var x1 = x.getPixelForValue(b.from);
                var x2 = x.getPixelForValue(b.to);
                ctx.fillStyle = b.color + '33';
                ctx.fillRect(x1, area.top, Math.max(2, x2 - x1), area.bottom - area.top);
                ctx.fillStyle = b.color;
                ctx.fillRect(x1, area.top, 1.5, area.bottom - area.top);
            });
            ctx.restore();
        }
    };

    function renderChart() {
        var s = activeSeries();
        var canvas = el('trafficChart');
        if (!s || typeof Chart === 'undefined' || !canvas) return;

        var labels = s.timestamps.map(fmtTime);
        // 輸出檔只保留最近 N 天的原始陣列（見 trim_series_for_output），
        // 比圖表範圍更早的異常事件仍列在下方卡片，但沒有對應的 x 座標可畫，
        // 這裡直接略過 —— 夾到 0 會在最左邊畫出一條假的異常帶
        var bands = [];
        (s.anomalies || []).forEach(function (e) {
            var from = seriesIndexForTime(s.timestamps, e.onset);
            if (from < 0) return;
            var to = seriesIndexForTime(s.timestamps, e.end);
            bands.push({
                from: from,
                to: to < 0 ? from : to,
                color: (SEVERITY[e.severity] || SEVERITY.medium).color
            });
        });

        if (state.chart) state.chart.destroy();
        state.chart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: t('觀測值', 'Observed'),
                        data: s.values,
                        borderColor: '#00f5ff',
                        backgroundColor: 'rgba(0,245,255,0.08)',
                        borderWidth: 1.6,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.25
                    },
                    {
                        label: t('季節基線（同星期幾＋同小時中位數）', 'Baseline (same weekday+hour median)'),
                        data: s.baseline,
                        borderColor: '#8aa4c8',
                        borderWidth: 1.2,
                        borderDash: [5, 4],
                        pointRadius: 0,
                        fill: false,
                        tension: 0.25
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        labels: { color: '#8aa4c8', boxWidth: 12, font: { size: 11 } }
                    },
                    anomalyBands: { bands: bands },
                    tooltip: {
                        callbacks: {
                            afterBody: function (items) {
                                var i = items[0].dataIndex;
                                var b = s.baseline[i], v = s.values[i];
                                if (b == null || v == null || !b) return '';
                                var dev = (v - b) / b * 100;
                                return t('相對基線: ', 'vs baseline: ') +
                                    (dev >= 0 ? '+' : '') + dev.toFixed(1) + '%';
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            color: '#8aa4c8', maxTicksLimit: 10, font: { size: 10 },
                            maxRotation: 0, autoSkip: true
                        },
                        grid: { color: 'rgba(138,164,200,0.08)' }
                    },
                    y: {
                        ticks: { color: '#8aa4c8', font: { size: 10 } },
                        grid: { color: 'rgba(138,164,200,0.08)' }
                    }
                }
            },
            plugins: [anomalyBands]
        });

        var caption = s.direction === 'spike'
            ? t('此序列偵測「上衝」（延遲變高＝品質變差）', 'This series detects spikes (higher latency = worse)')
            : t('此序列偵測「下掉」（流量減少）', 'This series detects drops (traffic loss)');
        if (String(s.id || '').indexOf('county:') === 0) {
            caption += t('　縣市序列為 3 小時一格的降採樣，偵測仍以逐時資料進行。',
                '  County series are downsampled to 3-hour buckets; detection still runs on hourly data.');
        }
        el('chartCaption').textContent = caption;
    }

    var TYPE_LABELS = {
        cargo: ['貨輪', 'Cargo'], tanker: ['油輪', 'Tanker'], lng: ['LNG', 'LNG'],
        coastguard: ['海警', 'Coast Guard'], msa: ['海巡', 'MSA'],
        rescue: ['海救', 'Rescue'], research: ['科研', 'Research'],
        fishing: ['漁船', 'Fishing'], other: ['其他', 'Other'], unknown: ['不明', 'Unknown']
    };
    function typeLabel(ty) {
        var pair = TYPE_LABELS[ty] || TYPE_LABELS.unknown;
        return zh() ? pair[0] : pair[1];
    }

    function vesselRow(v) {
        var hot = (v.type === 'cargo' || v.type === 'tanker' || v.type === 'lng');
        return '<tr>' +
            '<td style="color:' + (hot ? 'var(--accent-orange)' : 'var(--text-secondary)') + '">' +
            typeLabel(v.type) + '</td>' +
            '<td>' + (v.name || '—') + '</td>' +
            '<td class="mono">' + v.mmsi + '</td>' +
            '<td class="mono">' + v.speed + ' kn</td>' +
            '<td class="mono">' + v.nearest_cable_km + ' km</td>' +
            '<td class="mono">' + v.lat.toFixed(2) + ', ' + v.lon.toFixed(2) + '</td>' +
            '</tr>';
    }

    function anomalyCard(e, seriesLabel) {
        var sev = SEVERITY[e.severity] || SEVERITY.medium;
        var cs = e.candidate_summary || {};
        var leads = (cs.commercial || 0) + (cs.gov || 0);
        var vessels = e.correlated_vessels || [];

        var leadNote;
        if (e.correlation_coverage === 'outside_ais_window') {
            // 「比不到」不等於「沒有」—— AIS 軌跡只留 14 天，Radar 視窗是 28 天
            leadNote = '<span style="color:var(--text-secondary)">⏳ ' +
                t('此事件早於 AIS 軌跡保留範圍（14 天），無法比對船隻',
                  'Predates the 14-day AIS track retention — vessel correlation not possible') +
                '</span>';
        } else if (leads) {
            leadNote = '<span style="color:var(--accent-orange)">⚑ ' +
                t(leads + ' 艘商船／公務船在異常前於海纜旁滯留',
                  leads + ' commercial/gov vessel(s) loitering near a cable beforehand') + '</span>';
        } else {
            leadNote = '<span style="color:var(--text-secondary)">' +
                t('無商船／公務船候選（' + (cs.other || 0) + ' 艘漁船等，屬常態背景）',
                  'No commercial/gov candidate (' + (cs.other || 0) + ' fishing etc., normal background)') +
                '</span>';
        }

        return '<div class="anomaly-card" style="border-left-color:' + sev.color + '">' +
            '<div class="anomaly-head">' +
                '<span class="sev-badge" style="background:' + sev.color + '">' +
                    t(sev.zh, sev.en) + '</span>' +
                '<span class="anomaly-series">' + seriesLabel + '</span>' +
            '</div>' +
            '<div class="anomaly-metrics">' +
                '<div><span class="m-label">' + t('開始', 'Onset') + '</span>' +
                    '<span class="m-value mono">' + fmtTime(e.onset) + '</span></div>' +
                '<div><span class="m-label">' + t('持續', 'Duration') + '</span>' +
                    '<span class="m-value mono">' + e.duration_hours + ' h</span></div>' +
                '<div><span class="m-label">' + t('相對基線', 'vs baseline') + '</span>' +
                    '<span class="m-value mono" style="color:' + sev.color + '">' +
                    (e.max_deviation_pct > 0 ? '+' : '') + e.max_deviation_pct + '%</span></div>' +
                '<div><span class="m-label">' + t('穩健 z', 'Robust z') + '</span>' +
                    '<span class="m-value mono">' + e.peak_z + '</span></div>' +
            '</div>' +
            '<div class="anomaly-lead">' + leadNote + '</div>' +
            '</div>';
    }

    function anomalyTimelineData(seriesList) {
        var events = [];
        (seriesList || []).forEach(function (series, seriesIndex) {
            (series.anomalies || []).forEach(function (event, eventIndex) {
                events.push({
                    id: 'anomaly-' + seriesIndex + '-' + eventIndex,
                    label: series.label || series.id || t('未命名序列', 'Unnamed series'),
                    onset: event.onset,
                    end: event.end,
                    duration_hours: event.duration_hours,
                    severity: event.severity || 'medium',
                    max_deviation_pct: event.max_deviation_pct,
                    peak_z: event.peak_z,
                    candidate_summary: event.candidate_summary || {},
                    correlation_coverage: event.correlation_coverage,
                    correlated_vessels: event.correlated_vessels || []
                });
            });
        });
        return events;
    }

    function timelineVesselSummary(event) {
        var cs = event.candidate_summary || {};
        if (event.correlation_coverage === 'outside_ais_window') {
            return t('早於 AIS 14 天保留範圍，無法比對船隻',
                'Outside the 14-day AIS retention window; vessel correlation unavailable');
        }
        var leads = Number(cs.commercial || 0) + Number(cs.gov || 0);
        var total = Number(cs.total || 0);
        var named = (event.correlated_vessels || []).slice(0, 3).map(function (v) {
            return v.name || v.mmsi || typeLabel(v.type);
        });
        var summary = leads
            ? t(leads + ' 艘商船／公務船候選', leads + ' commercial/government candidate(s)')
            : t('無商船／公務船候選', 'No commercial/government candidates');
        summary += t('；共 ' + total + ' 艘關聯船隻', '; ' + total + ' correlated vessel(s)');
        if (named.length) summary += ' — ' + named.join(', ');
        return summary;
    }

    function renderAnomalies() {
        var wrap = el('anomalyList');
        var all = anomalyTimelineData(state.data.series);
        all.sort(function (a, b) { return (b.onset || '').localeCompare(a.onset || ''); });

        if (!all.length) {
            wrap.innerHTML = '<div class="empty-state">' +
                t('監測期間未偵測到流量異常 — 這是好消息。',
                  'No traffic anomalies detected in the window — that is good news.') + '</div>';
            return;
        }

        var onsetTimes = all.map(function (event) { return new Date(event.onset).getTime(); })
            .filter(function (time) { return isFinite(time); });
        var endTimes = all.map(function (event) { return new Date(event.end || event.onset).getTime(); })
            .filter(function (time) { return isFinite(time); });
        var generatedTime = new Date(state.data.generated_at).getTime();
        var rangeStart = Math.min.apply(null, onsetTimes);
        var lastEventEnd = Math.max.apply(null, endTimes);
        var rangeEnd = isFinite(generatedTime) ? generatedTime : lastEventEnd;
        rangeEnd = Math.max(rangeStart, rangeEnd);
        if (rangeEnd === rangeStart) rangeEnd += 3600 * 1000;
        var range = rangeEnd - rangeStart;
        var recentCutoff = rangeEnd - 2 * 3600 * 1000;
        var tickCount = 6;
        var ticks = [];
        for (var i = 0; i < tickCount; i++) {
            var tickTime = rangeStart + range * i / (tickCount - 1);
            ticks.push('<span class="timeline-tick" style="left:' + (i / (tickCount - 1) * 100) + '%">' +
                escapeHtml(fmtTime(new Date(tickTime).toISOString())) + '</span>');
        }

        var rows = all.map(function (event) {
            var onset = new Date(event.onset).getTime();
            var suppliedEnd = event.end ? new Date(event.end).getTime() : NaN;
            var effectiveEnd = isFinite(suppliedEnd) ? suppliedEnd : rangeEnd;
            var ongoing = !event.end || effectiveEnd >= recentCutoff;
            var left = Math.max(0, Math.min(100, (onset - rangeStart) / range * 100));
            var width = Math.max(0.8, Math.min(100 - left, (effectiveEnd - onset) / range * 100));
            var sev = SEVERITY[event.severity] || SEVERITY.medium;
            var duration = event.duration_hours != null ? event.duration_hours :
                Math.max(0, Math.round((effectiveEnd - onset) / 360000) / 10);
            var endText = event.end ? fmtTime(event.end) : t('尚未結束', 'Not ended');
            var deviation = event.max_deviation_pct == null ? '--' :
                (event.max_deviation_pct > 0 ? '+' : '') + event.max_deviation_pct + '%';
            var zScore = event.peak_z == null ? '--' : event.peak_z;
            var vesselSummary = timelineVesselSummary(event);
            var fullDescription = t('事件：', 'Event: ') + event.label + '。' +
                t('嚴重度：', 'Severity: ') + t(sev.zh, sev.en) + '。' +
                t('開始：', 'Onset: ') + fmtAccessibleTime(event.onset) + '。' +
                t('結束：', 'End: ') + (event.end ? fmtAccessibleTime(event.end) : endText) + '。' +
                t('持續：', 'Duration: ') + duration + t(' 小時。', ' hours. ') +
                t('相對基線：', 'Versus baseline: ') + deviation + '。' +
                t('穩健 z：', 'Robust z: ') + zScore + '。' + vesselSummary;

            return '<div class="timeline-event" id="' + event.id + '" tabindex="0" role="listitem" aria-label="' +
                escapeHtml(fullDescription) + '" aria-describedby="' + event.id + '-details">' +
                '<div class="timeline-event-label"><strong>' + escapeHtml(event.label) + '</strong>' +
                    '<span class="sev-badge" style="background:' + sev.color + '">' +
                    escapeHtml(t(sev.zh, sev.en)) + '</span>' +
                    (ongoing ? '<span class="ongoing-badge">● ' + escapeHtml(t('進行中', 'Ongoing')) + '</span>' : '') +
                '</div>' +
                '<div class="timeline-track" aria-hidden="true"><span class="timeline-bar" style="left:' +
                    left + '%;width:' + width + '%;background:' + sev.color + '">' +
                    '<span>' + escapeHtml(duration + ' h') + '</span></span></div>' +
                '<div class="timeline-tooltip" id="' + event.id + '-details" role="tooltip"><strong>' + escapeHtml(event.label) + '</strong>' +
                    '<dl><div><dt>' + escapeHtml(t('開始', 'Onset')) + '</dt><dd>' + escapeHtml(fmtTime(event.onset)) + '</dd></div>' +
                    '<div><dt>' + escapeHtml(t('結束', 'End')) + '</dt><dd>' + escapeHtml(endText) + '</dd></div>' +
                    '<div><dt>' + escapeHtml(t('持續時間', 'Duration')) + '</dt><dd>' + escapeHtml(duration + ' h') + '</dd></div>' +
                    '<div><dt>' + escapeHtml(t('相對基線', 'vs baseline')) + '</dt><dd>' + escapeHtml(deviation) + '</dd></div>' +
                    '<div><dt>' + escapeHtml(t('穩健 z', 'Robust z')) + '</dt><dd>' + escapeHtml(zScore) + '</dd></div></dl>' +
                    '<p>⚑ ' + escapeHtml(vesselSummary) + '</p></div></div>';
        }).join('');

        wrap.innerHTML = '<div class="anomaly-timeline" role="region" aria-label="' +
            escapeHtml(t('網路異常事件時間軸', 'Network anomaly event timeline')) + '">' +
            '<div class="timeline-header"><div class="timeline-label-heading">' +
                escapeHtml(t('事件／嚴重度', 'Event / severity')) + '</div><div class="timeline-axis">' +
                ticks.join('') + '</div></div><div class="timeline-scroll" role="list">' + rows +
            '</div></div>';
    }

    function renderOutages() {
        var panel = el('outagePanel');
        var list = state.data.outage_annotations || [];
        if (!list.length) { panel.style.display = 'none'; return; }
        panel.style.display = '';
        el('outageBody').innerHTML = list.map(function (o) {
            return '<tr>' +
                '<td class="mono">' + fmtTime(o.start) + '</td>' +
                '<td class="mono">' + (o.end ? fmtTime(o.end) : '—') + '</td>' +
                '<td>' + (o.scope || '—') + '</td>' +
                '<td>' + (o.cause || o.outage_type || '—') + '</td>' +
                '</tr>';
        }).join('');
    }

    function renderAll() {
        if (!state.data) return;
        renderBanner();
        renderCurrentStatus();
        renderCountyModes(countyViews());
        renderConnectivityMap(regionViews());
        renderIslands();
        renderStats();
        renderTabs();
        if (el('trendPanel').open) renderChart();
        renderAnomalies();
        renderCandidates();
        renderOutages();
        el('updateInfo').textContent = t('資料更新時間: ', 'Updated: ') +
            (state.data.generated_at ? new Date(state.data.generated_at).toLocaleString() : '--');
        el('dataStatus').textContent = state.isDemo ? t('示範資料', 'Demo data') :
            (Object.keys(state.sourceErrors).length ? '⚠' : '✅');
    }

    async function load() {
        state.sourceErrors = {};
        try {
            var res = await fetch('cf_radar.json?' + Date.now());
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var data = await res.json();
            if (!data.series || !data.series.length) throw new Error('empty series');
            // data.json 內嵌版本會剝掉原始陣列；這裡拿到的應是完整檔
            if (!data.series[0].timestamps) throw new Error('series without timestamps');
            state.data = data;
            state.isDemo = false;
        } catch (err) {
            console.warn('cf_radar.json unavailable, showing demo data:', err);
            state.data = recomputeSummary(buildDemo());
            state.isDemo = true;
            state.sourceErrors.cloudflare = true;
        }
        try {
            var islandRes = await fetch('ioda.json?' + Date.now());
            if (!islandRes.ok) throw new Error('HTTP ' + islandRes.status);
            state.islandData = await islandRes.json();
        } catch (islandErr) {
            console.warn('ioda.json unavailable:', islandErr);
            state.islandData = { islands: [], generated_at: null };
            state.sourceErrors.ioda = true;
        }
        // 縣市界圖是靜態資產（src/build_tw_counties.py 產生並提交），
        // 抓不到就只是沒有色塊，其餘畫面照常。
        try {
            var geoRes = await fetch('tw_counties.geojson');
            if (!geoRes.ok) throw new Error('HTTP ' + geoRes.status);
            state.countyGeo = await geoRes.json();
        } catch (geoErr) {
            console.warn('tw_counties.geojson unavailable:', geoErr);
            state.countyGeo = null;
            state.sourceErrors.counties = true;
        }
        try {
            var countyRes = await fetch('cf_radar_counties.json?' + Date.now());
            if (!countyRes.ok) throw new Error('HTTP ' + countyRes.status);
            state.countyData = await countyRes.json();
            state.countyDemo = false;
        } catch (countyErr) {
            // Cloudflare 的縣市指標是選配（需要 token，且不保證每個縣市都有樣本）。
            // 沒有它時 IODA 的縣市可達性仍然能把地圖填滿；兩個都沒有才用示範資料。
            console.warn('cf_radar_counties.json unavailable:', countyErr);
            state.countyData = null;
            if (state.isDemo && !((state.islandData || {}).counties || []).length) {
                state.countyData = buildDemoCounties();
                state.countyDemo = Boolean(state.countyData);
            }
        }
        state.activeId = state.data.series[0].id;
        renderAll();
    }

    function setupMapSizeToggle() {
        var button = el('mapSizeToggle');
        if (!button) return;
        button.addEventListener('click', function () {
            var panel = button.closest('.dashboard-map');
            var expanded = panel.classList.toggle('is-expanded');
            button.setAttribute('aria-expanded', String(expanded));
            button.innerHTML = expanded
                ? '<span class="lang-zh-only">收合地圖</span><span class="lang-en-only">Collapse map</span>'
                : '<span class="lang-zh-only">展開地圖</span><span class="lang-en-only">Expand map</span>';
            setTimeout(function () { if (state.map) state.map.invalidateSize(); }, 220);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        setupMapSizeToggle();
        load();
    });
    document.addEventListener('toggle', function (event) {
        if (event.target.matches('details.tech-details') && event.target.open && event.target.querySelector('#trafficChart')) renderChart();
    }, true);
    window.addEventListener('langchange', function () {
        // 示範資料的標籤是中英文字串，語言切換時要重建
        if (state.isDemo) {
            state.data = recomputeSummary(buildDemo());
            state.activeId = state.data.series[0].id;
        }
        if (state.countyDemo) state.countyData = buildDemoCounties();
        renderAll();
    });
})();

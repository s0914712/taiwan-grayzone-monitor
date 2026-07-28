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

    var state = { data: null, isDemo: false, activeId: null, chart: null };

    function zh() {
        return (typeof i18n !== 'undefined' ? i18n.getLang() : 'zh') !== 'en';
    }
    function t(zhText, enText) { return zh() ? zhText : enText; }
    function el(id) { return document.getElementById(id); }

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

    function renderBanner() {
        var banner = el('demoBanner');
        banner.style.display = state.isDemo ? 'flex' : 'none';
    }

    function renderStats() {
        var s = state.data.summary || {};
        var sev = s.by_severity || {};
        el('statSeries').textContent = s.series_analyzed || 0;
        el('statAnomalies').textContent = s.anomaly_count || 0;
        var worst = sev.critical ? 'critical' : (sev.high ? 'high' : (sev.medium ? 'medium' : null));
        var worstEl = el('statWorst');
        if (worst) {
            worstEl.textContent = t(SEVERITY[worst].zh, SEVERITY[worst].en);
            worstEl.style.color = SEVERITY[worst].color;
        } else {
            worstEl.textContent = t('無', 'None');
            worstEl.style.color = 'var(--accent-green)';
        }
        el('statLeads').textContent = s.anomalies_with_commercial_or_gov_candidates || 0;
    }

    function renderTabs() {
        var wrap = el('seriesTabs');
        wrap.innerHTML = state.data.series.map(function (s) {
            var n = (s.anomalies || []).length;
            return '<button class="series-tab' + (s.id === state.activeId ? ' active' : '') +
                '" data-id="' + s.id + '">' + s.label +
                (n ? ' <span class="tab-badge">' + n + '</span>' : '') + '</button>';
        }).join('');
        wrap.querySelectorAll('.series-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                state.activeId = btn.dataset.id;
                renderTabs();
                renderChart();
                renderAnomalies();
            });
        });
    }

    function activeSeries() {
        var list = state.data.series || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].id === state.activeId) return list[i];
        }
        return list[0] || null;
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
        var bands = (s.anomalies || []).map(function (e) {
            var from = s.timestamps.indexOf(e.onset);
            var to = s.timestamps.indexOf(e.end);
            return {
                from: from < 0 ? 0 : from,
                to: to < 0 ? (from < 0 ? 0 : from) : to,
                color: (SEVERITY[e.severity] || SEVERITY.medium).color
            };
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

        el('chartCaption').textContent = s.direction === 'spike'
            ? t('此序列偵測「上衝」（延遲變高＝品質變差）', 'This series detects spikes (higher latency = worse)')
            : t('此序列偵測「下掉」（流量減少）', 'This series detects drops (traffic loss)');
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

        var leadNote = leads
            ? '<span style="color:var(--accent-orange)">⚑ ' +
              t(leads + ' 艘商船／公務船在異常前於海纜旁滯留',
                leads + ' commercial/gov vessel(s) loitering near a cable beforehand') + '</span>'
            : '<span style="color:var(--text-secondary)">' +
              t('無商船／公務船候選（' + (cs.other || 0) + ' 艘漁船等，屬常態背景）',
                'No commercial/gov candidate (' + (cs.other || 0) + ' fishing etc., normal background)') +
              '</span>';

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
            (vessels.length
                ? '<div class="table-scroll"><table class="region-table"><thead><tr>' +
                    '<th>' + t('船型', 'Type') + '</th>' +
                    '<th>' + t('船名', 'Name') + '</th>' +
                    '<th>MMSI</th>' +
                    '<th>' + t('航速', 'Speed') + '</th>' +
                    '<th>' + t('距海纜', 'To cable') + '</th>' +
                    '<th>' + t('位置', 'Position') + '</th>' +
                    '</tr></thead><tbody>' + vessels.map(vesselRow).join('') +
                    '</tbody></table></div>'
                : '') +
            '</div>';
    }

    function renderAnomalies() {
        var wrap = el('anomalyList');
        var all = [];
        state.data.series.forEach(function (s) {
            (s.anomalies || []).forEach(function (e) { all.push({ e: e, label: s.label }); });
        });
        all.sort(function (a, b) { return (b.e.onset || '').localeCompare(a.e.onset || ''); });

        if (!all.length) {
            wrap.innerHTML = '<div class="empty-state">' +
                t('監測期間未偵測到流量異常 — 這是好消息。',
                  'No traffic anomalies detected in the window — that is good news.') + '</div>';
            return;
        }
        wrap.innerHTML = all.map(function (a) { return anomalyCard(a.e, a.label); }).join('');
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
        renderStats();
        renderTabs();
        renderChart();
        renderAnomalies();
        renderOutages();
        el('updateInfo').textContent = t('資料更新時間: ', 'Updated: ') +
            (state.data.generated_at ? new Date(state.data.generated_at).toLocaleString() : '--');
        el('dataStatus').textContent = state.isDemo ? t('示範資料', 'Demo data') : '✅';
    }

    async function load() {
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
        }
        state.activeId = state.data.series[0].id;
        renderAll();
    }

    document.addEventListener('DOMContentLoaded', load);
    window.addEventListener('langchange', function () {
        // 示範資料的標籤是中英文字串，語言切換時要重建
        if (state.isDemo) {
            state.data = recomputeSummary(buildDemo());
            state.activeId = state.data.series[0].id;
        }
        renderAll();
    });
})();

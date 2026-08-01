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

    var state = { data: null, ioda: null, sourceErrors: {}, isDemo: false, activeId: null, chart: null };
    var FRESH_HOURS = 6;

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

    function hoursSince(iso) {
        var ms = Date.now() - new Date(iso).getTime();
        return isFinite(ms) ? ms / 3600000 : Infinity;
    }

    function allEvents() {
        var events = [];
        (state.data.series || []).forEach(function (s) {
            (s.anomalies || []).forEach(function (e) { events.push({ event: e, region: 'taiwan' }); });
        });
        ((state.ioda && state.ioda.islands) || []).forEach(function (island) {
            (island.series || []).forEach(function (s) {
                (s.anomalies || []).forEach(function (e) {
                    events.push({ event: e, region: island.id, sources: e.corroborating_sources || 1 });
                });
            });
        });
        return events;
    }

    function deriveCurrentStatus() {
        var generated = state.data && state.data.generated_at;
        if (!generated || hoursSince(generated) > FRESH_HOURS) {
            return { level: 'unknown', icon: '⚪', title: t('目前資料不足', 'Current data unavailable'),
                detail: t('最新主要資料已過期或載入失敗，無法判定目前連線狀態。',
                    'The primary feed is stale or failed, so current connectivity cannot be determined.'), active: [] };
        }
        var cutoff = new Date(new Date(generated).getTime() - 2 * 3600000).toISOString();
        var active = allEvents().filter(function (x) { return !x.event.end || x.event.end >= cutoff; });
        if (!active.length) {
            return { level: 'normal', icon: '🟢', title: t('目前未偵測到持續中的網路異常', 'No ongoing network anomaly detected'),
                detail: t('最後成功更新：', 'Last successful update: ') + fmtTime(generated), active: [] };
        }
        var corroborated = active.some(function (x) { return (x.sources || 1) >= 2; });
        var critical = active.some(function (x) { return x.event.severity === 'critical'; });
        var level = corroborated && critical ? 'suspected_outage' : (corroborated ? 'high' : 'watch');
        var affected = new Set(active.map(function (x) { return x.region; })).size;
        return { level: level, icon: level === 'suspected_outage' ? '🔴' : (level === 'high' ? '🟠' : '🟡'),
            title: t('目前偵測到網路異常', 'Ongoing network anomaly detected'),
            detail: t('受影響區域：', 'Affected regions: ') + affected + ' · ' +
                (corroborated ? t('已有多來源印證', 'corroborated by multiple sources') : t('尚待其他來源印證', 'awaiting independent corroboration')),
            active: active };
    }

    function renderCurrentStatus() {
        var status = deriveCurrentStatus();
        var box = el('currentStatus');
        box.dataset.level = status.level;
        el('currentStatusTitle').textContent = status.icon + ' ' + status.title;
        el('currentStatusDetail').textContent = status.detail;
        return status;
    }

    function islandStatus(island) {
        if (!island || island.status === 'unavailable' || !(island.series || []).length ||
                !state.ioda.generated_at || hoursSince(state.ioda.generated_at) > FRESH_HOURS) {
            return { level: 'unknown', icon: '⚪', label: t('資料不足', 'No data') };
        }
        var events = [];
        island.series.forEach(function (s) { (s.anomalies || []).forEach(function (e) { events.push(e); }); });
        var freshCutoff = state.ioda && state.ioda.generated_at ?
            new Date(new Date(state.ioda.generated_at).getTime() - 2 * 3600000).toISOString() : '';
        var active = events.filter(function (e) { return !e.end || e.end >= freshCutoff; });
        if (!active.length) return { level: 'normal', icon: '🟢', label: t('未見持續異常', 'No ongoing anomaly') };
        var n = Math.max.apply(null, active.map(function (e) { return e.corroborating_sources || 1; }));
        return n >= 2 ? { level: 'high', icon: '🟠', label: t('多來源異常', 'Multi-source anomaly') } :
            { level: 'watch', icon: '🟡', label: t('單一訊號異常', 'Single-signal anomaly') };
    }

    function renderRegions() {
        var islands = ((state.ioda && state.ioda.islands) || []).slice();
        ['kinmen', 'lienchiang', 'penghu'].forEach(function (id) {
            if (!islands.some(function (i) { return i.id === id; })) {
                var names = { kinmen: ['金門', 'Kinmen'], lienchiang: ['馬祖（連江）', 'Matsu'], penghu: ['澎湖', 'Penghu'] };
                islands.push({ id: id, label_zh: names[id][0], label_en: names[id][1], status: 'unavailable', series: [] });
            }
        });
        var overall = deriveCurrentStatus();
        var taiwanActive = overall.active.filter(function (x) { return x.region === 'taiwan'; });
        var taiwanView = overall.level === 'unknown' ? overall : (taiwanActive.length ?
            { level: 'watch', icon: '🟡', title: t('偵測到網路異常', 'Network anomaly detected') } :
            { level: 'normal', icon: '🟢', title: t('未見持續異常', 'No ongoing anomaly') });
        var cards = [{ id: 'taiwan', label_zh: '台灣本島', label_en: 'Taiwan', statusView: taiwanView,
            series: (state.data && state.data.series) || [], generated_at: state.data && state.data.generated_at }];
        islands.forEach(function (i) { i.statusView = islandStatus(i); i.generated_at = state.ioda && state.ioda.generated_at; cards.push(i); });
        el('islandGrid').innerHTML = cards.map(function (i) {
            var st = i.statusView;
            var series = i.series || [];
            var signalText = series.length ? series.map(function (s) {
                return (s.label_zh || s.label || s.datasource) + ' ' + ((s.anomalies || []).length ? '●' : '✓');
            }).join(' · ') : t('無可用訊號', 'No available signals');
            return '<article class="island-card" data-level="' + st.level + '">' +
                '<div class="island-name">' + (zh() ? i.label_zh : i.label_en) + '</div>' +
                '<div class="island-state">' + st.icon + ' ' + (st.label || st.title) + '</div>' +
                '<div class="signal-list">' + signalText + '</div>' +
                '<div class="island-meta">' + t('更新：', 'Updated: ') + (i.generated_at ? fmtTime(i.generated_at) : '—') + '</div></article>';
        }).join('');
    }

    function renderBanner() {
        var banner = el('demoBanner');
        banner.style.display = state.isDemo ? 'flex' : 'none';
    }

    function renderStats() {
        var s = state.data.summary || {};
        var sev = s.by_severity || {};
        el('statSeries').textContent = new Set(deriveCurrentStatus().active.map(function (x) {
            return x.region;
        })).size;
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
        // 輸出檔只保留最近 N 天的原始陣列（見 trim_series_for_output），
        // 比圖表範圍更早的異常事件仍列在下方卡片，但沒有對應的 x 座標可畫，
        // 這裡直接略過 —— 夾到 0 會在最左邊畫出一條假的異常帶
        var bands = [];
        (s.anomalies || []).forEach(function (e) {
            var from = s.timestamps.indexOf(e.onset);
            if (from < 0) return;
            var to = s.timestamps.indexOf(e.end);
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
        renderCurrentStatus();
        renderRegions();
        renderTabs();
        if (el('trendPanel').open) renderChart();
        renderAnomalies();
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
        }
        try {
            var iodaRes = await fetch('ioda.json?' + Date.now());
            if (!iodaRes.ok) throw new Error('HTTP ' + iodaRes.status);
            state.ioda = await iodaRes.json();
        } catch (iodaErr) {
            console.warn('ioda.json unavailable:', iodaErr);
            state.ioda = { islands: [], generated_at: null };
            state.sourceErrors.ioda = true;
        }
        state.activeId = state.data.series[0].id;
        renderAll();
    }

    document.addEventListener('DOMContentLoaded', load);
    document.addEventListener('DOMContentLoaded', function () {
        var panel = el('trendPanel');
        if (panel) panel.addEventListener('toggle', function () { if (panel.open) renderChart(); });
    });
    window.addEventListener('langchange', function () {
        // 示範資料的標籤是中英文字串，語言切換時要重建
        if (state.isDemo) {
            state.data = recomputeSummary(buildDemo());
            state.activeId = state.data.series[0].id;
        }
        renderAll();
    });
})();

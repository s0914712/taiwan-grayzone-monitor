/**
 * 網路流量監控頁 — Taiwan Gray Zone Monitor
 *
 * 資料來源 cf_radar.json（src/fetch_cloudflare_radar.py 產生）與 ioda.json
 * （src/fetch_ioda.py 產生）。異常事件與基線都由排程資料管線計算；本檔只呈現
 * 真實觀測。來源失敗時顯示「資料不足」，不生成或替換任何合成資料。
 */
(function () {
    'use strict';

    var SEVERITY = {
        critical: { color: '#ff3366', zh: '嚴重', en: 'Critical' },
        high: { color: '#ff6b35', zh: '高', en: 'High' },
        medium: { color: '#ffd700', zh: '中', en: 'Medium' }
    };

    var state = { data: null, ioda: null, sourceErrors: {}, activeId: null, chart: null };
    var FRESH_HOURS = 6;

    function zh() {
        return (typeof i18n !== 'undefined' ? i18n.getLang() : 'zh') !== 'en';
    }
    function t(zhText, enText) { return zh() ? zhText : enText; }
    function el(id) { return document.getElementById(id); }

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
                var count = (s.anomalies || []).length;
                return (s.label_zh || s.label || s.datasource) + (count ?
                    t('（期間內 ' + count + ' 次）', ' (' + count + ' in period)') : t('（期間內無異常）', ' (none in period)'));
            }).join(' · ') : t('無可用訊號', 'No available signals');
            var signalSummary = series.length ? t(series.length + ' 個監測訊號', series.length + ' monitoring signals') :
                t('查看缺少原因', 'See why data is missing');
            return '<article class="island-card" data-level="' + st.level + '">' +
                '<div class="island-name">' + (zh() ? i.label_zh : i.label_en) + '</div>' +
                '<div class="island-state">' + st.icon + ' ' + (st.label || st.title) + '</div>' +
                '<div class="island-meta">' + t('更新：', 'Updated: ') + (i.generated_at ? fmtTime(i.generated_at) : '—') + '</div>' +
                '<details class="signal-details"><summary>' + signalSummary + '</summary>' +
                '<div class="signal-list">' + signalText + '</div></details></article>';
        }).join('');
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

    function anomalyCard(e, seriesLabel, compact) {
        var sev = SEVERITY[e.severity] || SEVERITY.medium;
        var cs = e.candidate_summary || {};
        var leads = (cs.commercial || 0) + (cs.gov || 0);
        var vessels = e.correlated_vessels || [];
        var priorityVessels = vessels.filter(function (v) {
            return v.type === 'cargo' || v.type === 'tanker' || v.type === 'lng' ||
                v.type === 'coastguard' || v.type === 'msa' || v.type === 'research';
        });
        var backgroundVessels = vessels.filter(function (v) { return priorityVessels.indexOf(v) < 0; });

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
                (compact ? '' : '<div><span class="m-label">' + t('穩健 z', 'Robust z') + '</span>' +
                    '<span class="m-value mono">' + e.peak_z + '</span></div>') +
            '</div>' +
            '<div class="anomaly-lead">' + leadNote + '</div>' +
            (priorityVessels.length
                ? '<div class="table-scroll"><table class="region-table"><thead><tr>' +
                    '<th>' + t('船型', 'Type') + '</th>' +
                    '<th>' + t('船名', 'Name') + '</th>' +
                    '<th>MMSI</th>' +
                    '<th>' + t('航速', 'Speed') + '</th>' +
                    '<th>' + t('距海纜', 'To cable') + '</th>' +
                    '<th>' + t('位置', 'Position') + '</th>' +
                    '</tr></thead><tbody>' + priorityVessels.map(vesselRow).join('') +
                    '</tbody></table></div>'
                : '') +
            (backgroundVessels.length && !compact ? '<details class="signal-details"><summary>' +
                t('查看 ' + backgroundVessels.length + ' 艘背景船流', 'View ' + backgroundVessels.length + ' background vessels') +
                '</summary><div class="table-scroll"><table class="region-table"><tbody>' +
                backgroundVessels.map(vesselRow).join('') + '</tbody></table></div></details>' : '') +
            '</div>';
    }

    function renderAnomalies() {
        var wrap = el('anomalyList');
        var title = el('focusEventTitle');
        var all = [];
        state.data.series.forEach(function (s) {
            (s.anomalies || []).forEach(function (e) { all.push({ e: e, label: s.label }); });
        });
        all.sort(function (a, b) { return (b.e.onset || '').localeCompare(a.e.onset || ''); });

        var history = el('historyAnomalyList');
        if (!all.length) {
            title.textContent = t('目前無需處理', 'No action needed now');
            wrap.innerHTML = '<div class="empty-state">' +
                t('監測期間未偵測到流量異常 — 這是好消息。',
                  'No traffic anomalies detected in the window — that is good news.') + '</div>';
            if (history) history.innerHTML = '';
            return;
        }
        var latest = all[0];
        var current = deriveCurrentStatus().active.some(function (x) { return x.event === latest.e; });
        title.textContent = current ? t('目前需要注意', 'Needs attention now') : t('最近一次異常（已結束）', 'Latest anomaly (ended)');
        wrap.innerHTML = anomalyCard(latest.e, latest.label, true) +
            (all.length > 1 ? '<div class="status-caveat">' +
                t('其餘 ' + (all.length - 1) + ' 筆已收進「近 28 天歷史」。',
                  (all.length - 1) + ' older event(s) are available in 28-day history.') + '</div>' : '');
        if (history) history.innerHTML = all.slice(1).map(function (a) { return anomalyCard(a.e, a.label, false); }).join('');
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
        renderStats();
        renderCurrentStatus();
        renderRegions();
        renderTabs();
        if (el('historyPanel').open) renderChart();
        renderAnomalies();
        renderOutages();
        el('updateInfo').textContent = t('資料更新時間: ', 'Updated: ') +
            (state.data.generated_at ? new Date(state.data.generated_at).toLocaleString() : '--');
        el('dataStatus').textContent = Object.keys(state.sourceErrors).length ?
            t('⚠ 資料不足', '⚠ Data unavailable') : '✅';
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
        } catch (err) {
            console.warn('cf_radar.json unavailable:', err);
            state.data = { generated_at: null, series: [], outage_annotations: [], summary: {} };
            state.sourceErrors.cloudflare = true;
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
        state.activeId = state.data.series.length ? state.data.series[0].id : null;
        renderAll();
    }

    document.addEventListener('DOMContentLoaded', load);
    document.addEventListener('DOMContentLoaded', function () {
        var panel = el('historyPanel');
        if (panel) panel.addEventListener('toggle', function () { if (panel.open) renderChart(); });
    });
    window.addEventListener('langchange', renderAll);
})();

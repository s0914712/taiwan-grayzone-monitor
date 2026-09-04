/**
 * Taiwan Gray Zone Monitor — 統計分析頁
 * statistics.html：單純呈現高風險船的「徘徊熱區地圖」＋「船種／船籍分布」。
 *
 * 資料來源與 weekly-report.html 相同（aggregate_highrisk 產出的週/月報 JSON，
 * 經 reports/weekly/index.json manifest 索引）。熱區色階與格線幾何一律走
 * js/hotspot-layer.js —— 同一份資料在兩個頁面與 LINE 的 PNG 不能是不同顏色。
 *
 * 本頁只做呈現：熱區判定、分位色階的門檻、逐船篩選都是後端算好的。
 */
(function () {
    'use strict';

    var MANIFEST_URL = 'reports/weekly/index.json';
    var map = null;
    var layers = {};
    var charts = {};
    var manifest = null;
    var current = null;    // {kind, label}
    var cached = null;

    function zh() {
        return (typeof i18n !== 'undefined' && i18n.getLang)
            ? i18n.getLang() === 'zh' : true;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function $(id) { return document.getElementById(id); }

    var TYPE_LABELS = {
        fishing: ['漁船', 'Fishing'], cargo: ['貨輪', 'Cargo'],
        tanker: ['油輪', 'Tanker'], lng: ['LNG 船', 'LNG'],
        coastguard: ['海警', 'Coast Guard'], msa: ['海巡', 'MSA'],
        rescue: ['海救', 'Rescue'], research: ['科研', 'Research'],
        other: ['其他', 'Other'], unknown: ['不明', 'Unknown']
    };
    var TYPE_COLORS = {
        fishing: '#00ff88', cargo: '#00f5ff', tanker: '#ff6b35',
        lng: '#f0e130', coastguard: '#ffffff', msa: '#4d9fff',
        rescue: '#ff9500', research: '#c77dff', other: '#ff3366',
        unknown: '#888'
    };

    function typeLabel(t) {
        var e = TYPE_LABELS[t];
        // 未對映的型別顯示原始字串，否則圖上會出現兩個都叫「不明」的長條
        if (!e) return t || (zh() ? '不明' : 'Unknown');
        return zh() ? e[0] : e[1];
    }

    // ── 地圖 ──────────────────────────────────────────────
    function initMap() {
        map = L.map('map', {
            center: [24.0, 120.8], zoom: 6,
            zoomControl: true, attributionControl: true
        });
        // CARTO 免費底圖已改為需 API key（無 key 會打浮水印）→ 免金鑰的 Esri 暗色底圖
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
            maxNativeZoom: 16, maxZoom: 18, opacity: 0.9,
            attribution: 'Tiles &copy; Esri'
        }).addTo(map);
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
            maxNativeZoom: 16, maxZoom: 18, opacity: 0.9
        }).addTo(map);
        layers.cables = L.layerGroup().addTo(map);
        layers.hotspots = L.layerGroup().addTo(map);

        [['lyrHotspots', 'hotspots'], ['lyrCables', 'cables']].forEach(function (pair) {
            $(pair[0]).addEventListener('change', function (e) {
                if (e.target.checked) map.addLayer(layers[pair[1]]);
                else map.removeLayer(layers[pair[1]]);
            });
        });
        loadCables();
    }

    function loadCables() {
        fetch('taiwan_cables.json').then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).then(function (gj) {
            L.geoJSON(gj, {
                style: function (f) {
                    var p = f.properties || {};
                    return {
                        color: '#' + (p.color || '00f5ff'),
                        weight: 1.4, opacity: 0.55,
                        dashArray: p.status === '規劃中' ? '4 4' : null
                    };
                },
                onEachFeature: function (f, l) {
                    var p = f.properties || {};
                    if (p.name) l.bindTooltip(esc(p.name), { sticky: true });
                }
            }).addTo(layers.cables);
        }).catch(function () { /* 海纜圖層缺檔不擋主功能 */ });
    }

    function plotHotspots(cells) {
        HotspotLayer.plotCells(L, layers.hotspots, cells, {
            popup: function (c) {
                return '<b>' + (zh() ? '低速徘徊熱區' : 'Loiter hotspot') + '</b><br>' +
                    c.lat.toFixed(1) + '°N, ' + c.lon.toFixed(1) + '°E<br>' +
                    (zh() ? '滯留總時數：' : 'Total loiter: ') + c.loiter_hours + ' h<br>' +
                    (zh() ? '涉及船數：' : 'Vessels: ') + c.vessels + '<br>' +
                    (zh() ? '事件數：' : 'Events: ') + c.events + '<br>' +
                    (zh() ? '平均船速：' : 'Avg speed: ') +
                    HotspotLayer.speedLabel(c.avg_speed_kn);
            }
        });
    }

    function fitToHotspots(cells) {
        var pts = (cells || []).filter(function (c) {
            return c.lat != null && c.lon != null;
        }).map(function (c) { return [c.lat, c.lon]; });
        if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 8 });
    }

    // ── 長條圖 ────────────────────────────────────────────
    function barChart(canvasId, key, labels, values, colors) {
        if (typeof Chart === 'undefined') return;
        if (charts[key]) charts[key].destroy();
        var el = $(canvasId);
        if (!el) return;
        charts[key] = new Chart(el.getContext('2d'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{ data: values, backgroundColor: colors,
                             borderWidth: 0, borderRadius: 3 }]
            },
            options: {
                indexAxis: 'y',
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#8aa4c8' },
                         grid: { color: 'rgba(26,42,64,0.8)' } },
                    y: { ticks: { color: '#e8eef7' }, grid: { display: false } }
                }
            }
        });
    }

    function renderCharts(report) {
        var s = report.summary || {};

        var byType = s.by_type || {};
        var types = Object.keys(byType).sort(function (a, b) {
            return byType[b] - byType[a];
        });
        barChart('typeChart', 'type',
                 types.map(typeLabel),
                 types.map(function (t) { return byType[t]; }),
                 types.map(function (t) { return TYPE_COLORS[t] || '#888'; }));

        // ⚠️ by_flag 的鍵是「整數樣式」字串 —— JS 物件一律按數值升冪列舉，
        // 會把後端排好的 count 降冪推翻（實測變成 MID 100、101、106…）。
        // 因此這裡一定要自己再排一次。
        var byFlag = s.by_flag || {};
        var flags = Object.keys(byFlag).map(function (mid) {
            return { mid: mid, f: byFlag[mid] };
        }).sort(function (a, b) {
            return (b.f.count || 0) - (a.f.count || 0) || a.mid.localeCompare(b.mid);
        }).slice(0, 8);
        barChart('flagChart', 'flag',
                 flags.map(function (e) {
                     return (zh() ? e.f.zh : e.f.en) + ' (' + e.mid + ')';
                 }),
                 flags.map(function (e) { return e.f.count; }),
                 flags.map(function () { return '#00f5ff'; }));
    }

    // ── 期別清單 ──────────────────────────────────────────
    function chipHtml(kind, e) {
        var label = kind === 'weekly' ? e.week : e.month;
        var active = current && current.kind === kind && current.label === label;
        return '<button class="period-chip' + (active ? ' active' : '') +
            '" data-kind="' + kind + '" data-label="' + esc(label) + '">' +
            '<span class="chip-label">' + esc(label) + '</span>' +
            '<span class="chip-sub">' + (e.unique_highrisk == null ? '—'
                : e.unique_highrisk) + (zh() ? ' 艘' : ' vsl') + '</span></button>';
    }

    function renderPeriodList() {
        if (!manifest) return;
        $('periodListWeekly').innerHTML = manifest.weekly.length
            ? manifest.weekly.map(function (e) { return chipHtml('weekly', e); }).join('')
            : '<span class="period-empty">' +
              (zh() ? '尚無週報' : 'No weekly reports yet') + '</span>';
        $('periodListMonthly').innerHTML = manifest.monthly.length
            ? manifest.monthly.map(function (e) { return chipHtml('monthly', e); }).join('')
            : '<span class="period-empty">' +
              (zh() ? '尚無月報' : 'No monthly reports yet') + '</span>';
        Array.prototype.forEach.call(
            document.querySelectorAll('.period-chip'), function (btn) {
                btn.addEventListener('click', function () {
                    selectPeriod(btn.dataset.kind, btn.dataset.label);
                });
            });
    }

    // ── 報表渲染 ──────────────────────────────────────────
    function renderReport(report) {
        plotHotspots(report.hotspots || []);
        renderCharts(report);

        var expect = current.kind === 'weekly' ? 7 : 28;
        var covered = report.days_covered || 0;
        var note = '';
        if (covered < expect) {
            note = zh()
                ? '⚠️ 本期僅累積 ' + covered + ' 天資料（資料累積剛啟用或該期有漏跑），數字尚不完整。'
                : '⚠️ Only ' + covered + ' day(s) of data accumulated for this period — figures are incomplete.';
        }
        $('coverageNote').textContent = note;
        $('coverageNote').style.display = note ? '' : 'none';

        $('updateInfo').textContent =
            (zh() ? '報表區間（UTC）：' : 'Period (UTC): ') + report.start +
            ' ～ ' + report.end +
            (zh() ? '　熱區 ' : ' · ') + (report.hotspots || []).length +
            (zh() ? ' 格　產生於 ' : ' cells · generated ') +
            new Date(report.generated_at).toLocaleString();

        $('reportBody').style.display = '';
        $('pendingNotice').style.display = 'none';
    }

    function selectPeriod(kind, label) {
        current = { kind: kind, label: label };
        renderPeriodList();
        $('dataStatus').textContent = zh() ? '載入中...' : 'Loading...';
        fetch('reports/' + kind + '/' + label + '.json?' + Date.now())
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (d) {
                cached = d;
                renderReport(d);
                fitToHotspots(d.hotspots);
                $('dataStatus').textContent = '✅';
            })
            .catch(function (e) {
                console.error('Report load failed:', e);
                $('dataStatus').textContent = zh() ? '❌ 載入失敗' : '❌ Load failed';
            });
    }

    function showPending() {
        $('reportBody').style.display = 'none';
        $('pendingNotice').style.display = '';
        $('dataStatus').textContent = zh() ? '⏳ 尚未產生' : '⏳ Pending';
    }

    function loadManifest() {
        fetch(MANIFEST_URL + '?' + Date.now())
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (m) {
                manifest = m;
                renderPeriodList();
                if (m.weekly.length) selectPeriod('weekly', m.weekly[0].week);
                else if (m.monthly.length) selectPeriod('monthly', m.monthly[0].month);
                else showPending();
            })
            .catch(function () { showPending(); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        initMap();
        loadManifest();
    });
    // 語言切換：期別 chips、popup 與圖表標籤都是動態字串，需重繪
    window.addEventListener('langchange', function () {
        renderPeriodList();
        if (cached) renderReport(cached);
    });
})();

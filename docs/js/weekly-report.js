/**
 * Taiwan Gray Zone Monitor — 高風險船舶週報/月報頁
 * weekly-report.html 的全部邏輯：
 *  - reports/weekly/index.json（aggregate_highrisk.write_manifest 產生）→ 期別清單
 *  - 選期別 → 載入 reports/{weekly,monthly}/<label>.json → 統計 + 互動地圖 + 表格
 *  - 地圖三層：0.1° 徘徊熱區（時數色階）、高風險船最後位置（風險色）、海纜路線
 * 資料與異常判讀都是後端算好的，這裡只負責呈現。
 */
(function () {
    'use strict';

    var MANIFEST_URL = 'reports/weekly/index.json';
    var map = null;
    var layers = {};
    var manifest = null;
    var current = null;    // {kind:'weekly'|'monthly', label}
    var cached = null;     // 目前期別的完整報表 JSON

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
    var RISK_COLORS = { critical: '#ff2d55', high: '#ff7847', medium: '#ffab2e' };
    // 熱區色階／分位切檔／格線幾何由 js/hotspot-layer.js 提供（statistics.html
    // 與 Python 的 report_charts.py 同一組值 —— 同一份資料不能各頁不同色）
    var HEAT_COLORS = HotspotLayer.HEAT_COLORS;

    function typeLabel(t) {
        var e = TYPE_LABELS[t];
        // 未對映的型別（如 high_speed）顯示原始字串 —— 一律 fallback 成
        // 「不明」會讓兩種不同船種在圖例上都叫 Unknown，看起來像重複
        if (!e) return t || (zh() ? '不明' : 'Unknown');
        return zh() ? e[0] : e[1];
    }

    // ── 地圖 ──────────────────────────────────────────────
    function initMap() {
        map = L.map('map', {
            center: [24.0, 120.8], zoom: 6,
            zoomControl: true, attributionControl: true
        });
        // CARTO 免費底圖已改為需 API key（無 key 會打上浮水印），改用免金鑰的 Esri 暗色底圖
        // （World_Dark_Gray 原生只到 z16，maxNativeZoom 讓更深的縮放放大 z16 圖磚而非變空白）
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
            maxNativeZoom: 16, maxZoom: 18, opacity: 0.9,
            attribution: 'Tiles &copy; Esri'
        }).addTo(map);
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
            maxNativeZoom: 16, maxZoom: 18, opacity: 0.9
        }).addTo(map);
        layers.cables = L.layerGroup().addTo(map);
        layers.hotspots = L.layerGroup().addTo(map);
        layers.vessels = L.layerGroup().addTo(map);

        [['lyrHotspots', 'hotspots'], ['lyrVessels', 'vessels'],
         ['lyrCables', 'cables']].forEach(function (pair) {
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
                    (zh() ? '滯留總時數：' : 'Total loiter: ') + c.loiter_hours +
                    ' h<br>' +
                    (zh() ? '涉及船數：' : 'Vessels: ') + c.vessels + '<br>' +
                    (zh() ? '事件數：' : 'Events: ') + c.events + '<br>' +
                    (zh() ? '平均船速：' : 'Avg speed: ') +
                    HotspotLayer.speedLabel(c.avg_speed_kn);
            }
        });
    }

    function vesselPopup(v) {
        var rows = [
            ['MMSI', v.mmsi],
            [zh() ? '船種' : 'Type', typeLabel(v.vessel_type)],
            [zh() ? '船籍' : 'Flag', zh() ? v.flag_zh : v.flag_en],
            [zh() ? '風險分數' : 'Risk score',
             v.max_risk_score + '（' + v.risk_level + '）'],
            [zh() ? '本期出現天數' : 'Days seen', v.days_seen],
            [zh() ? '海纜滯留' : 'Cable loiter',
             v.cable_loiter_hours > 0 ? v.cable_loiter_hours + ' h' : '—'],
            [zh() ? '滯留均速' : 'Loiter avg speed',
             v.cable_loiter_avg_speed_kn == null
                 ? '—' : v.cable_loiter_avg_speed_kn + ' kn'],
            [zh() ? '出港' : 'Departed',
             v.departure_port ? esc(v.departure_port) : '—'],
            [zh() ? '海上時間' : 'At sea',
             v.time_at_sea_hours == null ? '—'
                 : v.time_at_sea_hours + ' h' +
                   (v.time_at_sea_note ? '（' + esc(v.time_at_sea_note) + '）' : '')]
        ];
        return '<b>' + (esc(v.name) || 'MMSI ' + esc(v.mmsi)) + '</b><br>' +
            rows.map(function (r) {
                return r[0] + '：' + r[1];
            }).join('<br>');
    }

    function plotVessels(vessels) {
        layers.vessels.clearLayers();
        vessels.forEach(function (v) {
            if (v.last_lat == null || v.last_lon == null) return;
            var color = RISK_COLORS[v.risk_level] || '#ffab2e';
            // 點畫小且半透明：一期有數百艘船，實心大點會整片蓋掉底下的
            // 熱區方格 —— 熱區才是本頁的主訊號
            var m = L.circleMarker([v.last_lat, v.last_lon], {
                radius: v.risk_level === 'critical' ? 4 : 3,
                color: color, weight: 1.2, opacity: 0.9,
                fillColor: TYPE_COLORS[v.vessel_type] || '#888',
                fillOpacity: 0.55
            }).bindPopup(vesselPopup(v));
            m._mmsi = v.mmsi;
            m.addTo(layers.vessels);
        });
    }

    function focusVessel(mmsi) {
        var found = null;
        layers.vessels.eachLayer(function (l) {
            if (l._mmsi === mmsi) found = l;
        });
        if (found) {
            map.setView(found.getLatLng(), Math.max(map.getZoom(), 8));
            found.openPopup();
            $('map').scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    function fitToData(report) {
        var pts = [];
        (report.hotspots || []).forEach(function (c) { pts.push([c.lat, c.lon]); });
        (report.vessels || []).forEach(function (v) {
            if (v.last_lat != null) pts.push([v.last_lat, v.last_lon]);
        });
        if (pts.length) {
            map.fitBounds(L.latLngBounds(pts).pad(0.12), { maxZoom: 8 });
        }
    }

    // ── 期別清單 ──────────────────────────────────────────
    function chipHtml(kind, e) {
        var label = kind === 'weekly' ? e.week : e.month;
        var active = current && current.kind === kind && current.label === label;
        return '<button class="period-chip' + (active ? ' active' : '') +
            '" data-kind="' + kind + '" data-label="' + esc(label) + '">' +
            '<span class="chip-label">' + esc(label) + '</span>' +
            '<span class="chip-sub">' + (e.unique_highrisk == null ? '—'
                : e.unique_highrisk) + (zh() ? ' 艘' : ' vsl') +
            (e.days_covered != null && e.days_covered <
                (kind === 'weekly' ? 7 : 28)
                ? ' · ' + e.days_covered + (zh() ? ' 天' : 'd') : '') +
            '</span></button>';
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
    function renderSummary(report) {
        var s = report.summary || {};
        $('statVessels').textContent = s.unique_highrisk != null ? s.unique_highrisk : '--';
        $('statCritical').textContent = s.critical != null ? s.critical : '--';
        $('statLoiterVessels').textContent =
            s.cable_loiter_vessels != null ? s.cable_loiter_vessels : '--';
        $('statLoiterHours').textContent =
            s.cable_loiter_hours_total != null
                ? Math.round(s.cable_loiter_hours_total).toLocaleString() : '--';

        var byType = s.by_type || {};
        $('byTypeChips').innerHTML = Object.keys(byType).sort(function (a, b) {
            return byType[b] - byType[a];
        }).map(function (t) {
            return '<span class="type-chip" style="border-color:' +
                (TYPE_COLORS[t] || '#888') + '">' +
                '<span class="type-dot" style="background:' +
                (TYPE_COLORS[t] || '#888') + '"></span>' +
                esc(typeLabel(t)) + ' ' + byType[t] + '</span>';
        }).join('');

        // ⚠️ 後端已依船數排好序，但 MID 是「整數樣式」字串鍵 —— JS 物件對這種
        // 鍵一律按數值升冪列舉，會把排序推翻（實測變成 MID 100、101、106…）。
        // 因此這裡一定要自己再排一次，不能依賴 Object.keys 的順序。
        var byFlag = s.by_flag || {};
        var flags = Object.keys(byFlag).map(function (mid) {
            return { mid: mid, f: byFlag[mid] };
        }).sort(function (a, b) {
            return (b.f.count || 0) - (a.f.count || 0) ||
                   a.mid.localeCompare(b.mid);
        }).slice(0, 8);
        $('byFlagList').innerHTML = flags.map(function (e) {
            return '<tr><td>' + esc(zh() ? e.f.zh : e.f.en) +
                ' <span class="mid-code">MID ' + esc(e.mid) + '</span></td>' +
                '<td class="num">' + e.f.count + '</td></tr>';
        }).join('') || '<tr><td colspan="2">—</td></tr>';

        var note = '';
        var expect = current.kind === 'weekly' ? 7 : 28;
        if ((report.days_covered || 0) < expect) {
            note = zh()
                ? '⚠️ 本期僅累積 ' + report.days_covered +
                  ' 天資料（資料累積剛啟用或該期有漏跑；之後的期別會是完整區間）'
                : '⚠️ Only ' + report.days_covered +
                  ' day(s) of data accumulated for this period (accumulation ' +
                  'recently started or runs were missed; later periods will be complete)';
        }
        $('coverageNote').textContent = note;
        $('coverageNote').style.display = note ? '' : 'none';

        $('updateInfo').textContent =
            (zh() ? '報表區間（UTC）：' : 'Period (UTC): ') + report.start +
            ' ～ ' + report.end +
            (zh() ? '　產生於 ' : ' · generated ') +
            new Date(report.generated_at).toLocaleString();
    }

    function renderHotspotTable(cells) {
        var rows = cells.slice(0, 10).map(function (c, i) {
            return '<tr data-lat="' + c.lat + '" data-lon="' + c.lon + '">' +
                '<td>' + (i + 1) + '</td>' +
                '<td class="mono">' + c.lat.toFixed(1) + '°N ' +
                c.lon.toFixed(1) + '°E</td>' +
                '<td class="num">' + c.loiter_hours + '</td>' +
                '<td class="num">' + c.vessels + '</td>' +
                '<td class="num">' + (c.avg_speed_kn == null ? '—' : c.avg_speed_kn) +
                '</td></tr>';
        }).join('');
        $('hotspotTable').innerHTML = rows ||
            '<tr><td colspan="5">' + (zh() ? '本期無合格徘徊事件' :
                'No qualifying loiter events this period') + '</td></tr>';
        Array.prototype.forEach.call(
            document.querySelectorAll('#hotspotTable tr[data-lat]'), function (tr) {
                tr.addEventListener('click', function () {
                    map.setView([+tr.dataset.lat, +tr.dataset.lon], 8);
                    $('map').scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            });
    }

    function renderVesselTable(vessels) {
        var rows = vessels.slice(0, 20).map(function (v) {
            return '<tr data-mmsi="' + esc(v.mmsi) + '">' +
                '<td><span class="risk-badge" style="background:' +
                (RISK_COLORS[v.risk_level] || '#ffab2e') + '">' +
                v.max_risk_score + '</span></td>' +
                '<td><div class="v-name">' + (esc(v.name) || '—') +
                '</div><div class="v-mmsi mono">' + esc(v.mmsi) + '</div></td>' +
                '<td>' + esc(typeLabel(v.vessel_type)) + '</td>' +
                '<td>' + esc(zh() ? v.flag_zh : v.flag_en) + '</td>' +
                '<td class="num">' +
                (v.cable_loiter_hours > 0 ? v.cable_loiter_hours : '—') + '</td>' +
                '<td class="num">' + (v.cable_loiter_avg_speed_kn == null
                    ? '—' : v.cable_loiter_avg_speed_kn) + '</td>' +
                '<td class="num">' + v.days_seen + '</td>' +
                '<td>' + (v.departure_port ? esc(v.departure_port) : '—') +
                '</td></tr>';
        }).join('');
        $('vesselTable').innerHTML = rows ||
            '<tr><td colspan="8">' + (zh() ? '本期無資料' : 'No data') + '</td></tr>';
        Array.prototype.forEach.call(
            document.querySelectorAll('#vesselTable tr[data-mmsi]'), function (tr) {
                tr.addEventListener('click', function () {
                    focusVessel(tr.dataset.mmsi);
                });
            });
    }

    function renderReport(report) {
        renderSummary(report);
        plotHotspots(report.hotspots || []);
        plotVessels(report.vessels || []);
        renderHotspotTable(report.hotspots || []);
        renderVesselTable(report.vessels || []);

        var base = 'reports/' + current.kind + '/' + current.label;
        $('dlCsv').href = base + '.csv';
        $('dlJson').href = base + '.json';
        $('reportBody').style.display = '';
        $('pendingNotice').style.display = 'none';
    }

    function selectPeriod(kind, label, skipFit) {
        current = { kind: kind, label: label };
        renderPeriodList();   // 更新 active chip
        $('dataStatus').textContent = zh() ? '載入中...' : 'Loading...';
        fetch('reports/' + kind + '/' + label + '.json?' + Date.now())
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (d) {
                cached = d;
                renderReport(d);
                if (!skipFit) fitToData(d);
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
                if (m.weekly.length) {
                    selectPeriod('weekly', m.weekly[0].week);
                } else if (m.monthly.length) {
                    selectPeriod('monthly', m.monthly[0].month);
                } else {
                    showPending();
                }
            })
            .catch(function () { showPending(); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        initMap();
        loadManifest();
    });
    // 語言切換：重繪清單與目前報表（popup/表格/統計文字都是動態字串）
    window.addEventListener('langchange', function () {
        renderPeriodList();
        if (cached) renderReport(cached);
    });
})();

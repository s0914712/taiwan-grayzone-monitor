/**
 * Taiwan Gray Zone Monitor - Vessel Route Module
 * Route loading (pre-generated file + history fallback), rendering, track panel, snapshot.
 * Factory invoked by map.js init() once the Leaflet map + layer groups exist.
 * Load order (HTML): map-data.js → map-baseline.js → map-vessels.js →
 * map-routes.js → map-cables.js → map.js
 */

var MapRoutesFactory = function(map, layers) {
    'use strict';

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
    return {
        loadVesselRoute,
        clearVesselRoute,
        searchVesselRoute,
        snapshotMap,
    };
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapRoutesFactory;
}

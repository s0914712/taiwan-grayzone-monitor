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
        darkVessels: null,
        submarineCables: null,
        vesselRoutes: null
    };
    let vesselMarkers = {};

    // Cached vessel data for zoom-based re-rendering
    let cachedVesselList = [];
    let cachedVessels = new Map();
    let cachedStats = { total: 0, fishing: 0, cargo: 0, tanker: 0, suspicious: 0 };

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
    const REGION_COLORS = {
        taiwan_strait: '#ff3366',
        east_taiwan: '#ff6b35',
        south_china_sea: '#ffd700',
        east_china_sea: '#9b59b6'
    };

    const REGION_NAMES = {
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
        layers.darkVessels = L.layerGroup().addTo(map);
        layers.submarineCables = L.layerGroup();
        layers.vesselRoutes = L.layerGroup().addTo(map);

        // Draw Taiwan outline
        drawTaiwanOutline();

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

        return map;
    }

    /**
     * Draw Taiwan island outline
     */
    function drawTaiwanOutline() {
        const taiwanOutline = [
            [25.3, 121.0], [25.13, 121.5], [24.85, 121.82], [24.5, 121.8],
            [24.0, 121.5], [23.5, 121.3], [23.0, 121.0], [22.5, 120.85],
            [22.0, 120.75], [21.9, 120.85], [22.0, 120.45], [22.3, 120.25],
            [22.6, 120.3], [23.0, 120.1], [23.5, 120.05], [24.0, 120.2],
            [24.5, 120.5], [25.0, 121.0], [25.3, 121.0]
        ];

        L.polygon(taiwanOutline, {
            color: '#4a90d9',
            weight: 2,
            opacity: 0.5,
            fillColor: '#1a3a5c',
            fillOpacity: 0.3
        }).addTo(map);
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
            const color = isSuspicious ? '#ff3366' : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.other);

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

            const heading = v.heading !== undefined && v.heading !== null ? v.heading : null;
            const icon = createVesselIcon(color, isSuspicious, heading);
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const suspiciousInfo = isSuspicious
                ? `<br><b style="color:#ff3366">${t('app.csis_suspicious')}</b>`
                : '';

            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';

            marker.bindPopup(`
                <b>${v.name || 'Unknown'}</b><br>
                ${t('app.mmsi')} ${v.mmsi}<br>
                ${t('app.type')} ${v.type_name || t('common.unknown')}<br>
                ${t('app.speed')} ${(v.speed || 0).toFixed(1)} kn<br>
                航向: ${headingText}${suspiciousInfo}${routeLink}
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
            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';

            marker.bindPopup(
                '<b>' + (v.name || 'Unknown') + '</b><br>' +
                t('app.mmsi') + ' ' + v.mmsi + '<br>' +
                t('app.type') + ' ' + (v.type_name || t('common.unknown')) + '<br>' +
                t('app.speed') + ' ' + (v.speed || 0).toFixed(1) + ' kn<br>' +
                '航向: ' + headingText + suspiciousInfo + routeLink
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
            '<div class="track-info-header">' + t('app.track_info_title') + '</div>' +
            '<div class="track-info-body">' +
                '<div><b>' + (data.name || 'Unknown') + '</b></div>' +
                '<div>' + t('app.mmsi') + ' ' + data.mmsi + '</div>' +
                '<div>' + startDate + ' ~ ' + endDate + '</div>' +
                '<div>' + t('app.track_points') + ' ' + points + '</div>' +
                '<div>' + t('app.track_source') + ' ' + sourceName + '</div>' +
            '</div>' +
            '<button class="track-clear-btn" onclick="MapModule.clearVesselRoute(); return false;">' +
                t('app.clear_track') + '</button>';
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
        const riskColors = { critical: '#ff3366', high: '#ff6b35', medium: '#ffd700' };

        if (!suspiciousData.suspicious_vessels) return;

        suspiciousData.suspicious_vessels.forEach(sv => {
            if (sv.last_lat && sv.last_lon) {
                L.circleMarker([sv.last_lat, sv.last_lon], {
                    radius: 8,
                    fillColor: riskColors[sv.risk_level] || '#ff3366',
                    color: '#ffffff',
                    weight: 2,
                    opacity: 0.9,
                    fillOpacity: 0.9
                }).addTo(layers.vessels).bindPopup(() => {
                    const t3 = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
                    return `<b style="color:${riskColors[sv.risk_level] || '#ff3366'}">${(sv.names && sv.names[0]) || sv.mmsi}</b><br>
                    ${t3('app.mmsi')} ${sv.mmsi}<br>
                    <b>${t3('app.risk')} ${sv.risk_level.toUpperCase()}</b> (${t3('app.score')} ${sv.risk_score})<br>
                    ${(sv.flags || []).map(f => '- ' + f).join('<br>')}`;
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
        setFilterFoc,
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

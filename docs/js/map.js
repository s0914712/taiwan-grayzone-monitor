/**
 * Taiwan Gray Zone Monitor - Map Module
 * Handles Leaflet map initialization and vessel/zone rendering
 */

const MapModule = (function() {
    'use strict';
    // Static data & pure helpers shared via js/map-data.js
    // (must be loaded before this file)
    const { riskColors, MID_FLAG_TABLE, getMidFlag, CLUSTER_ZOOM_THRESHOLD, CLUSTER_CENTERS, FISHING_HOTSPOTS, VESSEL_COLORS, GOV_REGEX, GOV_TYPES, GOV_BADGE_ICON, getGovType, govLabel, FOC_MIDS, FOC_COMMERCIAL_TYPES, REGION_COLORS, REGION_NAMES, TERRITORIAL_BASEPOINT_MARKERS, offsetPolygonNm, _NAV_STATUS, _decodeNavStatus, debounce, createVesselIcon } = MapData;

    let map;
    let layers = {
        fishingHotspots: null,
        vessels: null,
        suspiciousVessels: null,
        govVessels: null,
        darkVessels: null,
        submarineCables: null,
        vesselRoutes: null,
        territorialBaseline: null
    };
    let vesselMarkers = {};

    // Cached vessel data for zoom-based re-rendering
    let cachedVesselList = [];
    let cachedVessels = new Map();
    let cachedStats = { total: 0, fishing: 0, cargo: 0, tanker: 0, suspicious: 0 };

    // UN sanctions lookup (loaded on init)
    var sanctionsNameSet = new Set();
    var sanctionsImoSet = new Set();
    var sanctionsByName = {};  // uppercase name -> sanction entry

    // Suspicious vessel data reference (set by app.js)
    let _suspiciousData = null;

    // Risk level colors (used in suspicious markers + info cards)


    let filterFocEnabled = false;


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
        layers.suspiciousVessels = L.layerGroup().addTo(map);
        layers.govVessels = L.layerGroup().addTo(map);
        layers.darkVessels = L.layerGroup().addTo(map);
        layers.submarineCables = L.layerGroup();
        layers.vesselRoutes = L.layerGroup().addTo(map);
        layers.territorialBaseline = L.layerGroup();

        // Draw Taiwan outline


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
            // Debounced auto-search: fire only on full 9-digit MMSI
            // (5-8 digit MMSIs still searchable via Enter / the 查詢 button)
            mmsiInput.addEventListener('input', debounce(function() {
                var v = mmsiInput.value.trim();
                if (/^\d{9}$/.test(v)) loadVesselRoute(v);
            }, 600));
        }

        // Load UN sanctions list for vessel warnings
        loadSanctionsList();

        return map;
    }



    // ── 詳細領海基線座標（從內政部 SHP 檔案轉換，存於 data/territorial_baseline.json）──
    var TERRITORIAL_BASELINE_DETAILED = null; // Loaded async


    /**
     * Draw territorial sea baseline, 12nm territorial sea limit,
     * and 24nm contiguous zone limit (領海基線 + 領海外界線 + 鄰接區外界線)
     * Uses detailed baseline from SHP data (territorial_baseline.json)
     */
    function drawTerritorialBaseline() {
        var layer = layers.territorialBaseline;
        layer.clearLayers();

        // Load detailed baseline from SHP-derived JSON, then draw
        if (TERRITORIAL_BASELINE_DETAILED) {
            _drawBaselineLayers(layer);
        } else {
            fetch('data/territorial_baseline.json')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    TERRITORIAL_BASELINE_DETAILED = data;
                    _drawBaselineLayers(layer);
                })
                .catch(function(err) {
                    console.warn('Failed to load territorial_baseline.json, using basepoints:', err);
                    _drawBaselineFromPoints(layer);
                });
        }
    }

    // Reference center points for radial offset (approximate geographic center)
    var REGION_CENTERS = {
        taiwan:  { lat: 23.65, lon: 120.90 },  // Central Taiwan
        dongsha: { lat: 20.70, lon: 116.72 }   // Dongsha Atoll center
    };

    /**
     * Draw baseline layers using detailed SHP coordinates
     */
    function _drawBaselineLayers(layer) {
        var lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';

        ['taiwan', 'dongsha'].forEach(function(region) {
            var markers = TERRITORIAL_BASEPOINT_MARKERS[region];
            var detailed = TERRITORIAL_BASELINE_DETAILED[region];
            var center = REGION_CENTERS[region];
            var regionLabel = lang === 'en'
                ? (region === 'taiwan' ? 'Taiwan' : 'Dongsha')
                : (region === 'taiwan' ? '台灣本島及附屬島嶼' : '東沙群島');

            // ── 1. Baseline 領海基線 (purple dashed) — from detailed SHP data ──
            var baseLatLngs = detailed.map(function(p) { return [p[1], p[0]]; });

            L.polyline(baseLatLngs, {
                color: '#e040fb',
                weight: 2,
                opacity: 0.8,
                dashArray: '8,5'
            }).addTo(layer).bindTooltip(
                (lang === 'en' ? 'Territorial Baseline — ' : '領海基線 — ') + regionLabel,
                { sticky: true }
            );

            // Basepoint markers
            markers.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {
                    radius: 3.5,
                    fillColor: '#e040fb',
                    color: '#fff',
                    weight: 1,
                    fillOpacity: 0.9
                }).addTo(layer).bindTooltip(
                    p.id + ' ' + (lang === 'en' ? p.nameE : p.name),
                    { permanent: false, direction: 'top', offset: [0, -6] }
                );
            });

            // Use basepoint markers for offset (uniform spacing, avoids
            // 125-point Pengjia cluster skewing the offset calculation)
            var offsetPts = markers.map(function(p) { return { lat: p.lat, lon: p.lon }; });

            // ── 2. Territorial Sea Limit 領海外界線 12nm (cyan dashed) ──
            var ts12 = offsetPolygonNm(offsetPts, 12, center.lat, center.lon);
            if (ts12.length > 0) {
                L.polyline(ts12, {
                    color: '#00f5ff',
                    weight: 1.8,
                    opacity: 0.6,
                    dashArray: '12,6'
                }).addTo(layer).bindTooltip(
                    (lang === 'en' ? 'Territorial Sea 12nm — ' : '領海外界線 12 浬 — ') + regionLabel,
                    { sticky: true }
                );
            }

            // ── 3. Contiguous Zone Limit 鄰接區外界線 24nm (yellow dashed) ──
            var cz24 = offsetPolygonNm(offsetPts, 24, center.lat, center.lon);
            if (cz24.length > 0) {
                L.polyline(cz24, {
                    color: '#ffd700',
                    weight: 1.5,
                    opacity: 0.45,
                    dashArray: '10,8'
                }).addTo(layer).bindTooltip(
                    (lang === 'en' ? 'Contiguous Zone 24nm — ' : '鄰接區外界線 24 浬 — ') + regionLabel,
                    { sticky: true }
                );
            }
        });
    }

    /**
     * Fallback: draw baseline from basepoints only (if JSON load fails)
     */
    function _drawBaselineFromPoints(layer) {
        var lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';

        ['taiwan', 'dongsha'].forEach(function(region) {
            var pts = TERRITORIAL_BASEPOINT_MARKERS[region];
            var center = REGION_CENTERS[region];
            var regionLabel = lang === 'en'
                ? (region === 'taiwan' ? 'Taiwan' : 'Dongsha')
                : (region === 'taiwan' ? '台灣本島及附屬島嶼' : '東沙群島');

            var baseLatLngs = pts.map(function(p) { return [p.lat, p.lon]; });
            baseLatLngs.push(baseLatLngs[0]);

            L.polyline(baseLatLngs, {
                color: '#e040fb', weight: 2, opacity: 0.8, dashArray: '8,5'
            }).addTo(layer).bindTooltip(
                (lang === 'en' ? 'Territorial Baseline — ' : '領海基線 — ') + regionLabel,
                { sticky: true }
            );

            pts.forEach(function(p) {
                L.circleMarker([p.lat, p.lon], {
                    radius: 3.5, fillColor: '#e040fb', color: '#fff', weight: 1, fillOpacity: 0.9
                }).addTo(layer).bindTooltip(
                    p.id + ' ' + (lang === 'en' ? p.nameE : p.name),
                    { permanent: false, direction: 'top', offset: [0, -6] }
                );
            });

            var ts12 = offsetPolygonNm(pts, 12, center.lat, center.lon);
            if (ts12.length > 0) {
                L.polyline(ts12, {
                    color: '#00f5ff', weight: 1.8, opacity: 0.6, dashArray: '12,6'
                }).addTo(layer);
            }
            var cz24 = offsetPolygonNm(pts, 24, center.lat, center.lon);
            if (cz24.length > 0) {
                L.polyline(cz24, {
                    color: '#ffd700', weight: 1.5, opacity: 0.45, dashArray: '10,8'
                }).addTo(layer);
            }
        });
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
     * Load UN sanctions vessel list for matching
     */
    function loadSanctionsList() {
        fetch('un_sanctions_vessels.json?' + Date.now())
            .then(function(res) { return res.ok ? res.json() : null; })
            .then(function(data) {
                if (!data || !data.vessels) return;
                data.vessels.forEach(function(v) {
                    var name = (v.name || '').toUpperCase().trim();
                    if (name) {
                        sanctionsNameSet.add(name);
                        sanctionsByName[name] = v;
                    }
                    if (v.imo) sanctionsImoSet.add(v.imo);
                });
                console.log('UN sanctions loaded:', sanctionsNameSet.size, 'vessels');
            })
            .catch(function() { /* sanctions file not available */ });
    }

    /**
     * Check if a vessel matches sanctions list (by name)
     */
    function getSanctionMatch(vesselName) {
        if (!vesselName) return null;
        var upper = vesselName.toUpperCase().trim();
        if (sanctionsNameSet.has(upper)) return sanctionsByName[upper];
        return null;
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
            const govType = getGovType(v);
            const color = isSuspicious ? '#ff3366'
                : govType ? VESSEL_COLORS[govType]
                : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.other);

            // Gov / special-interest vessels get a persistent white circle on a
            // dedicated layer (see displayGovVessels) — no extra glow here.

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
            const icon = createVesselIcon(color, isSuspicious || !!govType, heading, (v.name || v.mmsi || 'vessel') + '');
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const suspiciousInfo = isSuspicious
                ? `<br><b style="color:#ff3366">${t('app.csis_suspicious')}</b>`
                : '';
            const sanctionHit = getSanctionMatch(v.name);
            const sanctionInfo = sanctionHit
                ? `<br><span class="sanction-warning">${t('app.sanctioned')} (${t('app.sanction_res')} ${sanctionHit.resolution || '1718'})</span>`
                : '';
            const destInfo = v.destination
                ? '<br>📍 Dest: ' + v.destination
                : '';
            const navLabel = _decodeNavStatus(v.nav_status);
            const navInfo = navLabel ? '<br>狀態: ' + navLabel : '';
            const imoInfo = v.imo && v.imo !== '0' ? '<br>IMO: ' + v.imo : '';
            const coastGuardBadge = govType
                ? '<br><b style="color:' + VESSEL_COLORS[govType] + '">' + GOV_BADGE_ICON[govType] + ' ' + govLabel(govType) + '</b>'
                : '';

            // External lookup: MarineTraffic by MMSI for from/destination details
            const mtLink = '<br><a class="mt-lookup-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' +
                v.mmsi + '" target="_blank" rel="noopener">🔎 From / Dest 查詢</a>';

            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';
            const netMarkerNote = (v.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
            const flagName = getMidFlag(v.mmsi);
            const flagLine = flagName ? '<br>' + t('app.flag') + ' ' + flagName : '';

            marker.bindPopup(`
                <b>${v.name || 'Unknown'}</b><br>
                ${t('app.mmsi')} ${v.mmsi}${imoInfo}<br>
                ${t('app.type')} ${v.type_name || t('common.unknown')}${flagLine}<br>
                ${t('app.speed')} ${(v.speed || 0).toFixed(1)} kn<br>
                航向: ${headingText}${navInfo}${coastGuardBadge}${destInfo}${suspiciousInfo}${sanctionInfo}${routeLink}${mtLink}${netMarkerNote}
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
            const govType = getGovType(v);
            const color = isSuspicious ? '#ff3366'
                : govType ? VESSEL_COLORS[govType]
                : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.other);

            // Gov / special-interest vessels: persistent white circle drawn by
            // displayGovVessels on a dedicated layer — no extra glow here.

            if (isSuspicious) {
                L.circleMarker([v.lat, v.lon], {
                    radius: 12, fillColor: '#ff3366', color: '#ff3366',
                    weight: 1, opacity: 0.3, fillOpacity: 0.15
                }).addTo(layers.vessels);
            }

            const heading = v.heading !== undefined && v.heading !== null ? v.heading : null;
            const icon = createVesselIcon(color, isSuspicious || !!govType, heading, (v.name || v.mmsi || 'vessel') + '');
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const suspiciousInfo = isSuspicious
                ? '<br><b style="color:#ff3366">' + t('app.csis_suspicious') + '</b>' : '';
            var sanctionHit2 = getSanctionMatch(v.name);
            var sanctionInfo2 = sanctionHit2
                ? '<br><span class="sanction-warning">' + t('app.sanctioned') + ' (' + t('app.sanction_res') + ' ' + (sanctionHit2.resolution || '1718') + ')</span>'
                : '';
            var destInfo2 = v.destination ? '<br>📍 Dest: ' + v.destination : '';
            var navInfo2 = _decodeNavStatus(v.nav_status);
            navInfo2 = navInfo2 ? '<br>狀態: ' + navInfo2 : '';
            var imoInfo2 = v.imo && v.imo !== '0' ? '<br>IMO: ' + v.imo : '';
            var mtLink2 = '<br><a class="mt-lookup-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' +
                v.mmsi + '" target="_blank" rel="noopener">🔎 From / Dest 查詢</a>';
            const routeLink = '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';
            var netMarkerNote2 = (v.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
            var coastGuardBadge2 = govType
                ? '<br><b style="color:' + VESSEL_COLORS[govType] + '">' + GOV_BADGE_ICON[govType] + ' ' + govLabel(govType) + '</b>'
                : '';
            var flagName2 = getMidFlag(v.mmsi);
            var flagLine2 = flagName2 ? '<br>' + t('app.flag') + ' ' + flagName2 : '';

            marker.bindPopup(
                '<b>' + (v.name || 'Unknown') + '</b><br>' +
                t('app.mmsi') + ' ' + v.mmsi + imoInfo2 + '<br>' +
                t('app.type') + ' ' + (v.type_name || t('common.unknown')) + flagLine2 + '<br>' +
                t('app.speed') + ' ' + (v.speed || 0).toFixed(1) + ' kn<br>' +
                '航向: ' + headingText + navInfo2 + coastGuardBadge2 + destInfo2 + suspiciousInfo + sanctionInfo2 + routeLink + mtLink2 + netMarkerNote2
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
        if (!suspiciousData.suspicious_vessels) return;

        // Clear previous suspicious markers (separate layer so they survive zoom/pan)
        layers.suspiciousVessels.clearLayers();

        suspiciousData.suspicious_vessels.forEach(sv => {
            if (sv.last_lat && sv.last_lon) {
                L.circleMarker([sv.last_lat, sv.last_lon], {
                    radius: 8,
                    fillColor: riskColors[sv.risk_level] || '#ff3366',
                    color: '#ffffff',
                    weight: 2,
                    opacity: 0.9,
                    fillOpacity: 0.9
                }).addTo(layers.suspiciousVessels).bindPopup(() => {
                    const t3 = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
                    var sanctionHit = getSanctionMatch((sv.names && sv.names[0]) || '');
                    var sanctionLine = sanctionHit
                        ? '<br><span class="sanction-warning">' + t3('app.sanctioned') + ' (' + t3('app.sanction_res') + ' ' + (sanctionHit.resolution || '1718') + ')</span>'
                        : '';
                    var netMarkerNote3 = (sv.mmsi || '').startsWith('898') ? '<br><span style="color:#ffa500;font-weight:600">🎣 可能為魚網標記</span>' : '';
                    var flagName3 = getMidFlag(sv.mmsi);
                    var flagLine3 = flagName3 ? '<br>' + t3('app.flag') + ' ' + flagName3 : '';
                    return '<b style="color:' + (riskColors[sv.risk_level] || '#ff3366') + '">' + ((sv.names && sv.names[0]) || sv.mmsi) + '</b><br>' +
                        t3('app.mmsi') + ' ' + sv.mmsi + flagLine3 + '<br>' +
                        '<b>' + t3('app.risk') + ' ' + sv.risk_level.toUpperCase() + '</b> (' + t3('app.score') + ' ' + sv.risk_score + ')<br>' +
                        (sv.flags || []).map(function(f) { return '- ' + f; }).join('<br>') +
                        sanctionLine + netMarkerNote3 +
                        '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + sv.mmsi + '\'); return false;">' + t3('app.show_track') + '</button>' +
                        '<br><button class="route-lookup-btn vic-detail-btn" onclick="MapModule.showVesselInfoCard(\'' + sv.mmsi + '\'); return false;">ℹ️ ' + t3('vic.detail') + '</button>';
                });
            }
        });
    }

    /**
     * Display China gov / special-interest vessels (海警/海巡/海救/科研) as
     * white circle markers on a dedicated layer that survives zoom/pan/cluster,
     * mirroring the high-risk vessel treatment (white ring instead of red).
     */
    function displayGovVessels(vesselList) {
        if (!layers.govVessels) return;
        layers.govVessels.clearLayers();
        if (!vesselList) return;

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;

        vesselList.forEach(v => {
            const govType = getGovType(v);
            if (!govType || v.lat == null || v.lon == null) return;

            L.circleMarker([v.lat, v.lon], {
                radius: 9,
                fillColor: '#ffffff',
                color: '#ffffff',
                weight: 2,
                opacity: 0.95,
                fillOpacity: 0.25
            }).addTo(layers.govVessels).bindPopup(() => {
                const flagName = getMidFlag(v.mmsi);
                const flagLine = flagName ? '<br>' + t('app.flag') + ' ' + flagName : '';
                const headingText = (v.heading !== undefined && v.heading !== null)
                    ? v.heading.toFixed(0) + '°' : 'N/A';
                return '<b style="color:' + (VESSEL_COLORS[govType] || '#ffffff') + '">' +
                        (GOV_BADGE_ICON[govType] || '') + ' ' + (v.name || v.mmsi) + '</b><br>' +
                    '<b>' + govLabel(govType) + '</b>' + flagLine + '<br>' +
                    t('app.mmsi') + ' ' + v.mmsi + '<br>' +
                    t('app.speed') + ' ' + (v.speed || 0).toFixed(1) + ' kn　航向: ' + headingText +
                    '<br><button class="route-lookup-btn" onclick="MapModule.loadVesselRoute(\'' + v.mmsi + '\'); return false;">' + t('app.show_track') + '</button>';
            });
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
        // Suspicious + gov vessels follow the vessels layer toggle
        if (layerName === 'vessels') {
            [layers.suspiciousVessels, layers.govVessels].forEach(lyr => {
                if (!lyr) return;
                if (visible) {
                    map.addLayer(lyr);
                } else {
                    map.removeLayer(lyr);
                }
            });
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

            const lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';
            const L_ = {
                status:  lang === 'en' ? 'Status'      : '\u72c0\u614b',
                type:    lang === 'en' ? 'Type'        : '\u985e\u578b',
                length:  lang === 'en' ? 'Length'      : '\u9577\u5ea6',
                rfs:     lang === 'en' ? 'In service'  : '\u555f\u7528',
                owners:  lang === 'en' ? 'Owners'      : '\u696d\u4e3b',
                twland:  lang === 'en' ? 'TW landing'  : '\u53f0\u7063\u767b\u9678',
                cnland:  lang === 'en' ? 'CN landing'  : '\u4e2d\u570b\u767b\u9678',
                faulted: lang === 'en' ? 'FAULT'       : '\u6545\u969c'
            };

            L.geoJSON(geoData, {
                style: f => {
                    const p = f.properties || {};
                    const slug = p.slug || '';
                    const isFaulted = faulted.has(slug);
                    const isPlanned = (p.status || '').indexOf('\u898f\u5283') >= 0;
                    return {
                        color: isFaulted ? '#ff2d55' : '#' + (p.color || 'ffd700'),
                        weight: isFaulted ? 3 : 2,
                        opacity: isFaulted ? 0.9 : (isPlanned ? 0.55 : 0.75),
                        dashArray: isPlanned ? '6,6' : null
                    };
                },
                onEachFeature: (f, layer) => {
                    const p = f.properties || {};
                    const slug = p.slug || '';
                    const name = p.name || slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    const faults = faultDetails[slug];

                    // Hover tooltip: name (+ fault summary)
                    let tip = name;
                    if (faults && faults.length > 0) {
                        const details = faults.map(ft =>
                            '\u26a0 ' + ft.segment + ': ' + (ft.description_zh || ft.description_en)
                        ).join('<br>');
                        tip = '<b style="color:#ff2d55">' + name + '</b><br>' + details;
                    }
                    layer.bindTooltip(tip, { sticky: true });

                    // Click popup: status / owners / landing points etc.
                    const rows = [];
                    const add = (label, val) => { if (val) rows.push('<div><b>' + label + '</b>: ' + val + '</div>'); };
                    const statusTxt = (faults && faults.length > 0)
                        ? '<span style="color:#ff3366;font-weight:700">\u26a0 ' + L_.faulted + '</span>'
                        : (p.status || '');
                    add(L_.status, statusTxt);
                    add(L_.type, p.cable_type);
                    add(L_.length, p.length);
                    add(L_.rfs, p.rfs);
                    add(L_.owners, p.owners);
                    add(L_.twland, p.tw_landings);
                    add(L_.cnland, p.cn_landings);
                    let popup = '<div style="font-size:12px;max-width:240px;line-height:1.5">'
                        + '<div style="font-weight:700;margin-bottom:4px;color:#00f5ff">' + name + '</div>'
                        + rows.join('');
                    if (faults && faults.length > 0) {
                        popup += '<div style="margin-top:4px;color:#ff6b6b">'
                            + faults.map(ft => '\u26a0 ' + ft.segment + ': ' + (ft.description_zh || ft.description_en)).join('<br>')
                            + '</div>';
                    }
                    popup += '</div>';
                    layer.bindPopup(popup);
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

    /**
     * Locate and zoom to vessels of a specific type
     * @param {string} type - 'fishing','cargo','tanker','coastguard','msa','rescue','research','suspicious','other'
     */
    /**
     * Show a floating vessel list panel near the legend.
     * Clicking an item zooms to that vessel and opens its popup.
     */
    function showVesselListPanel(vessels, color, title) {
        // Remove any existing panel
        dismissVesselListPanel();

        const panel = document.createElement('div');
        panel.className = 'vessel-list-panel';
        panel.innerHTML = '<div class="vlp-title">' + (title || '⛽ LNG/Gas') + ' (' + vessels.length + ')</div>';

        vessels.slice(0, 5).forEach((v, i) => {
            const row = document.createElement('div');
            row.className = 'vlp-item';
            row.innerHTML = '<span class="vlp-num">' + (i + 1) + '</span>' +
                '<span class="vlp-name">' + (v.name || 'Unknown') + '</span>' +
                '<span class="vlp-speed">' + (v.speed || 0).toFixed(1) + ' kn</span>';
            row.addEventListener('click', function () {
                map.setView([v.lat, v.lon], 13);
                if (vesselMarkers[v.mmsi]) {
                    vesselMarkers[v.mmsi].openPopup();
                }
                // Flash this vessel
                var flash = L.circleMarker([v.lat, v.lon], {
                    radius: 22, fillColor: color, color: color,
                    weight: 2, opacity: 0.9, fillOpacity: 0.35
                }).addTo(layers.vessels);
                setTimeout(function () { layers.vessels.removeLayer(flash); }, 2500);
            });
            panel.appendChild(row);
        });

        document.querySelector('.map-legend').appendChild(panel);

        // Close panel when clicking elsewhere on the map
        setTimeout(function () {
            map.once('click', dismissVesselListPanel);
        }, 100);
    }

    function dismissVesselListPanel() {
        var old = document.querySelector('.vessel-list-panel');
        if (old) old.remove();
    }

    function locateVesselType(type) {
        if (!map || cachedVesselList.length === 0) return;

        const matched = cachedVesselList.filter(v => {
            if (type === 'suspicious') {
                return v.suspicious;
            }
            if (GOV_TYPES.indexOf(type) !== -1) {
                return getGovType(v) === type;
            }
            if (type === 'other') {
                return !getGovType(v) && !v.suspicious && !['fishing', 'cargo', 'tanker'].includes(v.type_name);
            }
            return v.type_name === type;
        });

        if (matched.length === 0) return;

        // China public-service vessels (海警/海巡/海救): show a clickable list panel
        if (GOV_TYPES.indexOf(type) !== -1) {
            showVesselListPanel(matched, VESSEL_COLORS[type], GOV_BADGE_ICON[type] + ' ' + govLabel(type));
            return;
        }

        // If only one vessel, zoom to it directly
        if (matched.length === 1) {
            map.setView([matched[0].lat, matched[0].lon], 12);
            // Open popup if marker exists
            if (vesselMarkers[matched[0].mmsi]) {
                vesselMarkers[matched[0].mmsi].openPopup();
            }
            return;
        }

        // Fit bounds to show all matching vessels
        const bounds = L.latLngBounds(matched.map(v => [v.lat, v.lon]));
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });

        // Flash matching markers briefly
        const color = type === 'suspicious' ? '#ff3366'
            : (VESSEL_COLORS[type] || VESSEL_COLORS.other);

        const flashMarkers = matched.map(v => {
            return L.circleMarker([v.lat, v.lon], {
                radius: 18,
                fillColor: color,
                color: color,
                weight: 2,
                opacity: 0.8,
                fillOpacity: 0.3
            }).addTo(layers.vessels);
        });

        setTimeout(() => {
            flashMarkers.forEach(m => layers.vessels.removeLayer(m));
        }, 2000);
    }

    /**
     * Set suspicious data reference for info card lookups
     */
    function setSuspiciousData(data) {
        _suspiciousData = data;
    }

    /**
     * Show vessel deep-dive info card overlay
     * @param {string} mmsi - vessel MMSI to look up in suspicious data
     */
    function showVesselInfoCard(mmsi) {
        if (!_suspiciousData || !_suspiciousData.suspicious_vessels) {
            _buildFallbackCard(mmsi);
            return;
        }
        const sv = _suspiciousData.suspicious_vessels.find(v => v.mmsi === mmsi);
        if (sv) {
            _buildInfoCard(sv);
            return;
        }
        // Also check all_classifications
        const ac = (_suspiciousData.all_classifications || []).find(v => v.mmsi === mmsi);
        if (ac) {
            _buildInfoCard(ac);
            return;
        }
        _buildFallbackCard(mmsi);
    }

    function _buildFallbackCard(mmsi) {
        const existing = document.getElementById('vesselInfoOverlay');
        if (existing) existing.remove();

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const flagName = getMidFlag(mmsi);
        const overlay = document.createElement('div');
        overlay.id = 'vesselInfoOverlay';
        overlay.className = 'vessel-info-overlay';
        overlay.innerHTML = `
            <div class="vessel-info-card">
                <button class="vic-close" onclick="document.getElementById('vesselInfoOverlay').remove()" title="${t('vic.close')}">✕</button>
                <div class="vic-header">
                    <div class="vic-header-left">
                        <div class="vic-vessel-name">${mmsi}</div>
                        <div class="vic-vessel-meta">MMSI: ${mmsi}${flagName ? ' | ' + t('app.flag') + ' ' + flagName : ''}</div>
                    </div>
                </div>
                <div class="vic-section">
                    <div class="vic-empty">${t('vic.no_data')}</div>
                </div>
                <div class="vic-section">
                    <div class="vic-links-row">
                        <a class="vic-link" href="https://www.marinetraffic.com/en/ais/index/search/all?mmsi=${mmsi}" target="_blank" rel="noopener">MarineTraffic</a>
                        <a class="vic-link" href="https://www.vesselfinder.com/vessels/details/${mmsi}" target="_blank" rel="noopener">VesselFinder</a>
                        <button class="route-lookup-btn" onclick="MapModule.loadVesselRoute('${mmsi}'); document.getElementById('vesselInfoOverlay').remove(); return false;">${t('vic.show_track')}</button>
                    </div>
                </div>
            </div>`;
        overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
        document.body.appendChild(overlay);
        document.addEventListener('keydown', function handler(e) {
            if (e.key === 'Escape') { var el = document.getElementById('vesselInfoOverlay'); if (el) el.remove(); document.removeEventListener('keydown', handler); }
        });
    }

    function _buildInfoCard(sv) {
        // Remove any existing card
        const existing = document.getElementById('vesselInfoOverlay');
        if (existing) existing.remove();

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const flagName = getMidFlag(sv.mmsi);
        const vesselName = (sv.names && sv.names[0]) || sv.mmsi;
        const riskColor = riskColors[sv.risk_level] || '#ff3366';

        // ── Header ──
        const headerHtml = `
            <div class="vic-header">
                <div class="vic-header-left">
                    <div class="vic-vessel-name" style="color:${riskColor}">${vesselName}</div>
                    <div class="vic-vessel-meta">
                        MMSI: ${sv.mmsi}
                        ${flagName ? ' | ' + t('app.flag') + ' ' + flagName : ''}
                        ${sv.vessel_type ? ' | ' + t('vic.vessel_type') + ': ' + sv.vessel_type : ''}
                    </div>
                </div>
                <div class="vic-header-right">
                    <span class="risk-badge risk-${sv.risk_level}" style="font-size:11px;padding:3px 8px">${(sv.risk_level || '').toUpperCase()}</span>
                </div>
            </div>`;

        // ── ITU MARS Registry ──
        let marsHtml = '';
        const marsDetails = sv.itu_mars_details || {};
        const marsRec = marsDetails.mars_record;
        const mismatches = marsDetails.mismatches || [];
        const mismatchFields = new Set(mismatches.map(m => m.field));

        if (marsRec && marsRec.found !== false) {
            const rows = [
                { label: t('vic.mars_name'), value: marsRec.ship_name || '-', field: 'ship_name' },
                { label: t('vic.mars_cs'), value: marsRec.call_sign || '-', field: 'call_sign' },
                { label: t('vic.mars_imo'), value: marsRec.imo_number || '-', field: 'imo_number' },
                { label: t('vic.mars_flag'), value: marsRec.administration || '-', field: 'administration' },
                { label: t('vic.mars_updated'), value: marsRec.update_date || '-', field: null },
            ];
            let rowsHtml = rows.map(r => {
                const isMismatch = r.field && mismatchFields.has(r.field);
                const mismatchInfo = isMismatch
                    ? mismatches.find(m => m.field === r.field)
                    : null;
                const mismatchNote = mismatchInfo
                    ? `<span class="vic-mismatch">${t('vic.mismatch')}: ${t('vic.ais_vs_mars')} ${Array.isArray(mismatchInfo.ais) ? mismatchInfo.ais.join(', ') : mismatchInfo.ais}</span>`
                    : '';
                return `<div class="vic-row${isMismatch ? ' vic-row-warn' : ''}">
                    <span class="vic-label">${r.label}</span>
                    <span class="vic-value">${r.value}${mismatchNote}</span>
                </div>`;
            }).join('');
            marsHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.registry')}</div>
                ${rowsHtml}
            </div>`;
        } else {
            marsHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.registry')}</div>
                <div class="vic-empty">${t('vic.no_mars')}</div>
            </div>`;
        }

        // ── Risk Score Breakdown ──
        const score = sv.risk_score || 0;
        const maxScore = 20;
        const pct = Math.min(100, Math.round((score / maxScore) * 100));
        const flagsList = (sv.flags || []).map(f => `<div class="vic-flag-item">• ${f}</div>`).join('');
        const scoreHtml = `<div class="vic-section">
            <div class="vic-section-title">${t('vic.risk_score')}: ${score} / ${(sv.risk_level || '').toUpperCase()}</div>
            <div class="vic-score-bar-wrap">
                <div class="vic-score-bar" style="width:${pct}%;background:${riskColor}"></div>
            </div>
            ${sv.type_multiplier != null ? `<div class="vic-row"><span class="vic-label">${t('vic.multiplier')}</span><span class="vic-value">×${sv.type_multiplier}</span></div>` : ''}
            <div class="vic-flags-list">${flagsList || '-'}</div>
        </div>`;

        // ── AIS Anomalies ──
        let anomaliesHtml = '';
        const anomalies = sv.ais_anomalies || [];
        if (anomalies.length > 0) {
            const items = anomalies.map(a => {
                const desc = a.description || a.type || '';
                const severity = a.severity ? ` <span class="vic-severity-${a.severity}">[${a.severity}]</span>` : '';
                return `<div class="vic-anomaly-item">${desc}${severity}</div>`;
            }).join('');
            anomaliesHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.anomalies')}</div>
                ${items}
            </div>`;
        } else {
            anomaliesHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.anomalies')}</div>
                <div class="vic-empty">${t('vic.no_anomalies')}</div>
            </div>`;
        }

        // ── Cable Activity ──
        let cableHtml = '';
        const cd = sv.cable_details;
        if (cd && (sv.cable_proximity || sv.cable_loitering)) {
            cableHtml = `<div class="vic-section">
                <div class="vic-section-title">${t('vic.cable')}</div>
                <div class="vic-row"><span class="vic-label">${t('vic.nearest_cable')}</span><span class="vic-value">${cd.nearest_cable || '-'}</span></div>
                <div class="vic-row"><span class="vic-label">${t('vic.min_dist')}</span><span class="vic-value">${cd.min_distance_km != null ? cd.min_distance_km.toFixed(1) + ' km' : '-'}</span></div>
                <div class="vic-row"><span class="vic-label">${t('vic.loiter_hrs')}</span><span class="vic-value">${cd.loiter_hours != null ? cd.loiter_hours.toFixed(1) + ' h' : '-'}</span></div>
            </div>`;
        }

        // ── External Links ──
        const mtUrl = 'https://www.marinetraffic.com/en/ais/index/search/all?mmsi=' + sv.mmsi;
        const vfUrl = 'https://www.vesselfinder.com/vessels/details/' + sv.mmsi;
        const linksHtml = `<div class="vic-section">
            <div class="vic-section-title">${t('vic.links')}</div>
            <div class="vic-links-row">
                <a class="vic-link" href="${mtUrl}" target="_blank" rel="noopener">MarineTraffic</a>
                <a class="vic-link" href="${vfUrl}" target="_blank" rel="noopener">VesselFinder</a>
                <button class="route-lookup-btn" onclick="MapModule.loadVesselRoute('${sv.mmsi}'); document.getElementById('vesselInfoOverlay').remove(); return false;">${t('vic.show_track')}</button>
            </div>
        </div>`;

        // ── Assemble card ──
        const overlay = document.createElement('div');
        overlay.id = 'vesselInfoOverlay';
        overlay.className = 'vessel-info-overlay';
        overlay.innerHTML = `
            <div class="vessel-info-card">
                <button class="vic-close" onclick="document.getElementById('vesselInfoOverlay').remove()" title="${t('vic.close')}">✕</button>
                ${headerHtml}
                ${marsHtml}
                ${scoreHtml}
                ${anomaliesHtml}
                ${cableHtml}
                ${linksHtml}
            </div>`;

        // Click overlay background to close
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) overlay.remove();
        });

        document.body.appendChild(overlay);

        // ESC to close
        const escHandler = function(e) {
            if (e.key === 'Escape') {
                const el = document.getElementById('vesselInfoOverlay');
                if (el) el.remove();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    return {
        init,
        drawFishingHotspots,
        displayVessels,
        renderVesselsForZoom,
        displayDarkVessels,
        displaySuspiciousVessels,
        displayGovVessels,
        toggleLayer,
        focusVessel,
        focusPosition,
        loadSubmarineCables,
        loadCableFaultStatus,
        getCableFaultStatus,
        loadVesselRoute,
        clearVesselRoute,
        searchVesselRoute,
        snapshotMap,
        setFilterFoc,
        locateVesselType,
        drawTerritorialBaseline,
        setSuspiciousData,
        showVesselInfoCard,
        getMidFlag,
        getGovType,
        govLabel,
        GOV_BADGE_ICON,
        GOV_TYPES,
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

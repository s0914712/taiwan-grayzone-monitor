/**
 * Taiwan Gray Zone Monitor - Vessel Rendering Module
 * AIS vessel markers (cluster + detail), dark/suspicious/gov layers, vessel info cards.
 * Factory invoked by map.js init() once the Leaflet map + layer groups exist.
 * Load order (HTML): map-data.js → map-baseline.js → map-vessels.js →
 * map-routes.js → map-cables.js → map.js
 */

var MapVesselsFactory = function(map, layers) {
    'use strict';
    const { riskColors, getMidFlag, CLUSTER_ZOOM_THRESHOLD, CLUSTER_CENTERS, FISHING_HOTSPOTS, VESSEL_COLORS, GOV_TYPES, GOV_BADGE_ICON, getGovType, govLabel, FOC_MIDS, FOC_COMMERCIAL_TYPES, REGION_COLORS, REGION_NAMES, _decodeNavStatus, createVesselIcon } = MapData;

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
     * Focus on a specific vessel
     */
    function focusVessel(mmsi, vessels) {
        const v = vessels.get(mmsi);
        if (v && vesselMarkers[mmsi]) {
            map.flyTo([v.lat, v.lon], 10);
            vesselMarkers[mmsi].openPopup();
        }
    }

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
        drawFishingHotspots,
        loadSanctionsList,
        displayVessels,
        computeVesselStats,
        renderVesselsForZoom,
        displayDarkVessels,
        displaySuspiciousVessels,
        displayGovVessels,
        focusVessel,
        locateVesselType,
        setSuspiciousData,
        setFilterFoc,
        showVesselInfoCard,
    };
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapVesselsFactory;
}

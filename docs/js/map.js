/**
 * Taiwan Gray Zone Monitor - Map Module
 * Handles Leaflet map initialization and vessel/zone rendering
 */

const MapModule = (function() {
    'use strict';

    let map;
    let layers = { 
        drillZones: null, 
        fishingHotspots: null, 
        vessels: null 
    };
    let vesselMarkers = {};

    // Drill zone definitions
    const DRILL_ZONES = {
        north: { 
            name: '北部海域', 
            color: '#00f5ff', 
            coords: [[25.5, 121.0], [25.5, 122.5], [26.8, 122.5], [26.8, 121.0]] 
        },
        east: { 
            name: '東部海域', 
            color: '#ff6b35', 
            coords: [[23.0, 122.5], [23.0, 125.0], [25.5, 125.0], [25.5, 122.5]] 
        },
        south: { 
            name: '南部海域', 
            color: '#00ff88', 
            coords: [[21.5, 119.0], [21.5, 121.0], [23.0, 121.0], [23.0, 119.0]] 
        },
        west: { 
            name: '西部海域', 
            color: '#ffd700', 
            coords: [[23.5, 118.5], [23.5, 120.0], [25.0, 120.0], [25.0, 118.5]] 
        }
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
        layers.drillZones = L.layerGroup().addTo(map);
        layers.fishingHotspots = L.layerGroup().addTo(map);
        layers.vessels = L.layerGroup().addTo(map);

        // Draw Taiwan outline
        drawTaiwanOutline();

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
     * Draw drill zones on the map
     */
    function drawDrillZones() {
        layers.drillZones.clearLayers();

        Object.entries(DRILL_ZONES).forEach(([key, zone]) => {
            const polygon = L.polygon(zone.coords, {
                color: zone.color,
                weight: 2,
                opacity: 0.6,
                fillColor: zone.color,
                fillOpacity: 0.08,
                dashArray: '6, 4'
            }).addTo(layers.drillZones);

            polygon.bindTooltip(zone.name, { permanent: false, direction: 'center' });
            polygon.on('click', () => map.flyToBounds(zone.coords, { padding: [30, 30] }));
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
     * Display vessels on the map
     */
    function displayVessels(vesselList, vessels = new Map()) {
        layers.vessels.clearLayers();
        vesselMarkers = {};

        let stats = { total: 0, fishing: 0, cargo: 0, tanker: 0, inZone: 0, suspicious: 0 };
        let zoneCounts = { north: 0, east: 0, south: 0, west: 0 };

        vesselList.forEach(v => {
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

            const marker = L.circleMarker([v.lat, v.lon], {
                radius: isSuspicious ? 6 : 4,
                fillColor: color,
                color: isSuspicious ? '#ffffff' : color,
                weight: isSuspicious ? 2 : 1,
                opacity: 0.9,
                fillOpacity: isSuspicious ? 1 : 0.7
            }).addTo(layers.vessels);

            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            const suspiciousInfo = isSuspicious
                ? `<br><b style="color:#ff3366">${t('app.csis_suspicious')}</b>`
                : '';

            marker.bindPopup(`
                <b>${v.name || 'Unknown'}</b><br>
                ${t('app.mmsi')} ${v.mmsi}<br>
                ${t('app.type')} ${v.type_name || t('common.unknown')}<br>
                ${t('app.speed')} ${(v.speed || 0).toFixed(1)} kn${suspiciousInfo}
            `);

            vesselMarkers[v.mmsi] = marker;

            // Count by type
            if (v.type_name === 'fishing') stats.fishing++;
            if (v.type_name === 'cargo') stats.cargo++;
            if (v.type_name === 'tanker') stats.tanker++;

            // Check zone
            const zone = v.in_drill_zone || getZoneForPosition(v.lat, v.lon);
            if (zone) {
                stats.inZone++;
                zoneCounts[zone]++;
            }
        });

        return { stats, zoneCounts, vessels };
    }

    /**
     * Display dark vessels on the map
     */
    function displayDarkVessels(darkData) {
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
                }).addTo(layers.vessels).bindPopup(
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
     * Get which zone a position is in
     */
    function getZoneForPosition(lat, lon) {
        for (const [key, zone] of Object.entries(DRILL_ZONES)) {
            const [sw, , ne] = [zone.coords[0], zone.coords[1], zone.coords[2]];
            const minLat = Math.min(sw[0], ne[0]);
            const maxLat = Math.max(sw[0], ne[0]);
            const minLon = Math.min(sw[1], ne[1]);
            const maxLon = Math.max(sw[1], ne[1]);

            if (lat >= minLat && lat <= maxLat && lon >= minLon && lon <= maxLon) {
                return key;
            }
        }
        return null;
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
     * Focus on a specific zone
     */
    function focusZone(key) {
        const zone = DRILL_ZONES[key];
        if (zone) {
            map.flyToBounds(zone.coords, { padding: [30, 30] });
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

    // Public API
    return {
        init,
        drawDrillZones,
        drawFishingHotspots,
        displayVessels,
        displayDarkVessels,
        displaySuspiciousVessels,
        toggleLayer,
        focusZone,
        focusVessel,
        focusPosition,
        getZoneForPosition,
        DRILL_ZONES,
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

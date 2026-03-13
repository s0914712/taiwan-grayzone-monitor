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
        submarineCables: null
    };
    let vesselMarkers = {};

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
        layers.fishingHotspots = L.layerGroup().addTo(map);
        layers.vessels = L.layerGroup().addTo(map);
        layers.submarineCables = L.layerGroup();

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
        const size = isSuspicious ? 16 : 12;
        const half = size / 2;
        const rotation = heading !== null && heading !== undefined ? heading : 0;
        const opacity = isSuspicious ? 0.85 : 0.6;

        let shape;
        if (heading !== null && heading !== undefined) {
            // Triangle pointing up, rotated by heading
            shape = '<polygon points="' + half + ',1 ' + (size - 1) + ',' + (size - 1) + ' 1,' + (size - 1) + '" ' +
                    'fill="' + color + '" fill-opacity="' + opacity + '" ' +
                    'stroke="' + color + '" stroke-width="' + (isSuspicious ? 1.5 : 0.8) + '" stroke-opacity="0.9"/>';
        } else {
            // Diamond for unknown heading
            shape = '<polygon points="' + half + ',1 ' + (size - 1) + ',' + half + ' ' + half + ',' + (size - 1) + ' 1,' + half + '" ' +
                    'fill="' + color + '" fill-opacity="' + opacity + '" ' +
                    'stroke="' + color + '" stroke-width="' + (isSuspicious ? 1.5 : 0.8) + '" stroke-opacity="0.9"/>';
        }

        const svg = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '" ' +
                    'xmlns="http://www.w3.org/2000/svg" ' +
                    'style="transform:rotate(' + rotation + 'deg)">' +
                    shape + '</svg>';

        return L.divIcon({
            html: svg,
            className: 'vessel-icon',
            iconSize: [size, size],
            iconAnchor: [half, half],
            popupAnchor: [0, -half]
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

            marker.bindPopup(`
                <b>${v.name || 'Unknown'}</b><br>
                ${t('app.mmsi')} ${v.mmsi}<br>
                ${t('app.type')} ${v.type_name || t('common.unknown')}<br>
                ${t('app.speed')} ${(v.speed || 0).toFixed(1)} kn<br>
                航向: ${headingText}${suspiciousInfo}
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
    return {
        init,
        drawFishingHotspots,
        displayVessels,
        displayDarkVessels,
        displaySuspiciousVessels,
        toggleLayer,
        focusVessel,
        focusPosition,
        loadSubmarineCables,
        loadCableFaultStatus,
        getCableFaultStatus,
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

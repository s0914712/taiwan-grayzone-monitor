/**
 * Taiwan Gray Zone Monitor - Territorial Baseline Module
 * Territorial sea baseline / 12nm / 24nm contiguous zone rendering.
 * Factory invoked by map.js init() once the Leaflet map + layer groups exist.
 * Load order (HTML): map-data.js → map-baseline.js → map-vessels.js →
 * map-routes.js → map-cables.js → map.js
 */

var MapBaselineFactory = function(map, layers) {
    'use strict';
    const { TERRITORIAL_BASEPOINT_MARKERS, offsetPolygonNm } = MapData;

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
    return {
        drawTerritorialBaseline,
    };
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapBaselineFactory;
}

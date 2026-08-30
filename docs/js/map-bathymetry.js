/**
 * Taiwan Gray Zone Monitor - Seascape Bathymetry Module
 * Global seabed depth shading from the Seascape tileset
 * (https://github.com/openwatersio/seascape, tiles CC BY 4.0
 * © Open Water Software, LLC).
 *
 * Seascape publishes Terrarium-encoded raster DEM tiles (depth per pixel),
 * not pre-shaded imagery, so this module decodes each tile on a canvas and
 * applies a depth color-relief. The colormap has a deliberate break at 200 m
 * to make the continental shelf edge readable — the same 200 m contour used
 * by the threat-scoring engine (criterion 3).
 *
 * The tile URL template, zoom range and attribution come from the TileJSON
 * endpoint at runtime; nothing is hardcoded beyond the endpoint itself.
 * Not for navigation — depths are approximate (see Seascape README).
 *
 * Factory invoked by map.js init() once the Leaflet map + layer groups exist.
 * Load order (HTML): map-data.js → map-baseline.js → map-vessels.js →
 * map-routes.js → map-cables.js → map-bathymetry.js → map.js
 */

var MapBathymetryFactory = function(map, layers) {
    'use strict';

    var TILEJSON_URL = 'https://tiles.openwaters.io/seascape/raster.json';
    var FALLBACK_ATTRIBUTION = '&copy; <a href="https://openwaters.io/charts/seascape#license">Open Water Software, LLC</a>';
    var SHELF_BREAK_M = 200;   // color discontinuity at the shelf edge
    var PIXEL_ALPHA = 210;     // per-pixel alpha; combined with layer opacity below
    var LAYER_OPACITY = 0.7;

    // Depth colormap stops (depth in metres below sea level → [r,g,b]).
    // Two stops at the shelf break create a visible jump instead of a smooth
    // gradient, so the 200 m contour reads as an edge on the dark basemap.
    var DEPTH_STOPS = [
        [0,               [130, 205, 255]],
        [SHELF_BREAK_M,   [ 75, 155, 230]],  // shallow side of the break
        [SHELF_BREAK_M + 0.001, [ 35,  90, 170]],  // deep side of the break
        [1000,            [ 22,  58, 125]],
        [3000,            [ 12,  32,  80]],
        [6000,            [  5,  14,  45]],
        [11000,           [  2,   6,  24]]
    ];

    var loaded = false;    // TileJSON fetched + grid layer created
    var loading = null;    // in-flight promise
    var attributionControl = null;
    var attributionText = FALLBACK_ATTRIBUTION;

    /**
     * Decode a Terrarium-encoded RGB pixel to elevation in metres
     * (negative below sea level).
     */
    function decodeTerrarium(r, g, b) {
        return (r * 256 + g + b / 256) - 32768;
    }

    /**
     * Map a depth (metres below sea level, positive) to [r,g,b,a].
     * Depths ≤0 (land / dry) are fully transparent.
     */
    function depthColor(depth) {
        if (!(depth > 0)) return [0, 0, 0, 0];
        var stops = DEPTH_STOPS;
        if (depth >= stops[stops.length - 1][0]) {
            var last = stops[stops.length - 1][1];
            return [last[0], last[1], last[2], PIXEL_ALPHA];
        }
        for (var i = 1; i < stops.length; i++) {
            if (depth <= stops[i][0]) {
                var d0 = stops[i - 1][0], d1 = stops[i][0];
                var c0 = stops[i - 1][1], c1 = stops[i][1];
                var t = d1 === d0 ? 0 : (depth - d0) / (d1 - d0);
                return [
                    Math.round(c0[0] + (c1[0] - c0[0]) * t),
                    Math.round(c0[1] + (c1[1] - c0[1]) * t),
                    Math.round(c0[2] + (c1[2] - c0[2]) * t),
                    PIXEL_ALPHA
                ];
            }
        }
        return [0, 0, 0, 0];
    }

    /**
     * Paint a loaded Terrarium DEM tile image onto the canvas as color-relief.
     */
    function renderDepthTile(img, canvas) {
        var ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        var imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        var px = imageData.data;
        for (var i = 0; i < px.length; i += 4) {
            var elevation = decodeTerrarium(px[i], px[i + 1], px[i + 2]);
            var c = depthColor(-elevation);
            px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = c[3];
        }
        ctx.putImageData(imageData, 0, 0);
    }

    // Custom GridLayer: fetches Terrarium PNG tiles and renders them decoded.
    // zoomOffset is applied manually (GridLayer, unlike TileLayer, has none):
    // Seascape serves 512px tiles, so tile z = map z - 1.
    var TerrariumDepthLayer = L.GridLayer.extend({
        createTile: function(coords, done) {
            var canvas = document.createElement('canvas');
            var size = this.getTileSize();
            canvas.width = size.x;
            canvas.height = size.y;
            var tz = coords.z + (this.options.zoomOffset || 0);
            var url = this.options.tileUrlTemplate
                .replace('{z}', tz)
                .replace('{x}', coords.x)
                .replace('{y}', coords.y);
            var img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = function() {
                try {
                    renderDepthTile(img, canvas);
                    done(null, canvas);
                } catch (e) {
                    done(e, canvas);
                }
            };
            // Missing tiles (land-only / outside coverage) stay transparent
            img.onerror = function() { done(null, canvas); };
            img.src = url;
            return canvas;
        }
    });

    /**
     * Lazy-load the bathymetry layer: fetch the TileJSON, build the decoding
     * grid layer, and put it in layers.bathymetry. Resolves true on success.
     */
    function loadBathymetry() {
        if (loaded) return Promise.resolve(true);
        if (loading) return loading;
        loading = (async function() {
            try {
                var res = await fetch(TILEJSON_URL);
                if (!res.ok) throw new Error('TileJSON HTTP ' + res.status);
                var tj = await res.json();
                if (!tj.tiles || !tj.tiles.length) throw new Error('TileJSON has no tiles');
                if (tj.attribution) attributionText = tj.attribution;

                var tileSize = tj.tileSize || 512;
                var zoomOffset = tileSize === 512 ? -1 : 0;
                if (!map.getPane('bathymetry')) {
                    // Above the basemap tiles (200), below vector overlays (400)
                    map.createPane('bathymetry').style.zIndex = 250;
                }
                var grid = new TerrariumDepthLayer({
                    pane: 'bathymetry',
                    tileUrlTemplate: tj.tiles[0],
                    tileSize: tileSize,
                    zoomOffset: zoomOffset,
                    opacity: LAYER_OPACITY,
                    minNativeZoom: (tj.minzoom || 0) - zoomOffset,
                    maxNativeZoom: (tj.maxzoom || 14) - zoomOffset,
                    bounds: tj.bounds ? L.latLngBounds(
                        [tj.bounds[1], tj.bounds[0]], [tj.bounds[3], tj.bounds[2]]) : undefined
                });
                grid.addTo(layers.bathymetry);
                loaded = true;
                return true;
            } catch (e) {
                console.error('Bathymetry load failed:', e);
                loading = null;  // allow retry on next toggle
                return false;
            }
        })();
        return loading;
    }

    // CC BY 4.0 requires visible attribution while the tiles are shown. A
    // dedicated control (separate from the basemap's Esri attribution) is added
    // only while the bathymetry layer group is on the map.
    map.on('layeradd', function(e) {
        if (e.layer !== layers.bathymetry) return;
        if (!attributionControl) {
            attributionControl = L.control.attribution({ prefix: false, position: 'bottomright' });
        }
        attributionControl.addTo(map);
        attributionControl.addAttribution(attributionText);
    });
    map.on('layerremove', function(e) {
        if (e.layer !== layers.bathymetry) return;
        if (attributionControl) attributionControl.remove();
    });

    return {
        loadBathymetry,
        // pure helpers exposed for tests
        decodeTerrarium,
        depthColor
    };
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapBathymetryFactory;
}

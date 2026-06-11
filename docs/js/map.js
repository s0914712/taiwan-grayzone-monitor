/**
 * Taiwan Gray Zone Monitor - Map Module (core)
 * Leaflet map initialization, layer groups, and public API assembly.
 *
 * The heavy lifting lives in sibling factory modules, instantiated by
 * init() once the map + layer groups exist:
 *   map-data.js     — static lookup tables + pure helpers (MapData)
 *   map-baseline.js — territorial baseline / 12nm / 24nm (MapBaselineFactory)
 *   map-vessels.js  — vessel/dark/suspicious/gov rendering (MapVesselsFactory)
 *   map-routes.js   — route loading + track panel + snapshot (MapRoutesFactory)
 *   map-cables.js   — submarine cable layer + fault status (MapCablesFactory)
 * Load order in HTML must follow the list above, ending with this file.
 */

const MapModule = (function() {
    'use strict';
    const { getMidFlag, getGovType, govLabel, GOV_BADGE_ICON, GOV_TYPES,
            FISHING_HOTSPOTS, VESSEL_COLORS, REGION_COLORS, REGION_NAMES,
            CLUSTER_ZOOM_THRESHOLD, debounce } = MapData;

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

    // Sub-module instances (created in init once map + layers exist)
    let _vessels = null;
    let _routes = null;
    let _cables = null;
    let _baseline = null;

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

        // Instantiate sub-modules now that map + layers exist
        _vessels = MapVesselsFactory(map, layers);
        _routes = MapRoutesFactory(map, layers);
        _cables = MapCablesFactory(map, layers);
        _baseline = MapBaselineFactory(map, layers);

        // Zoom/move events for cluster <-> detail transitions
        // (renderVesselsForZoom is a no-op while the vessel cache is empty)
        map.on('zoomend', () => {
            _vessels.renderVesselsForZoom();
        });
        map.on('moveend', () => {
            if (map.getZoom() > CLUSTER_ZOOM_THRESHOLD) {
                _vessels.renderVesselsForZoom();
            }
        });

        // Bind Enter key on MMSI search input
        var mmsiInput = document.getElementById('mmsiSearchInput');
        if (mmsiInput) {
            mmsiInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') _routes.searchVesselRoute();
            });
            // Debounced auto-search: fire only on full 9-digit MMSI
            // (5-8 digit MMSIs still searchable via Enter / the 查詢 button)
            mmsiInput.addEventListener('input', debounce(function() {
                var v = mmsiInput.value.trim();
                if (/^\d{9}$/.test(v)) _routes.loadVesselRoute(v);
            }, 600));
        }

        // Load UN sanctions list for vessel warnings
        _vessels.loadSanctionsList();

        return map;
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
     * Focus on coordinates
     */
    function focusPosition(lat, lon, zoom = 10) {
        if (lat && lon) {
            map.flyTo([lat, lon], zoom);
        }
    }

    // Public API — delegates to sub-modules created in init()
    return {
        init,
        toggleLayer,
        focusPosition,
        // Vessel rendering
        drawFishingHotspots: (...a) => _vessels.drawFishingHotspots(...a),
        displayVessels: (...a) => _vessels.displayVessels(...a),
        renderVesselsForZoom: (...a) => _vessels.renderVesselsForZoom(...a),
        displayDarkVessels: (...a) => _vessels.displayDarkVessels(...a),
        displaySuspiciousVessels: (...a) => _vessels.displaySuspiciousVessels(...a),
        displayGovVessels: (...a) => _vessels.displayGovVessels(...a),
        focusVessel: (...a) => _vessels.focusVessel(...a),
        locateVesselType: (...a) => _vessels.locateVesselType(...a),
        setSuspiciousData: (...a) => _vessels.setSuspiciousData(...a),
        setFilterFoc: (...a) => _vessels.setFilterFoc(...a),
        showVesselInfoCard: (...a) => _vessels.showVesselInfoCard(...a),
        // Routes
        loadVesselRoute: (...a) => _routes.loadVesselRoute(...a),
        clearVesselRoute: (...a) => _routes.clearVesselRoute(...a),
        searchVesselRoute: (...a) => _routes.searchVesselRoute(...a),
        snapshotMap: (...a) => _routes.snapshotMap(...a),
        // Submarine cables
        loadSubmarineCables: (...a) => _cables.loadSubmarineCables(...a),
        loadCableFaultStatus: (...a) => _cables.loadCableFaultStatus(...a),
        getCableFaultStatus: (...a) => _cables.getCableFaultStatus(...a),
        // Territorial baseline
        drawTerritorialBaseline: (...a) => _baseline.drawTerritorialBaseline(...a),
        // MapData passthroughs (kept on MapModule for backward compatibility)
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

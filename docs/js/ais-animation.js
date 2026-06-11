/**
 * Taiwan Gray Zone Monitor - AIS track playback animation page logic
 * Extracted verbatim from the page's former inline <script> block.
 * Loaded with defer after i18n.js; bootstraps on DOMContentLoaded.
 */

    // =========================================================================
    // Fishing hotspots (same as fetch_ais_data.py)
    // =========================================================================
    // Vessel type colors (MarineTraffic-style, matches docs/js/map.js)
    const VESSEL_COLORS = {
        fishing: '#00ff88',
        cargo:   '#00f5ff',
        tanker:  '#ff5e8a',   // rose — off the warm severity ramp
        lng:     '#c8ff3d',   // lime — clears the cable/severity yellows
        other:   '#ff3366',
        unknown: '#8aa4c8'
    };

    // Distinct, eye-catching colour reserved for anchoring vessels (nav status
    // "at anchor" / "moored"). Deliberately unused by any vessel type so the
    // anchor state stands out on the dark map.
    const ANCHOR_COLOR = '#19b6ff';

    // Per-risk-level colours for high-risk vessel annotations (warm severity ramp,
    // mirrors index.html / --sev-* in main.css)
    const RISK_COLORS = { critical: '#ff2d55', high: '#ff7847', medium: '#ffab2e', normal: '#8aa4c8' };

    const FISHING_HOTSPOTS = {
        taiwan_bank:   { name: '台灣灘漁場',   bounds: [[22.0, 117.0], [23.5, 119.5]] },
        penghu:        { name: '澎湖漁場',     bounds: [[23.0, 119.0], [24.0, 120.0]] },
        kuroshio_east: { name: '東部黑潮漁場', bounds: [[22.5, 121.0], [24.5, 122.0]] },
        northeast:     { name: '東北漁場',     bounds: [[24.8, 121.5], [25.8, 123.0]] },
        southwest:     { name: '西南沿岸漁場', bounds: [[22.0, 120.0], [23.0, 120.8]] }
    };

    // =========================================================================
    // Animation state
    // =========================================================================
    const Anim = {
        allFrames: [],
        filteredFrames: [],
        currentIdx: 0,
        playing: false,
        speed: 1,
        intervalId: null,
        selectedRange: 14,
        markerLayer: null,
        trailLayer: null,
        showTrails: true,
        vesselFilter: 'all',  // 'all', 'cn_fishing', 'suspicious'
        chart: null,
        vesselTrails: {},  // mmsi -> [{lat, lon, timestamp}, ...]
        eventLayer: null,  // pulsing gray-zone event markers
        showEvents: true,
        loop: false,
        focusMmsi: null,   // single-vessel focus / case narrative
        // Researcher tools
        showDark: false,
        darkThresholdHrs: 24,
        drawMode: null,    // null | 'area' | 'line'
        drawPts: [],       // vertices being drawn
        aoi: null,         // [[lat,lon],...] closed polygon ring
        tripwire: null,    // [[lat,lon],[lat,lon]]
        drawLayer: null,   // Leaflet layer for AOI/tripwire + preview
        aoiChart: null
    };

    // External-event correlation context (PLA sorties, named exercises)
    const Context = {
        byDate: {},        // 'YYYY-MM-DD' -> {sorties, naval, dark}
        exercises: [],     // [{exercise, trigger, start, end}]
        maxSorties: 1
    };

    let map;

    // =========================================================================
    // Gray zone events (STS transfers, identity changes, cable faults) +
    // vessel threat profiles. Loaded once, mapped onto the animation timeline.
    // =========================================================================
    const Gray = {
        events: [],            // [{type, ts, lat, lon, mmsis:[], label, detail, color, frameIdx}]
        profiles: new Map(),   // mmsi -> suspicious_analysis profile
        suspiciousMmsis: new Set(), // mmsi (string) of vessels flagged suspicious by analyze_suspicious.py
        loaded: false
    };

    // Track-history vessels no longer carry a `suspicious` flag (detection moved
    // to analyze_suspicious.py → data.json). Resolve suspicion from that set.
    function isSuspicious(mmsi) {
        return Gray.suspiciousMmsis.has(String(mmsi));
    }

    // Gray-zone event colours follow the threat-priority ramp (see PRIORITY map):
    // cable_fault (highest) → critical red, sts → high coral, identity → medium amber.
    const EVENT_COLORS = { sts: '#ff7847', identity: '#ffab2e', cable_fault: '#ff2d55' };

    // Reuse backend exclusion logic: skip buoy / fishing-net beacon noise
    function isBeaconNoise(mmsi, name) {
        const m = String(mmsi || '');
        if (m.startsWith('9') || m.startsWith('898')) return true;
        const n = String(name || '').trim().toUpperCase();
        if (n.includes('%') || n.includes('BUOY')) return true;
        // Fishing-net beacon voltage suffix (e.g. "12.5V") — mirrors backend EXCLUSION_RULES
        if (/\d+\.?\d*V$/.test(n)) return true;
        return false;
    }

    // =========================================================================
    // Cable & Detection state
    // =========================================================================
    const Cable = {
        geoData: null,          // GeoJSON FeatureCollection
        layer: null,            // Leaflet GeoJSON layer
        bufferLayer: null,      // Leaflet layer for buffer zones
        bufferNm: 2,            // buffer distance in nautical miles
        visible: false,
        lines: [],              // pre-extracted [[lat,lon], ...] per segment
        faultStatus: null       // { faultedSlugs: Set, faultsBySlug: {} }
    };

    // Detection results per frame (cached)
    const Detection = {
        vesselIndex: null,      // Map<mmsi, [{lat, lon, speed, heading, frameIdx}, ...]>
        loiteringSet: null,     // Set<mmsi> - vessels loitering >6hr
        zigzagSet: null,        // Set<mmsi> - vessels with zigzag heading
        nearCableSet: null,     // Set<mmsi> - vessels near cable at current frame
        anchoredSet: null,      // Set<mmsi> - vessels that were ever anchoring/moored
        darkInfo: null,         // Map<mmsi, {count, maxGapHrs, segments}> - going-dark gaps
        lastBuiltFrame: -1,
        lastCableFrame: -1
    };

    // =========================================================================
    // Cable data loading & rendering
    // =========================================================================
    async function loadCableData() {
        if (Cable.geoData) return;
        try {
            const [cableRes, statusRes] = await Promise.all([
                fetch('taiwan_cables.json?' + Date.now()),
                fetch('cable_status.json?' + Date.now()).catch(() => null)
            ]);
            if (!cableRes.ok) throw new Error('HTTP ' + cableRes.status);
            Cable.geoData = await cableRes.json();

            // Load fault status
            if (statusRes && statusRes.ok) {
                try {
                    const statusData = await statusRes.json();
                    const faultedSlugs = new Set();
                    const faultsBySlug = {};
                    (statusData.faults || []).forEach(f => {
                        if (f.status === 'fault') {
                            faultedSlugs.add(f.slug);
                            if (!faultsBySlug[f.slug]) faultsBySlug[f.slug] = [];
                            faultsBySlug[f.slug].push(f);
                        }
                    });
                    Cable.faultStatus = { faultedSlugs, faultsBySlug };
                } catch (_) {}
            }

            // Pre-extract line segments as [lat, lon] arrays for distance calc
            Cable.lines = [];
            Cable.geoData.features.forEach(f => {
                const coords = f.geometry.coordinates;
                if (f.geometry.type === 'MultiLineString') {
                    coords.forEach(line => {
                        Cable.lines.push(line.map(c => [c[1], c[0]])); // [lat, lon]
                    });
                } else if (f.geometry.type === 'LineString') {
                    Cable.lines.push(coords.map(c => [c[1], c[0]]));
                }
            });
        } catch (e) {
            console.error('Cable data load failed:', e);
        }
    }

    function renderCableLayer() {
        if (!Cable.geoData || !map) return;
        if (Cable.layer) { map.removeLayer(Cable.layer); Cable.layer = null; }
        if (Cable.bufferLayer) { map.removeLayer(Cable.bufferLayer); Cable.bufferLayer = null; }

        if (!Cable.visible) return;

        const faulted = Cable.faultStatus ? Cable.faultStatus.faultedSlugs : new Set();
        const faultDetails = Cable.faultStatus ? Cable.faultStatus.faultsBySlug : {};

        Cable.layer = L.geoJSON(Cable.geoData, {
            style: f => {
                const slug = f.properties.slug || '';
                const isFaulted = faulted.has(slug);
                return {
                    color: isFaulted ? '#ff2d55' : '#' + (f.properties.color || 'ffd700'),
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
                    tip = '<b style="color:#ff2d55">' + name + '</b><br>' + details;
                }
                layer.bindTooltip(tip, { sticky: true, className: 'cable-tooltip' });
            }
        }).addTo(map);

        // Render buffer zones as semi-transparent corridors
        renderCableBuffers();
    }

    function renderCableBuffers() {
        if (Cable.bufferLayer) { map.removeLayer(Cable.bufferLayer); Cable.bufferLayer = null; }
        if (!Cable.visible || Cable.bufferNm <= 0) return;

        Cable.bufferLayer = L.layerGroup().addTo(map);
        const bufferDeg = Cable.bufferNm / 60; // 1 nm ≈ 1/60 degree

        Cable.lines.forEach(line => {
            if (line.length < 2) return;
            // Simple corridor: offset line on both sides
            const left = [], right = [];
            for (let i = 0; i < line.length; i++) {
                const lat = line[i][0], lon = line[i][1];
                // Compute perpendicular direction
                let dx = 0, dy = 0;
                if (i < line.length - 1) {
                    dy = line[i + 1][0] - lat;
                    dx = line[i + 1][1] - lon;
                } else {
                    dy = lat - line[i - 1][0];
                    dx = lon - line[i - 1][1];
                }
                const len = Math.sqrt(dx * dx + dy * dy) || 1;
                const perpLat = -dx / len * bufferDeg;
                const perpLon = dy / len * bufferDeg;
                left.push([lat + perpLat, lon + perpLon]);
                right.push([lat - perpLat, lon - perpLon]);
            }
            const polygon = left.concat(right.reverse());
            L.polygon(polygon, {
                color: '#ffd700', weight: 0.5, opacity: 0.3,
                fillColor: '#ffd700', fillOpacity: 0.08,
                interactive: false
            }).addTo(Cable.bufferLayer);
        });
    }

    async function toggleCableLayer() {
        Cable.visible = document.getElementById('showCables').checked;
        document.getElementById('cableControls').style.display = Cable.visible ? 'flex' : 'none';
        if (Cable.visible && !Cable.geoData) {
            await loadCableData();
        }
        renderCableLayer();
        // Re-run near-cable detection if filter is active
        if (Anim.vesselFilter === 'near_cable') {
            Detection.lastCableFrame = -1;
            renderFrame(Anim.currentIdx);
        }
    }

    function updateCableBuffer() {
        Cable.bufferNm = parseFloat(document.getElementById('cableBuffer').value);
        document.getElementById('cableBufferVal').textContent = Cable.bufferNm;
        renderCableBuffers();
        // Re-run near-cable detection
        Detection.lastCableFrame = -1;
        if (Anim.vesselFilter === 'near_cable') {
            renderFrame(Anim.currentIdx);
        }
    }

    // =========================================================================
    // Detection engine
    // =========================================================================

    /**
     * Build vessel index: Map<mmsi, Array<{lat, lon, speed, heading, frameIdx}>>
     * Called once when filteredFrames change.
     */
    function buildVesselIndex() {
        if (Detection.vesselIndex) return; // already built
        Detection.vesselIndex = new Map();
        Anim.filteredFrames.forEach((frame, fi) => {
            if (!frame.vessels) return;
            frame.vessels.forEach(v => {
                if (!Detection.vesselIndex.has(v.mmsi)) {
                    Detection.vesselIndex.set(v.mmsi, []);
                }
                Detection.vesselIndex.get(v.mmsi).push({
                    lat: v.lat, lon: v.lon,
                    speed: v.speed || 0,
                    heading: v.heading !== undefined ? v.heading : null,
                    frameIdx: fi,
                    name: v.name, type_name: v.type_name,
                    anc: v.anc === 1,            // reported "at anchor" / "moored"
                    suspicious: isSuspicious(v.mmsi)
                });
            });
        });
    }

    /**
     * Detect loitering: vessel appears in 3+ consecutive frames (≈6hr+)
     * with speed < 1 kn and displacement < 0.01° (≈1km)
     */
    function detectLoitering() {
        if (Detection.loiteringSet) return Detection.loiteringSet;
        buildVesselIndex();
        const result = new Set();

        Detection.vesselIndex.forEach((records, mmsi) => {
            if (records.length < 3) return;
            // Sort by frameIdx
            records.sort((a, b) => a.frameIdx - b.frameIdx);
            let streak = 1;
            for (let i = 1; i < records.length; i++) {
                const prev = records[i - 1], curr = records[i];
                const isConsecutive = (curr.frameIdx - prev.frameIdx) <= 2; // allow 1 gap
                const lowSpeed = curr.speed < 1;
                const smallDisp = Math.abs(curr.lat - prev.lat) < 0.01 &&
                                  Math.abs(curr.lon - prev.lon) < 0.01;
                if (isConsecutive && lowSpeed && smallDisp) {
                    streak++;
                    if (streak >= 3) result.add(mmsi);
                } else {
                    streak = 1;
                }
            }
        });
        Detection.loiteringSet = result;
        return result;
    }

    /**
     * Detect zigzag: heading change > 30° for 3+ consecutive observations
     */
    function detectZigzag() {
        if (Detection.zigzagSet) return Detection.zigzagSet;
        buildVesselIndex();
        const result = new Set();

        Detection.vesselIndex.forEach((records, mmsi) => {
            if (records.length < 4) return;
            records.sort((a, b) => a.frameIdx - b.frameIdx);
            let zigCount = 0;
            for (let i = 1; i < records.length; i++) {
                const prev = records[i - 1], curr = records[i];
                if (prev.heading === null || curr.heading === null) { zigCount = 0; continue; }
                let diff = Math.abs(curr.heading - prev.heading);
                if (diff > 180) diff = 360 - diff;
                if (diff > 30) {
                    zigCount++;
                    if (zigCount >= 3) { result.add(mmsi); break; }
                } else {
                    zigCount = 0;
                }
            }
        });
        Detection.zigzagSet = result;
        return result;
    }

    /**
     * Detect anchoring vessels — those that were EVER at anchor/moored within the
     * visible range. Prefers the reported AIS navigational status (`anc` flag);
     * for older data without it, falls back to a sustained near-zero-speed,
     * near-stationary run (≥2 consecutive frames ≈ 4hr). Returns Set<mmsi>.
     */
    function detectAnchored() {
        if (Detection.anchoredSet) return Detection.anchoredSet;
        buildVesselIndex();
        const result = new Set();
        Detection.vesselIndex.forEach((records, mmsi) => {
            records.sort((a, b) => a.frameIdx - b.frameIdx);
            // Explicit reported anchoring status anywhere in the track
            if (records.some(r => r.anc)) { result.add(mmsi); return; }
            // Fallback: stayed put at near-zero speed for ≥2 consecutive frames
            let streak = 1;
            for (let i = 1; i < records.length; i++) {
                const prev = records[i - 1], curr = records[i];
                const isConsecutive = (curr.frameIdx - prev.frameIdx) <= 2;
                const nearZero = curr.speed < 0.5;
                const stationary = Math.abs(curr.lat - prev.lat) < 0.003 &&
                                   Math.abs(curr.lon - prev.lon) < 0.003;
                if (isConsecutive && nearZero && stationary) {
                    streak++;
                    if (streak >= 2) { result.add(mmsi); break; }
                } else {
                    streak = 1;
                }
            }
        });
        Detection.anchoredSet = result;
        return result;
    }

    /** Is this vessel anchoring at the given frame (reported anchor, or stopped & ever-anchored)? */
    function isAnchoredAt(mmsi, frameIdx) {
        const records = Detection.vesselIndex && Detection.vesselIndex.get(mmsi);
        if (!records) return false;
        const rec = records.find(r => r.frameIdx === frameIdx);
        if (!rec) return false;
        if (rec.anc) return true;
        return rec.speed < 0.5 && detectAnchored().has(mmsi);
    }

    /**
     * Detect "going dark": a vessel disappears for > threshold hours BETWEEN two
     * observed points (i.e. it later reappears). A vessel that simply stops being
     * seen at the end of its sequence is treated as "left the area", NOT going dark.
     * Returns Map<mmsi, {count, maxGapHrs, segments:[{aIdx,bIdx,gapHrs}]}>.
     */
    function detectGoingDark() {
        if (Detection.darkInfo) return Detection.darkInfo;
        buildVesselIndex();
        const info = new Map();
        const frames = Anim.filteredFrames;
        const thr = Anim.darkThresholdHrs;
        Detection.vesselIndex.forEach((records, mmsi) => {
            // Need a densely-tracked vessel; a few sparse hits are just intermittent
            // reception, not deliberate "going dark".
            if (records.length < 10) return;
            records.sort((a, b) => a.frameIdx - b.frameIdx);
            const gaps = [];
            for (let i = 1; i < records.length; i++) {
                gaps.push((Date.parse(frames[records[i].frameIdx].timestamp) -
                           Date.parse(frames[records[i - 1].frameIdx].timestamp)) / 3600e3);
            }
            // median inter-report gap = the vessel's normal cadence
            const med = [...gaps].sort((a, b) => a - b)[Math.floor(gaps.length / 2)] || 1;
            const segs = [];
            let maxGap = 0;
            for (let i = 0; i < gaps.length; i++) {
                // anomalous silence: longer than threshold AND ≫ this vessel's own cadence
                if (gaps[i] > thr && gaps[i] > med * 4) {
                    segs.push({ aIdx: records[i].frameIdx, bIdx: records[i + 1].frameIdx, gapHrs: gaps[i] });
                    maxGap = Math.max(maxGap, gaps[i]);
                }
            }
            if (segs.length) info.set(mmsi, { count: segs.length, maxGapHrs: maxGap, segments: segs });
        });
        Detection.darkInfo = info;
        return info;
    }

    /**
     * Detect vessels near submarine cables at current frame
     * Uses point-to-segment distance
     */
    function detectNearCable(frameIdx) {
        if (Detection.lastCableFrame === frameIdx && Detection.nearCableSet) {
            return Detection.nearCableSet;
        }
        const result = new Set();
        if (!Cable.lines || Cable.lines.length === 0) {
            Detection.nearCableSet = result;
            Detection.lastCableFrame = frameIdx;
            return result;
        }

        const frame = Anim.filteredFrames[frameIdx];
        if (!frame || !frame.vessels) {
            Detection.nearCableSet = result;
            Detection.lastCableFrame = frameIdx;
            return result;
        }

        const bufferDeg = Cable.bufferNm / 60;
        const bufferDegSq = bufferDeg * bufferDeg;

        frame.vessels.forEach(v => {
            for (const line of Cable.lines) {
                if (isPointNearLine(v.lat, v.lon, line, bufferDegSq)) {
                    result.add(v.mmsi);
                    break;
                }
            }
        });

        Detection.nearCableSet = result;
        Detection.lastCableFrame = frameIdx;
        return result;
    }

    /**
     * Check if point is within buffer distance of any segment in line
     */
    function isPointNearLine(plat, plon, line, bufferDegSq) {
        for (let i = 0; i < line.length - 1; i++) {
            const a = line[i], b = line[i + 1];
            // Quick bounding box check
            const minLat = Math.min(a[0], b[0]) - Math.sqrt(bufferDegSq);
            const maxLat = Math.max(a[0], b[0]) + Math.sqrt(bufferDegSq);
            const minLon = Math.min(a[1], b[1]) - Math.sqrt(bufferDegSq);
            const maxLon = Math.max(a[1], b[1]) + Math.sqrt(bufferDegSq);
            if (plat < minLat || plat > maxLat || plon < minLon || plon > maxLon) continue;

            // Point-to-segment distance squared
            const dx = b[1] - a[1], dy = b[0] - a[0];
            const lenSq = dx * dx + dy * dy;
            let t = lenSq > 0 ? ((plon - a[1]) * dx + (plat - a[0]) * dy) / lenSq : 0;
            t = Math.max(0, Math.min(1, t));
            const projLon = a[1] + t * dx;
            const projLat = a[0] + t * dy;
            const distSq = (plon - projLon) * (plon - projLon) + (plat - projLat) * (plat - projLat);
            if (distSq <= bufferDegSq) return true;
        }
        return false;
    }

    /**
     * Reset detection caches (call when filteredFrames change)
     */
    function resetDetectionCache() {
        Detection.vesselIndex = null;
        Detection.loiteringSet = null;
        Detection.zigzagSet = null;
        Detection.nearCableSet = null;
        Detection.anchoredSet = null;
        Detection.darkInfo = null;
        Detection.lastBuiltFrame = -1;
        Detection.lastCableFrame = -1;
    }

    /**
     * Get detection tags for a vessel at current frame
     * Returns array of {type, label, color}
     */
    function getDetectionTags(mmsi, frameIdx) {
        const tags = [];
        // Loitering
        const loiterSet = detectLoitering();
        if (loiterSet.has(mmsi)) {
            // Count how many consecutive low-speed frames
            const records = Detection.vesselIndex.get(mmsi) || [];
            let maxStreak = 0, streak = 1;
            for (let i = 1; i < records.length; i++) {
                if (records[i].speed < 1 && records[i].frameIdx - records[i-1].frameIdx <= 2) {
                    streak++;
                    maxStreak = Math.max(maxStreak, streak);
                } else { streak = 1; }
            }
            const hours = maxStreak * 2; // each frame ≈ 2hr
            tags.push({ type: 'loiter', label: i18n.t('ais_anim.loiter_hours', hours), color: '#b66bff' });
        }
        // Zigzag
        const zigzagSet = detectZigzag();
        if (zigzagSet.has(mmsi)) {
            tags.push({ type: 'zigzag', label: i18n.t('ais_anim.detect_zigzag'), color: '#ff5fe0' });
        }
        // Near cable
        if (Cable.lines.length > 0) {
            const cableSet = detectNearCable(frameIdx);
            if (cableSet.has(mmsi)) {
                tags.push({ type: 'cable', label: i18n.t('ais_anim.detect_cable'), color: '#ffd700' });
            }
        }
        // Anchoring (current frame) — pushed last so cable/loiter/zigzag still
        // drive the marker colour; anchoring is conveyed by the anchor glyph.
        if (isAnchoredAt(mmsi, frameIdx)) {
            tags.push({ type: 'anchor', label: i18n.t('ais_anim.detect_anchor'), color: ANCHOR_COLOR });
        }
        return tags;
    }

    // =========================================================================
    // Gray zone event loading & timeline mapping
    // =========================================================================
    async function loadGrayEvents() {
        if (Gray.loaded) return;
        Gray.loaded = true;
        try {
            const [stsRes, idRes, dataRes] = await Promise.all([
                fetch('ship_transfers.json?' + Date.now()).catch(() => null),
                fetch('identity_events.json?' + Date.now()).catch(() => null),
                fetch('data.json?' + Date.now()).catch(() => null)
            ]);

            const events = [];

            // STS transfers — keep only suspicious rendezvous
            if (stsRes && stsRes.ok) {
                const sts = await stsRes.json();
                const all = [].concat(sts.active_transfers || [], sts.history || []);
                all.forEach(t => {
                    if (t.classification !== 'suspicious') return;
                    const loc = t.location || {};
                    const lat = loc.lat, lon = loc.lon;
                    if (lat == null || lon == null) return;
                    const v1 = (t.vessel1 || {}).name || (t.vessel1 || {}).mmsi || '?';
                    const v2 = (t.vessel2 || {}).name || (t.vessel2 || {}).mmsi || '?';
                    events.push({
                        type: 'sts', ts: t.first_seen, lat, lon,
                        mmsis: [String((t.vessel1 || {}).mmsi), String((t.vessel2 || {}).mmsi)],
                        label: v1 + ' ↔ ' + v2,
                        detail: i18n.t('ais_anim.evt_at', (t.min_distance_m || 0).toFixed(1),
                                       (t.duration_hours || 0).toFixed(1)),
                        color: EVENT_COLORS.sts, frameIdx: -1
                    });
                });
            }

            // Identity changes — filter out buoy / beacon name flicker
            if (idRes && idRes.ok) {
                const ids = await idRes.json();
                (Array.isArray(ids) ? ids : []).forEach(ev => {
                    const ch = (ev.changes || [])[0];
                    if (!ch) return;
                    if (isBeaconNoise(ev.mmsi, ch.old) || isBeaconNoise(ev.mmsi, ch.new)) return;
                    if (ev.lat == null || ev.lon == null) return;
                    events.push({
                        type: 'identity', ts: ev.timestamp, lat: ev.lat, lon: ev.lon,
                        mmsis: [String(ev.mmsi)],
                        label: (ch.field || '') + ': ' + (ch.old || '?') + ' → ' + (ch.new || '?'),
                        detail: 'MMSI ' + ev.mmsi + (ev.multi_field ? ' · multi-field' : ''),
                        color: EVENT_COLORS.identity, frameIdx: -1
                    });
                });
            }

            // Cable faults — load cable geometry + status, place marker at segment midpoint.
            // (Faults dated outside the visible window are range-filtered out later.)
            if (!Cable.geoData) await loadCableData();
            if (Cable.faultStatus && Cable.faultStatus.faultsBySlug) {
                Object.entries(Cable.faultStatus.faultsBySlug).forEach(([slug, faults]) => {
                    const feat = (Cable.geoData.features || []).find(f => (f.properties || {}).slug === slug);
                    if (!feat) return;
                    let coords = feat.geometry.coordinates;
                    if (feat.geometry.type === 'MultiLineString') coords = coords[0];
                    if (!coords || !coords.length) return;
                    const mid = coords[Math.floor(coords.length / 2)];
                    faults.forEach(ft => {
                        events.push({
                            type: 'cable_fault', ts: ft.fault_date || null,
                            lat: mid[1], lon: mid[0], mmsis: [],
                            label: ft.name_zh || ft.segment || slug.replace(/-/g, ' '),
                            detail: ft.description_zh || ft.description_en || '',
                            color: EVENT_COLORS.cable_fault, frameIdx: -1
                        });
                    });
                });
            }

            Gray.events = events;

            // Vessel threat profiles for case narrative + PLA event-correlation context
            if (dataRes && dataRes.ok) {
                const data = await dataRes.json();
                const sa = data.suspicious_analysis || {};
                const sv = sa.suspicious_vessels || [];
                sv.forEach(v => Gray.profiles.set(String(v.mmsi), v));
                // Build the suspicious-vessel set from the full risk classification
                // (suspicious_vessels ∪ all_classifications where suspicious===true)
                // so the per-frame chart and markers reflect real threat scoring.
                sv.forEach(v => Gray.suspiciousMmsis.add(String(v.mmsi)));
                (sa.all_classifications || []).forEach(c => {
                    if (c && c.suspicious) Gray.suspiciousMmsis.add(String(c.mmsi));
                });

                // High-risk vessel annotations — surface the main-page suspicious
                // list onto this timeline (last known position / time). Shown on
                // the timeline ticks + event log, but not as extra map rings since
                // these vessels already render as red markers each frame.
                sv.forEach(v => {
                    if (v.last_lat == null || v.last_lon == null) return;
                    const nm = (v.names && v.names.length) ? v.names[v.names.length - 1] : String(v.mmsi);
                    const lvl = v.risk_level || 'normal';
                    Gray.events.push({
                        type: 'highrisk', ts: v.last_seen, lat: v.last_lat, lon: v.last_lon,
                        mmsis: [String(v.mmsi)],
                        label: nm + ' · ' + riskLabel(lvl) + ' (' + (v.risk_score != null ? v.risk_score : '?') + ')',
                        detail: (v.flags && v.flags.length ? v.flags.slice(0, 2).join('; ') : 'MMSI ' + v.mmsi),
                        color: RISK_COLORS[lvl] || RISK_COLORS.normal,
                        risk_level: lvl, risk_score: v.risk_score, frameIdx: -1
                    });
                });

                const ep = data.exercise_prediction || {};
                (ep.daily_merged || []).forEach(d => {
                    if (!d.date) return;
                    Context.byDate[d.date] = {
                        sorties: d.sorties || 0, naval: d.vessels || 0, dark: d.dark_vessels || 0
                    };
                    Context.maxSorties = Math.max(Context.maxSorties, d.sorties || 0);
                });
                Context.exercises = (ep.event_study || []).map(e => ({
                    exercise: e.exercise, trigger: e.trigger, start: e.start, end: e.end
                })).filter(e => e.start);
            }
        } catch (e) {
            console.error('Gray event load failed:', e);
        }

        buildEventFrameIndex();
        renderTimelineEvents();
        renderContextStrip();
        renderEventMarkers(Anim.currentIdx);
        renderEventLog();
        updateContextReadout(Anim.currentIdx);
        // Suspicious set is only known after data.json loads → refresh the
        // per-period suspicious chart and recolor the current frame's markers.
        if (Gray.suspiciousMmsis.size) {
            updateChart();
            renderFrame(Anim.currentIdx);
        }
    }

    /** Map each event timestamp to the nearest frame index in filteredFrames. */
    function buildEventFrameIndex() {
        const frames = Anim.filteredFrames;
        if (!frames.length || !Gray.events.length) return;
        const times = frames.map(f => Date.parse(f.timestamp || '') || 0);
        Gray.events.forEach(ev => {
            const t = Date.parse(ev.ts || '');
            if (isNaN(t)) { ev.frameIdx = -1; return; }
            // outside the visible range → hide
            if (t < times[0] - 2 * 3600e3 || t > times[times.length - 1] + 2 * 3600e3) {
                ev.frameIdx = -1; return;
            }
            let best = 0, bestD = Infinity;
            for (let i = 0; i < times.length; i++) {
                const d = Math.abs(times[i] - t);
                if (d < bestD) { bestD = d; best = i; }
            }
            ev.frameIdx = best;
        });
    }

    /** Render colored ticks on the timeline events track (aggregated per frame). */
    function renderTimelineEvents() {
        const track = document.getElementById('timelineEvents');
        if (!track) return;
        track.innerHTML = '';
        const n = Anim.filteredFrames.length;
        if (!Anim.showEvents || n <= 1 || !Gray.events.length) return;

        // Aggregate events by frame index to avoid hundreds of overlapping ticks
        const PRIORITY = { cable_fault: 4, highrisk: 3, sts: 2, identity: 1 };
        const byFrame = new Map();
        Gray.events.forEach(ev => {
            if (ev.frameIdx < 0) return;
            if (!byFrame.has(ev.frameIdx)) byFrame.set(ev.frameIdx, []);
            byFrame.get(ev.frameIdx).push(ev);
        });

        byFrame.forEach((evs, frameIdx) => {
            evs.sort((a, b) => (PRIORITY[b.type] || 0) - (PRIORITY[a.type] || 0));
            const top = evs[0];
            const pct = (frameIdx / (n - 1)) * 100;
            const tick = document.createElement('div');
            tick.className = 'tl-event';
            tick.style.left = pct + '%';
            tick.style.background = top.color;
            if (evs.length > 2) tick.style.height = '13px';
            tick.title = i18n.t('ais_anim.evt_' + top.type) + ' · ' + top.label +
                (evs.length > 1 ? ' (+' + (evs.length - 1) + ')' : '');
            tick.addEventListener('click', () => { pause(); renderFrame(frameIdx); });
            track.appendChild(tick);
        });
    }

    /** Render PLA sortie-intensity bars + named-exercise bands under the timeline. */
    function renderContextStrip() {
        const strip = document.getElementById('contextStrip');
        if (!strip) return;
        strip.innerHTML = '';
        const frames = Anim.filteredFrames;
        const n = frames.length;
        if (n <= 1 || Object.keys(Context.byDate).length === 0) return;

        // Per-frame PLA sortie intensity bar
        frames.forEach((f, i) => {
            const date = (f.timestamp || '').slice(0, 10);
            const ctx = Context.byDate[date];
            if (!ctx || !ctx.sorties) return;
            const s = ctx.sorties;
            const color = s >= 20 ? '#ff2d55' : s >= 10 ? '#ff7847' : '#00f5ff';
            const bar = document.createElement('div');
            bar.className = 'ctx-bar';
            bar.style.left = (i / (n - 1)) * 100 + '%';
            bar.style.height = Math.max(2, Math.min(12, (s / Context.maxSorties) * 12)) + 'px';
            bar.style.background = color;
            strip.appendChild(bar);
        });

        // Named exercise windows that fall inside the visible range
        const t0 = Date.parse(frames[0].timestamp), t1 = Date.parse(frames[n - 1].timestamp);
        const span = t1 - t0 || 1;
        Context.exercises.forEach(ex => {
            const s = Date.parse(ex.start + 'T00:00:00Z'), e = Date.parse((ex.end || ex.start) + 'T23:59:59Z');
            if (isNaN(s) || e < t0 || s > t1) return;
            const L = Math.max(0, (s - t0) / span) * 100;
            const R = Math.min(1, (e - t0) / span) * 100;
            const band = document.createElement('div');
            band.className = 'ctx-exercise';
            band.style.left = L + '%';
            band.style.width = Math.max(1.5, R - L) + '%';
            band.title = i18n.t('ais_anim.ctx_exercise') + ' · ' + ex.exercise + (ex.trigger ? ' (' + ex.trigger + ')' : '');
            strip.appendChild(band);
        });
    }

    /** Show the current frame's PLA pressure in the readout. */
    function updateContextReadout(idx) {
        const el = document.getElementById('contextReadout');
        if (!el) return;
        const frame = Anim.filteredFrames[idx];
        if (!frame) { el.textContent = ''; return; }
        const date = (frame.timestamp || '').slice(0, 10);
        const ctx = Context.byDate[date];
        if (!ctx) { el.textContent = ''; return; }
        el.textContent = i18n.t('ais_anim.ctx_readout', ctx.sorties, ctx.naval, ctx.dark);
    }

    /** Draw pulsing rings + caption for events active at the current frame. */
    function renderEventMarkers(idx) {
        if (!Anim.eventLayer) return;
        Anim.eventLayer.clearLayers();
        const caption = document.getElementById('eventCaption');
        if (!Anim.showEvents) { if (caption) caption.classList.remove('show'); return; }

        // High-risk vessels already render as red markers each frame, so keep them
        // out of the event rings/caption to avoid clutter (they stay on the
        // timeline + event log).
        const active = Gray.events.filter(ev => ev.frameIdx === idx && ev.type !== 'highrisk');
        active.forEach(ev => {
            const icon = L.divIcon({
                className: 'event-ring',
                html: '<span class="ring" style="color:' + ev.color + '"></span>',
                iconSize: [22, 22], iconAnchor: [11, 11]
            });
            L.marker([ev.lat, ev.lon], { icon, zIndexOffset: 500 })
                .addTo(Anim.eventLayer)
                .bindPopup('<div style="font-size:13px;line-height:1.6;min-width:160px">' +
                    '<b style="color:' + ev.color + '">' + i18n.t('ais_anim.evt_' + ev.type) + '</b><br>' +
                    ev.label + '<br><span style="color:#8aa4c8">' + ev.detail + '</span></div>');
        });

        if (caption) {
            if (active.length) {
                const ev = active[0];
                const more = active.length > 1 ? ' +' + (active.length - 1) : '';
                caption.style.borderLeftColor = ev.color;
                caption.innerHTML = '<span class="ec-type" style="color:' + ev.color + '">' +
                    i18n.t('ais_anim.evt_' + ev.type) + more + '</span> · ' + ev.label +
                    '<br><span style="color:#8aa4c8;font-size:10px">' + ev.detail + '</span>';
                caption.classList.add('show');
            } else {
                caption.classList.remove('show');
            }
        }
    }

    /**
     * Render the textual event log: the main-page high-risk vessel list +
     * gray-zone events in range. Rows are clickable (focus vessel / jump to frame).
     */
    function renderEventLog() {
        const body = document.getElementById('eventLogBody');
        if (!body) return;
        let html = '';

        // High-risk vessels (same list flagged on the main dashboard), top score first
        const profs = [...Gray.profiles.values()]
            .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0));
        html += '<div class="elog-section">⚠ ' + i18n.t('ais_anim.elog_highrisk') +
                ' <span class="elog-count">' + profs.length + '</span></div>';
        if (!profs.length) {
            html += '<div class="elog-empty">' + i18n.t('ais_anim.elog_no_hr') + '</div>';
        } else {
            html += profs.slice(0, 50).map(p => {
                const lvl = p.risk_level || 'normal';
                const nm = (p.names && p.names.length) ? p.names[p.names.length - 1] : String(p.mmsi);
                const flag = (p.flags && p.flags.length) ? p.flags[0] : '';
                return '<div class="elog-row" onclick="enterFocus(\'' + p.mmsi + '\')">' +
                    '<span class="risk-badge ' + lvl + '">' + (p.risk_score != null ? p.risk_score : '--') + '</span>' +
                    '<span class="elog-main"><span class="elog-name">' + nm + '</span>' +
                    '<span class="elog-sub">' + p.mmsi + (flag ? ' · ' + flag : '') + '</span></span>' +
                    '<button class="focus-btn" onclick="event.stopPropagation();enterFocus(\'' + p.mmsi + '\')">' +
                        i18n.t('ais_anim.focus_btn') + '</button>' +
                    '</div>';
            }).join('');
        }

        // Gray-zone events mapped into the current range
        const evs = Gray.events
            .filter(e => e.frameIdx >= 0 && e.type !== 'highrisk')
            .sort((a, b) => a.frameIdx - b.frameIdx);
        html += '<div class="elog-section">' + i18n.t('ais_anim.events_label') +
                ' <span class="elog-count">' + evs.length + '</span></div>';
        if (!evs.length) {
            html += '<div class="elog-empty">' + i18n.t('ais_anim.evt_none') + '</div>';
        } else {
            html += evs.map(e => {
                const ts = ((Anim.filteredFrames[e.frameIdx] || {}).timestamp || '').slice(5, 16).replace('T', ' ');
                return '<div class="elog-row" onclick="pause();renderFrame(' + e.frameIdx + ')">' +
                    '<span class="ce-dot" style="background:' + e.color + '"></span>' +
                    '<span class="elog-main"><span class="elog-name">' +
                        i18n.t('ais_anim.evt_' + e.type) + ' · ' + e.label + '</span>' +
                    '<span class="elog-sub">' + ts + (e.detail ? ' · ' + e.detail : '') + '</span></span>' +
                    '</div>';
            }).join('');
        }

        body.innerHTML = html;
    }

    function toggleEvents() {
        Anim.showEvents = document.getElementById('showEvents').checked;
        renderTimelineEvents();
        renderEventMarkers(Anim.currentIdx);
        syncUrl();
    }

    function toggleLoop() {
        Anim.loop = document.getElementById('loopPlay').checked;
    }

    // Collapse/expand the researcher-tools group (draw / export / going-dark).
    function toggleTools() {
        const tools = document.getElementById('researchTools');
        const btn = document.getElementById('toolsToggle');
        const hidden = tools.classList.toggle('tools-hidden');
        btn.classList.toggle('active', !hidden);
        btn.setAttribute('aria-expanded', String(!hidden));
    }

    // =========================================================================
    // Map initialization
    // =========================================================================
    function initMap() {
        map = L.map('map', {
            center: [24.5, 123.0],
            zoom: 6,
            zoomControl: true,
            attributionControl: false,
            preferCanvas: true
        });

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 18,
            opacity: 0.9
        }).addTo(map);

        // Fishing hotspots
        Object.entries(FISHING_HOTSPOTS).forEach(([key, spot]) => {
            const b = spot.bounds;
            L.rectangle([b[0], b[1]], {
                color: '#00ff88', weight: 1, opacity: 0.3,
                fillColor: 'rgba(0,255,136,0.05)', fillOpacity: 0.05,
                dashArray: '4,4'
            }).addTo(map).bindTooltip(spot.name, { permanent: false, direction: 'center' });
        });

        Anim.markerLayer = L.layerGroup().addTo(map);
        Anim.trailLayer = L.layerGroup().addTo(map);
        Anim.eventLayer = L.layerGroup().addTo(map);
        Anim.drawLayer = L.layerGroup().addTo(map);
        map.on('click', onMapClick);
    }

    // =========================================================================
    // Data loading
    // =========================================================================
    function hideLoading() {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.classList.add('hidden');
            setTimeout(() => overlay.style.display = 'none', 300);
        }
    }

    async function loadData() {
        try {
            document.getElementById('loadingDetail').textContent =
                '正在下載 AIS 軌跡資料...';

            const res = await fetch('ais_track_history.json?' + Date.now());
            if (!res.ok) throw new Error('HTTP ' + res.status);

            document.getElementById('loadingDetail').textContent =
                '正在解析資料，請稍候...';
            const data = await res.json();

            if (!Array.isArray(data) || data.length === 0) {
                hideLoading();
                document.getElementById('dataStatus').textContent = i18n.t('ais_anim.no_data');
                document.getElementById('vesselTableBody').innerHTML =
                    '<tr><td colspan="5" class="empty-state">' + i18n.t('ais_anim.no_data_msg') + '</td></tr>';
                return;
            }

            document.getElementById('loadingDetail').textContent =
                '渲染 ' + data.length + ' 幀資料中...';

            Anim.allFrames = data;

            // 用 setTimeout 讓 UI 更新後再渲染（避免阻塞 loading 動畫）
            await new Promise(r => setTimeout(r, 50));
            updateRangeButtons();
            applyRangeFilter(Anim.selectedRange);

            const first = data[0].timestamp || '';
            const last = data[data.length - 1].timestamp || '';
            document.getElementById('dataStatus').textContent =
                '\u2705 ' + first.slice(0, 10) + ' ~ ' + last.slice(0, 10);
            document.getElementById('updateInfo').textContent =
                i18n.t('common.updated') + ' ' + new Date(last).toLocaleString();

            hideLoading();

            // Restore any shareable state from the URL (focus / range / AOI / frame)
            applyUrlState();

            // Load gray-zone events in the background (non-blocking)
            loadGrayEvents();

        } catch (e) {
            console.error('載入失敗:', e);
            hideLoading();
            document.getElementById('dataStatus').textContent = i18n.t('ais_anim.load_fail');
            document.getElementById('vesselTableBody').innerHTML =
                '<tr><td colspan="5" class="empty-state">' + i18n.t('ais_anim.no_data_msg') + '</td></tr>';
        }
    }

    // =========================================================================
    // Range filtering
    // =========================================================================
    function applyRangeFilter(days) {
        Anim.selectedRange = days;
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - days);
        const cutoffStr = cutoff.toISOString();

        Anim.filteredFrames = Anim.allFrames.filter(f => f.timestamp >= cutoffStr);
        if (Anim.filteredFrames.length === 0) {
            Anim.filteredFrames = Anim.allFrames;
        }

        Anim.currentIdx = 0;
        Anim.vesselTrails = {};
        resetDetectionCache();
        pause();
        updateSlider();
        updateChart();
        if (Gray.loaded) { buildEventFrameIndex(); renderTimelineEvents(); renderContextStrip(); renderEventLog(); }
        renderFrame(0);
        if (Anim.aoi || Anim.tripwire) recomputeAreaMetrics();
        syncUrl();
    }

    function setRange(days) {
        document.querySelectorAll('[data-range]').forEach(btn => {
            btn.classList.toggle('active', parseInt(btn.dataset.range) === days);
        });
        applyRangeFilter(days);
    }

    /** 資料載入後，檢查每個時間範圍按鈕是否有對應資料，無資料則 disabled */
    function updateRangeButtons() {
        if (!Anim.allFrames || Anim.allFrames.length === 0) return;
        const now = new Date();
        document.querySelectorAll('[data-range]').forEach(btn => {
            const days = parseInt(btn.dataset.range);
            const cutoff = new Date(now);
            cutoff.setDate(cutoff.getDate() - days);
            const cutoffStr = cutoff.toISOString();
            const hasData = Anim.allFrames.some(f => f.timestamp >= cutoffStr);
            btn.disabled = !hasData;
            if (btn.disabled && btn.classList.contains('active')) {
                btn.classList.remove('active');
            }
        });
        // 如果目前 active 的按鈕被 disabled，自動選最大可用範圍
        const activeBtn = document.querySelector('[data-range].active:not([disabled])');
        if (!activeBtn) {
            const available = [...document.querySelectorAll('[data-range]:not([disabled])')];
            if (available.length > 0) {
                const largest = available.reduce((a, b) =>
                    parseInt(a.dataset.range) > parseInt(b.dataset.range) ? a : b);
                setRange(parseInt(largest.dataset.range));
            }
        }
    }

    // =========================================================================
    // Build vessel trails up to current frame
    // =========================================================================
    function buildTrails(upToIdx) {
        Anim.vesselTrails = {};
        for (let i = 0; i <= upToIdx; i++) {
            const frame = Anim.filteredFrames[i];
            if (!frame || !frame.vessels) continue;
            frame.vessels.forEach(v => {
                if (!Anim.vesselTrails[v.mmsi]) {
                    Anim.vesselTrails[v.mmsi] = { points: [], frames: [], suspicious: isSuspicious(v.mmsi), name: v.name };
                }
                Anim.vesselTrails[v.mmsi].points.push([v.lat, v.lon]);
                Anim.vesselTrails[v.mmsi].frames.push(i);  // for going-dark gap detection
                Anim.vesselTrails[v.mmsi].suspicious = isSuspicious(v.mmsi);
            });
        }
    }

    // Compute heading angle (degrees, 0=north, clockwise) from trail history
    function computeHeading(mmsi, currentLat, currentLon) {
        const trail = Anim.vesselTrails && Anim.vesselTrails[mmsi];
        if (trail && trail.points.length >= 2) {
            const pts = trail.points;
            const prev = pts[pts.length - 2];
            const curr = pts[pts.length - 1];
            const dLon = curr[1] - prev[1];
            const dLat = curr[0] - prev[0];
            if (Math.abs(dLon) > 0.0001 || Math.abs(dLat) > 0.0001) {
                const angle = Math.atan2(dLon, dLat) * (180 / Math.PI);
                return (angle + 360) % 360;
            }
        }
        return null; // unknown heading
    }

    // Draw an age-graded "comet" trail: older segments faint/thin, recent bright/thick
    function drawCometTrail(points, color, layer, opts) {
        opts = opts || {};
        const maxSeg = opts.maxSeg || 60;
        const minW = opts.minW || 0.6, maxW = opts.maxW || 2.4, maxO = opts.maxO || 0.85;
        const start = Math.max(1, points.length - maxSeg);
        const total = Math.max(1, points.length - start);
        for (let i = start; i < points.length; i++) {
            const age = (i - start) / total; // 0 = oldest shown, 1 = newest
            L.polyline([points[i - 1], points[i]], {
                color: color,
                weight: minW + age * (maxW - minW),
                opacity: 0.12 + age * (maxO - 0.12),
                interactive: false
            }).addTo(layer);
        }
    }

    // Create a clean MarineTraffic-style vessel symbol.
    //  - moving vessel: a kite/pentagon hull pointed toward its heading
    //  - stationary / unknown heading: a circle (MT convention)
    // Type-colored fill + thin light outline for crisp edges on the dark map.
    function createVesselIcon(color, emphasis, heading, stationary) {
        const w = emphasis ? 13 : 10;
        const h = emphasis ? 18 : 14;
        const sw = emphasis ? 1.1 : 0.8;          // outline width
        const stroke = 'rgba(255,255,255,0.7)';   // light halo for contrast

        let shape, box;
        if (stationary || heading === null) {
            // Circle for moored / drifting vessels
            const r = emphasis ? 4.6 : 3.4;
            const d = r * 2 + sw * 2;
            box = d;
            shape = '<circle cx="' + (d / 2) + '" cy="' + (d / 2) + '" r="' + r + '" ' +
                    'fill="' + color + '" fill-opacity="0.92" ' +
                    'stroke="' + stroke + '" stroke-width="' + sw + '"/>';
            const svg = '<svg width="' + d + '" height="' + d + '" viewBox="0 0 ' + d + ' ' + d + '" ' +
                        'xmlns="http://www.w3.org/2000/svg">' + shape + '</svg>';
            return L.divIcon({
                html: svg, className: 'vessel-icon',
                iconSize: [d, d], iconAnchor: [d / 2, d / 2], popupAnchor: [0, -d / 2]
            });
        }

        // Hull pointing up (bow at top); rotated by heading via CSS transform.
        const cx = w / 2;
        const pts = [
            cx + ',0',                              // bow tip
            w + ',' + (h * 0.32),                   // starboard shoulder
            (w * 0.80) + ',' + h,                   // starboard stern
            (w * 0.20) + ',' + h,                   // port stern
            '0,' + (h * 0.32)                       // port shoulder
        ].join(' ');
        shape = '<polygon points="' + pts + '" ' +
                'fill="' + color + '" fill-opacity="0.95" ' +
                'stroke="' + stroke + '" stroke-width="' + sw + '" stroke-linejoin="round"/>';

        const svg = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" ' +
                    'xmlns="http://www.w3.org/2000/svg" ' +
                    'style="transform:rotate(' + heading + 'deg);transform-origin:center">' +
                    shape + '</svg>';

        return L.divIcon({
            html: svg, className: 'vessel-icon',
            iconSize: [w, h], iconAnchor: [w / 2, h / 2], popupAnchor: [0, -h / 2]
        });
    }

    // Anchoring vessel symbol: a filled disc carrying a white anchor glyph.
    // Suspicious anchoring vessels get a red ring so the threat still reads.
    function createAnchorIcon(emphasis, suspicious) {
        const d = emphasis ? 22 : 18;
        const ring = suspicious ? '#ff3366' : 'rgba(255,255,255,0.85)';
        const ringW = suspicious ? 2.2 : 1.4;
        const svg = '<svg width="' + d + '" height="' + d + '" viewBox="0 0 24 24" ' +
            'xmlns="http://www.w3.org/2000/svg">' +
            '<circle cx="12" cy="12" r="11" fill="' + ANCHOR_COLOR + '" ' +
                'fill-opacity="0.95" stroke="' + ring + '" stroke-width="' + ringW + '"/>' +
            '<g fill="none" stroke="#fff" stroke-width="1.7" ' +
                'stroke-linecap="round" stroke-linejoin="round">' +
            '<circle cx="12" cy="6" r="1.7"/>' +              // ring
            '<path d="M12 7.7 V17.6"/>' +                      // shank
            '<path d="M8.4 10 H15.6"/>' +                      // stock
            '<path d="M6.4 13 A6 6 0 0 0 17.6 13"/>' +         // flukes arc
            '<path d="M6.4 13 l-1.1 -1.1 M6.4 13 l1.6 0.4"/>' +
            '<path d="M17.6 13 l1.1 -1.1 M17.6 13 l-1.6 0.4"/>' +
            '</g></svg>';
        return L.divIcon({
            html: svg, className: 'vessel-icon anchor-icon',
            iconSize: [d, d], iconAnchor: [d / 2, d / 2], popupAnchor: [0, -d / 2]
        });
    }

    // =========================================================================
    // Frame rendering
    // =========================================================================
    function renderFrame(idx) {
        if (idx < 0 || idx >= Anim.filteredFrames.length) return;
        Anim.currentIdx = idx;

        const frame = Anim.filteredFrames[idx];
        const allVessels = frame.vessels || [];
        const focus = Anim.focusMmsi;
        // In focus mode, ignore the dropdown filter and show only the focused vessel
        const vessels = focus
            ? allVessels.filter(v => String(v.mmsi) === focus)
            : filterVessels(allVessels);

        // Clear layers
        Anim.markerLayer.clearLayers();
        Anim.trailLayer.clearLayers();

        // Always build trails (needed for heading computation)
        buildTrails(idx);

        // Draw trail lines if enabled (comet gradient for suspicious / focus vessels)
        if (Anim.showTrails) {
            if (focus) {
                const tr = Anim.vesselTrails[focus];
                if (tr && tr.points.length >= 2) {
                    drawCometTrail(tr.points, '#ff3366', Anim.trailLayer, { maxSeg: 400, maxW: 3, maxO: 0.95 });
                }
            } else {
                Object.entries(Anim.vesselTrails).forEach(([mmsi, trail]) => {
                    if (trail.points.length < 2) return;
                    if (trail.suspicious) {
                        drawCometTrail(trail.points, '#ff3366', Anim.trailLayer, { maxSeg: 80, maxW: 2.2, maxO: 0.85 });
                    } else {
                        L.polyline(trail.points, {
                            color: '#00f5ff', weight: 0.7, opacity: 0.18, interactive: false
                        }).addTo(Anim.trailLayer);
                    }
                });
            }
        }

        // Going-dark ghost segments (gaps where AIS went silent then reappeared)
        if (Anim.showDark) drawGoingDarkSegments(focus);

        // Plot vessel markers (triangle = heading known, diamond = unknown)
        vessels.forEach(v => {
            const isSusp = isSuspicious(v.mmsi);
            const tags = getDetectionTags(v.mmsi, idx);
            const hasDetection = tags.length > 0;

            // Color priority: detection > suspicious > vessel type (MarineTraffic-style)
            let color = isSusp ? '#ff3366' : (VESSEL_COLORS[v.type_name] || VESSEL_COLORS.unknown);
            if (hasDetection) {
                color = tags[0].color; // use primary detection color
            }
            const heading = computeHeading(v.mmsi, v.lat, v.lon) ?? (v.heading !== undefined ? v.heading : null);

            const emphasis = isSusp || hasDetection || (focus && String(v.mmsi) === focus);
            const stationary = (v.speed !== undefined && v.speed !== null && v.speed < 0.5);
            const anchoring = tags.some(t => t.type === 'anchor');
            const icon = anchoring
                ? createAnchorIcon(emphasis, isSusp)
                : createVesselIcon(color, emphasis, heading, stationary);
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(Anim.markerLayer);

            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            const tagHtml = tags.map(t =>
                '<span style="display:inline-block;padding:1px 5px;border-radius:3px;font-size:11px;font-weight:600;background:rgba(255,255,255,0.1);color:' + t.color + ';margin-right:3px">' + t.label + '</span>'
            ).join('');
            const focusBtn = (focus && String(v.mmsi) === focus) ? '' :
                '<button onclick="enterFocus(\'' + v.mmsi + '\')" style="margin-top:6px;background:#141e32;border:1px solid #00f5ff;color:#00f5ff;border-radius:4px;padding:3px 8px;font-size:12px;cursor:pointer">' + i18n.t('ais_anim.focus_btn') + '</button>';
            marker.bindPopup(
                '<div style="font-size:14px;line-height:1.8;min-width:180px">' +
                '<b style="color:' + color + ';font-size:15px">' + (v.name || v.mmsi) + '</b><br>' +
                'MMSI: ' + v.mmsi + '<br>' +
                ((v.mmsi || '').startsWith('898') ? '<span style="color:#8aa4c8;font-weight:600">🎣 可能為魚網標記</span><br>' : '') +
                i18n.t('ais_anim.popup_type') + ': ' + v.type_name + '<br>' +
                i18n.t('ais_anim.popup_speed') + ': ' + (v.speed || 0).toFixed(1) + ' kn<br>' +
                '航向: ' + headingText + '<br>' +
                (isSusp ? '<span style="color:#ff3366;font-weight:600;font-size:14px">⚠ SUSPICIOUS</span><br>' : '') +
                (tagHtml ? tagHtml : '') +
                focusBtn +
                '</div>'
            );
        });

        // Follow the focused vessel
        if (focus && vessels.length) {
            map.panTo([vessels[0].lat, vessels[0].lon], { animate: true, duration: 0.4 });
        }

        // Gray-zone event markers + caption for this frame
        renderEventMarkers(idx);

        // Update UI
        updateDayIndicator(frame, idx);
        updateStatCards(frame, idx);
        updateVesselTable(focus ? filterVessels(allVessels) : vessels);
        updateSliderPosition(idx);
        highlightChartBar(idx);
    }

    // =========================================================================
    // UI updates
    // =========================================================================
    function updateDayIndicator(frame, idx) {
        const ts = frame.timestamp || '';
        const dateStr = ts.slice(0, 16).replace('T', ' ');
        document.getElementById('dayIndicator').innerHTML =
            '<div class="date-text">' + dateStr + ' UTC</div>' +
            '<div class="frame-text">' + i18n.t('ais_anim.frame', idx + 1, Anim.filteredFrames.length) + '</div>';
    }

    function updateStatCards(frame, idx) {
        // Repurposed: drive the PLA event-correlation readout
        updateContextReadout(idx);
    }

    function updateVesselTable(vessels) {
        const tbody = document.getElementById('vesselTableBody');
        if (!vessels || vessels.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">' + i18n.t('ais_anim.no_data_msg') + '</td></tr>';
            return;
        }

        // Sort: suspicious first, then by speed descending
        const sorted = [...vessels].sort((a, b) => {
            const sa = isSuspicious(a.mmsi), sb = isSuspicious(b.mmsi);
            if (sa !== sb) return sb ? 1 : -1;
            return (b.speed || 0) - (a.speed || 0);
        });

        const rows = sorted.slice(0, 30).map(v => {
            const isSusp = isSuspicious(v.mmsi);
            const tags = getDetectionTags(v.mmsi, Anim.currentIdx);
            const tagHtml = tags.map(t =>
                '<span class="detect-tag ' + t.type + '">' + t.label + '</span>'
            ).join('');
            const combined = (isSusp ? '<span class="suspicious-tag">⚠ SUSPICIOUS</span> ' : '') + tagHtml;
            const focusBtn = '<button class="focus-btn" onclick="enterFocus(\'' + v.mmsi + '\')">' + i18n.t('ais_anim.focus_btn') + '</button>';
            const statusHtml = (combined || 'OK') + ' ' + focusBtn;
            return '<tr>' +
                '<td>' + v.mmsi + '</td>' +
                '<td>' + (v.name || '--') + '</td>' +
                '<td>' + (v.type_name || '--') + '</td>' +
                '<td>' + (v.speed || 0).toFixed(1) + ' kn</td>' +
                '<td>' + statusHtml + '</td>' +
                '</tr>';
        });

        tbody.innerHTML = rows.join('');
        if (sorted.length > 30) {
            tbody.innerHTML += '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);font-size:9px">... ' + (sorted.length - 30) + ' more vessels</td></tr>';
        }
    }

    // =========================================================================
    // Slider
    // =========================================================================
    function updateSlider() {
        const slider = document.getElementById('daySlider');
        slider.min = 0;
        slider.max = Math.max(0, Anim.filteredFrames.length - 1);
        slider.value = 0;

        const labels = document.getElementById('timelineLabels');
        const frames = Anim.filteredFrames;
        if (frames.length <= 1) {
            labels.innerHTML = frames.length === 1 ? '<span>' + (frames[0].timestamp || '').slice(5, 16) + '</span>' : '';
            return;
        }

        const step = Math.max(1, Math.floor(frames.length / 6));
        const labelHtml = [];
        for (let i = 0; i < frames.length; i++) {
            if (i === 0 || i === frames.length - 1 || i % step === 0) {
                labelHtml.push('<span>' + (frames[i].timestamp || '').slice(5, 16).replace('T', ' ') + '</span>');
            }
        }
        labels.innerHTML = labelHtml.join('');
    }

    function updateSliderPosition(idx) {
        const slider = document.getElementById('daySlider');
        slider.value = idx;
        const max = parseInt(slider.max) || 1;
        slider.style.setProperty('--progress', (max ? (idx / max) * 100 : 0) + '%');
    }

    document.getElementById('daySlider').addEventListener('input', function () {
        pause();
        renderFrame(parseInt(this.value));
        syncUrl();
    });

    // =========================================================================
    // Chart
    // =========================================================================
    function updateChart() {
        const ctx = document.getElementById('dailyChart').getContext('2d');
        if (Anim.chart) Anim.chart.destroy();

        const labels = Anim.filteredFrames.map(f => (f.timestamp || '').slice(5, 16).replace('T', ' '));
        // Track frames no longer carry suspicious_count (always 0 since detection
        // moved to analyze_suspicious.py). Derive the count per frame from the
        // suspicious-vessel set loaded from data.json. Falls back to the stored
        // field before that set is available.
        const data = Anim.filteredFrames.map(f => {
            if (Gray.suspiciousMmsis.size && Array.isArray(f.vessels)) {
                return f.vessels.reduce((n, v) => n + (isSuspicious(v.mmsi) ? 1 : 0), 0);
            }
            return f.suspicious_count || 0;
        });

        Anim.chart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: i18n.t('ais_anim.suspicious'),
                    data: data,
                    backgroundColor: data.map((_, i) =>
                        i === Anim.currentIdx ? 'rgba(0, 245, 255, 0.8)' : 'rgba(255, 51, 102, 0.5)'
                    ),
                    borderColor: data.map((_, i) =>
                        i === Anim.currentIdx ? '#00f5ff' : '#ff3366'
                    ),
                    borderWidth: 1,
                    borderRadius: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        ticks: { color: '#8aa4c8', font: { size: 7, family: 'JetBrains Mono' }, maxRotation: 45 },
                        grid: { color: 'rgba(0,245,255,0.05)' }
                    },
                    y: {
                        ticks: { color: '#8aa4c8', font: { size: 9, family: 'JetBrains Mono' } },
                        grid: { color: 'rgba(0,245,255,0.05)' }
                    }
                }
            }
        });
    }

    function highlightChartBar(idx) {
        if (!Anim.chart) return;
        const dataset = Anim.chart.data.datasets[0];
        dataset.backgroundColor = dataset.data.map((_, i) =>
            i === idx ? 'rgba(0, 245, 255, 0.8)' : 'rgba(255, 51, 102, 0.5)'
        );
        dataset.borderColor = dataset.data.map((_, i) =>
            i === idx ? '#00f5ff' : '#ff3366'
        );
        Anim.chart.update('none');
    }

    // =========================================================================
    // Playback controls
    // =========================================================================
    function play() {
        if (Anim.playing) return;
        if (Anim.filteredFrames.length === 0) return;
        if (Anim.currentIdx >= Anim.filteredFrames.length - 1) {
            Anim.currentIdx = -1;
        }

        Anim.playing = true;
        const btn = document.getElementById('playBtn');
        btn.classList.add('playing');
        btn.textContent = '\u23F8';

        const baseInterval = 1500;
        const interval = baseInterval / Anim.speed;

        Anim.intervalId = setInterval(() => {
            const nextIdx = Anim.currentIdx + 1;
            if (nextIdx >= Anim.filteredFrames.length) {
                if (Anim.loop) { renderFrame(0); return; }
                pause();
                return;
            }
            renderFrame(nextIdx);
        }, interval);
    }

    function pause() {
        Anim.playing = false;
        clearInterval(Anim.intervalId);
        const btn = document.getElementById('playBtn');
        btn.classList.remove('playing');
        btn.textContent = '\u25B6';
    }

    function togglePlay() {
        Anim.playing ? pause() : play();
    }

    function stepForward() {
        pause();
        renderFrame(Math.min(Anim.currentIdx + 1, Anim.filteredFrames.length - 1));
    }

    function stepBackward() {
        pause();
        renderFrame(Math.max(Anim.currentIdx - 1, 0));
    }

    function setSpeed(speed) {
        Anim.speed = speed;
        document.querySelectorAll('[data-speed]').forEach(btn => {
            btn.classList.toggle('active', parseInt(btn.dataset.speed) === speed);
        });
        if (Anim.playing) {
            pause();
            play();
        }
    }

    function toggleTrails() {
        Anim.showTrails = document.getElementById('showTrails').checked;
        renderFrame(Anim.currentIdx);
    }

    // Chinese fishing vessel name patterns
    const CN_FISHING_PATTERNS = [
        /^MIN/i, /^闽/i, /^閩/i,           // 福建 (Fujian/Min)
        /^XIANG/i, /^湘/i,                  // 湖南 (Hunan/Xiang)
        /^LU\s?YU/i, /^鲁/i, /^魯/i,       // 山東 (Shandong/Lu)
        /^ZHE/i, /^浙/i,                    // 浙江 (Zhejiang)
        /^YUE/i, /^粤/i, /^粵/i,            // 廣東 (Guangdong/Yue)
        /^SU\s?YU/i, /^苏/i, /^蘇/i,       // 江蘇 (Jiangsu/Su)
        /^GUI/i, /^桂/i,                    // 廣西 (Guangxi/Gui)
        /^QIONG/i, /^琼/i, /^瓊/i,         // 海南 (Hainan/Qiong)
        /^JIN\s?YU/i, /^津/i,              // 天津 (Tianjin/Jin)
        /^LIAO/i, /^辽/i, /^遼/i,          // 遼寧 (Liaoning)
    ];

    function isCnFishingVessel(v) {
        const name = (v.name || '').toUpperCase();
        if (CN_FISHING_PATTERNS.some(p => p.test(name))) return true;
        // Also match names containing "YU" (漁) with number patterns like "MIN.HUI.YU.00582"
        if (/YU[.\s]*\d/i.test(name)) return true;
        return false;
    }

    function filterVessels(vessels) {
        switch (Anim.vesselFilter) {
            case 'cn_fishing':
                return vessels.filter(v => isCnFishingVessel(v));
            case 'suspicious':
                return vessels.filter(v => isSuspicious(v.mmsi));
            case 'loitering':
                { const set = detectLoitering(); return vessels.filter(v => set.has(v.mmsi)); }
            case 'zigzag':
                { const set = detectZigzag(); return vessels.filter(v => set.has(v.mmsi)); }
            case 'near_cable':
                { const set = detectNearCable(Anim.currentIdx); return vessels.filter(v => set.has(v.mmsi)); }
            case 'anchored':
                { const set = detectAnchored(); return vessels.filter(v => set.has(v.mmsi)); }
            case 'dark':
                { const info = detectGoingDark(); return vessels.filter(v => info.has(v.mmsi)); }
            default:
                return vessels;
        }
    }

    async function setVesselFilter(filter) {
        Anim.vesselFilter = filter;
        // Auto-enable cable layer when selecting near_cable filter
        if (filter === 'near_cable') {
            if (!Cable.geoData) await loadCableData();
            if (!Cable.visible) {
                Cable.visible = true;
                document.getElementById('showCables').checked = true;
                document.getElementById('cableControls').style.display = 'flex';
                renderCableLayer();
            }
        }
        renderFrame(Anim.currentIdx);
        syncUrl();
    }

    // =========================================================================
    // Single-vessel focus / case narrative
    // =========================================================================
    function enterFocus(mmsi) {
        Anim.focusMmsi = String(mmsi);
        if (map) map.closePopup();
        const panel = document.getElementById('casePanel');
        panel.style.display = 'block';
        renderCaseNarrative(Anim.focusMmsi);
        renderFrame(Anim.currentIdx);
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        syncUrl();
    }

    function exitFocus() {
        Anim.focusMmsi = null;
        document.getElementById('casePanel').style.display = 'none';
        renderFrame(Anim.currentIdx);
        syncUrl();
    }

    function riskLabel(level) { return i18n.t('ais_anim.risk_' + (level || 'normal')); }

    function renderCaseNarrative(mmsi) {
        const prof = Gray.profiles.get(mmsi);
        const trail = Anim.vesselTrails[mmsi];
        let displayName = mmsi;
        if (prof && prof.names && prof.names.length) displayName = prof.names[prof.names.length - 1];
        else if (trail && trail.name) displayName = trail.name;
        document.getElementById('caseName').textContent = displayName + ' · ' + mmsi;

        let html = '';
        if (prof) {
            const lvl = prof.risk_level || 'normal';
            html += '<div class="case-row"><span class="k">' + i18n.t('ais_anim.focus_risk') + '</span>' +
                '<span class="risk-badge ' + lvl + '">' + (prof.risk_score != null ? prof.risk_score : '--') + '</span> ' +
                '<span style="color:#8aa4c8">' + riskLabel(lvl) + '</span></div>';
            if (prof.names && prof.names.length > 1) {
                html += '<div class="case-row"><span class="k">' + i18n.t('ais_anim.focus_names') + '</span>' +
                    prof.names.slice(0, 8).join('、') + '</div>';
            }
            if (prof.flags && prof.flags.length) {
                html += '<div class="case-row"><span class="k">' + i18n.t('ais_anim.focus_flags') + '</span>' +
                    prof.flags.map(f => '<div class="case-flag">⚠ ' + f + '</div>').join('') + '</div>';
            }
        } else {
            html += '<div class="case-row" style="color:#8aa4c8">' + i18n.t('ais_anim.focus_no_profile') + '</div>';
            const tags = getDetectionTags(mmsi, Anim.currentIdx);
            if (tags.length) {
                html += '<div class="case-row">' + tags.map(t =>
                    '<span class="detect-tag ' + t.type + '">' + t.label + '</span>').join(' ') + '</div>';
            }
        }

        const dark = detectGoingDark().get(mmsi);
        if (dark) {
            html += '<div class="case-row"><span class="k">' + i18n.t('ais_anim.focus_dark') + '</span>' +
                i18n.t('ais_anim.focus_dark_val', dark.count, Math.round(dark.maxGapHrs)) + '</div>';
        }

        const evs = Gray.events.filter(e => e.mmsis.includes(mmsi) && e.frameIdx >= 0)
            .sort((a, b) => a.frameIdx - b.frameIdx);
        html += '<div class="case-row"><span class="k">' + i18n.t('ais_anim.focus_timeline') + '</span>';
        if (evs.length) {
            html += evs.map(e => {
                const ts = (Anim.filteredFrames[e.frameIdx].timestamp || '').slice(5, 16).replace('T', ' ');
                return '<div class="case-evt" onclick="pause();renderFrame(' + e.frameIdx + ')">' +
                    '<span class="ce-dot" style="background:' + e.color + '"></span>' +
                    '<span class="ce-time">' + ts + '</span>' +
                    '<span>' + i18n.t('ais_anim.evt_' + e.type) + ' · ' + e.label + '</span></div>';
            }).join('');
        } else {
            html += '<div style="color:#8aa4c8">' + i18n.t('ais_anim.focus_no_events') + '</div>';
        }
        html += '</div>';
        document.getElementById('caseBody').innerHTML = html;
    }

    // =========================================================================
    // Education: intro panel + behavior tooltips
    // =========================================================================
    function initIntro() {
        const intro = document.getElementById('gzIntro');
        const seen = localStorage.getItem('ais-anim-intro-seen');
        if (!seen) intro.style.display = 'block';
        document.getElementById('gzIntroClose').addEventListener('click', () => {
            intro.style.display = 'none';
            localStorage.setItem('ais-anim-intro-seen', '1');
        });
        // Reopen button in the controls bar
        const btn = document.createElement('button');
        btn.className = 'gz-intro-reopen';
        btn.textContent = i18n.t('ais_anim.intro_reopen');
        btn.id = 'gzReopen';
        btn.addEventListener('click', () => { intro.style.display = 'block'; });
        document.getElementById('animControls').prepend(btn);
    }

    const BEH_KEYS = {
        sts: 'ais_anim.beh_sts', identity: 'ais_anim.beh_identity', cable: 'ais_anim.beh_cable',
        loiter: 'ais_anim.beh_loiter', zigzag: 'ais_anim.beh_zigzag', dark: 'ais_anim.beh_dark',
        anchor: 'ais_anim.beh_anchor'
    };
    let behTip = null;
    function initBehaviorTips() {
        behTip = document.createElement('div');
        behTip.style.cssText = 'position:fixed;z-index:9998;max-width:240px;background:rgba(10,15,28,0.96);' +
            'border:1px solid var(--accent-cyan);border-radius:6px;padding:8px 10px;font-size:11px;' +
            'line-height:1.5;color:#e8eef7;display:none;box-shadow:0 4px 16px rgba(0,0,0,0.5)';
        document.body.appendChild(behTip);
        document.addEventListener('click', e => {
            if (!e.target.closest('[data-beh]')) behTip.style.display = 'none';
        });
        document.querySelectorAll('[data-beh]').forEach(el => {
            const key = BEH_KEYS[el.dataset.beh];
            if (!key) return;
            el.addEventListener('click', e => {
                e.stopPropagation();
                behTip.textContent = i18n.t(key);
                behTip.style.display = 'block';
                const r = el.getBoundingClientRect();
                behTip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 252)) + 'px';
                behTip.style.top = (r.bottom + 6) + 'px';
            });
        });
        refreshBehTitles();
    }
    function refreshBehTitles() {
        document.querySelectorAll('[data-beh]').forEach(el => {
            const key = BEH_KEYS[el.dataset.beh];
            if (key) el.setAttribute('title', i18n.t(key));
        });
    }

    // =========================================================================
    // Feature 4 — Going dark: ghost segments + toggles
    // =========================================================================
    function drawGoingDarkSegments(focus) {
        const info = detectGoingDark();
        if (info.size === 0) return;
        const frames = Anim.filteredFrames;
        const entries = focus
            ? (Anim.vesselTrails[focus] ? [[focus, Anim.vesselTrails[focus]]] : [])
            : Object.entries(Anim.vesselTrails);
        entries.forEach(([mmsi, trail]) => {
            if (!info.has(mmsi)) return;
            const pts = trail.points, fr = trail.frames;
            for (let k = 1; k < pts.length; k++) {
                const gapHrs = (Date.parse(frames[fr[k]].timestamp) - Date.parse(frames[fr[k - 1]].timestamp)) / 3600e3;
                if (gapHrs <= Anim.darkThresholdHrs) continue;
                L.polyline([pts[k - 1], pts[k]], {
                    color: '#9aa6b8', weight: 1, opacity: 0.6, dashArray: '2,8', interactive: false
                }).addTo(Anim.trailLayer);
                const mid = [(pts[k - 1][0] + pts[k][0]) / 2, (pts[k - 1][1] + pts[k][1]) / 2];
                L.marker(mid, {
                    icon: L.divIcon({ className: 'dark-label', html: '<span>' + i18n.t('ais_anim.dark_label', Math.round(gapHrs)) + '</span>', iconSize: [0, 0] }),
                    interactive: false
                }).addTo(Anim.trailLayer);
            }
        });
    }

    function toggleDark() {
        Anim.showDark = document.getElementById('showDark').checked;
        document.getElementById('darkControls').style.display = Anim.showDark ? 'inline-flex' : 'none';
        renderFrame(Anim.currentIdx);
        syncUrl();
    }
    function updateDarkThreshold() {
        Anim.darkThresholdHrs = parseFloat(document.getElementById('darkThreshold').value);
        document.getElementById('darkThresholdVal').textContent = Anim.darkThresholdHrs;
        Detection.darkInfo = null; // recompute
        renderFrame(Anim.currentIdx);
        if (Anim.vesselFilter === 'dark') renderFrame(Anim.currentIdx);
    }

    // =========================================================================
    // Feature 3 — AOI / tripwire geometry + metrics
    // =========================================================================
    function pointInPolygon(lat, lon, ring) {
        // ray casting; ring = [[lat,lon],...]
        let inside = false;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
            const yi = ring[i][0], xi = ring[i][1], yj = ring[j][0], xj = ring[j][1];
            const intersect = ((xi > lon) !== (xj > lon)) &&
                (lat < (yj - yi) * (lon - xi) / ((xj - xi) || 1e-12) + yi);
            if (intersect) inside = !inside;
        }
        return inside;
    }
    function segIntersect(a, b, c, d) {
        // a,b,c,d = [lat,lon]; segment AB vs CD
        function ccw(p, q, r) { return (r[0] - p[0]) * (q[1] - p[1]) - (q[0] - p[0]) * (r[1] - p[1]); }
        const d1 = ccw(c, d, a), d2 = ccw(c, d, b), d3 = ccw(a, b, c), d4 = ccw(a, b, d);
        return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
    }

    function onMapClick(e) {
        if (!Anim.drawMode) return;
        Anim.drawPts.push([e.latlng.lat, e.latlng.lng]);
        renderDrawLayer(true);
        if (Anim.drawMode === 'line' && Anim.drawPts.length >= 2) finishDraw();
    }

    function toggleDraw(mode) {
        if (Anim.drawMode === mode) { finishDraw(); return; }
        clearDraw(true);
        Anim.drawMode = mode;
        Anim.drawPts = [];
        if (map.doubleClickZoom) map.doubleClickZoom.disable();
        document.getElementById('drawAreaBtn').classList.toggle('active', mode === 'area');
        document.getElementById('drawLineBtn').classList.toggle('active', mode === 'line');
        const hint = document.getElementById('drawHint');
        hint.style.display = 'inline-block';
        hint.textContent = i18n.t(mode === 'area' ? 'ais_anim.draw_hint_area' : 'ais_anim.draw_hint_line');
    }

    function finishDraw() {
        const mode = Anim.drawMode, pts = Anim.drawPts;
        Anim.drawMode = null;
        if (map.doubleClickZoom) map.doubleClickZoom.enable();
        document.getElementById('drawHint').style.display = 'none';
        document.getElementById('drawAreaBtn').classList.remove('active');
        document.getElementById('drawLineBtn').classList.remove('active');
        if (mode === 'area' && pts.length >= 3) { Anim.aoi = pts.slice(); Anim.tripwire = null; }
        else if (mode === 'line' && pts.length >= 2) { Anim.tripwire = pts.slice(0, 2); Anim.aoi = null; }
        else { renderDrawLayer(false); return; }
        document.getElementById('drawClearBtn').style.display = 'inline-block';
        renderDrawLayer(false);
        recomputeAreaMetrics();
        syncUrl();
    }

    function clearDraw(silent) {
        Anim.drawMode = null; Anim.drawPts = []; Anim.aoi = null; Anim.tripwire = null;
        if (Anim.drawLayer) Anim.drawLayer.clearLayers();
        if (Anim.aoiChart) { Anim.aoiChart.destroy(); Anim.aoiChart = null; }
        document.getElementById('aoiPanel').style.display = 'none';
        document.getElementById('drawClearBtn').style.display = 'none';
        document.getElementById('drawHint').style.display = 'none';
        document.getElementById('drawAreaBtn').classList.remove('active');
        document.getElementById('drawLineBtn').classList.remove('active');
        if (map && map.doubleClickZoom) map.doubleClickZoom.enable();
        if (!silent) syncUrl();
    }

    function renderDrawLayer(preview) {
        if (!Anim.drawLayer) return;
        Anim.drawLayer.clearLayers();
        const style = { color: '#00ff88', weight: 2, dashArray: '6,5', fillColor: '#00ff88', fillOpacity: 0.08 };
        if (preview && Anim.drawPts.length) {
            if (Anim.drawMode === 'area') L.polyline(Anim.drawPts, style).addTo(Anim.drawLayer);
            else L.polyline(Anim.drawPts, style).addTo(Anim.drawLayer);
            Anim.drawPts.forEach(p => L.circleMarker(p, { radius: 3, color: '#00ff88', fillOpacity: 1 }).addTo(Anim.drawLayer));
            return;
        }
        if (Anim.aoi) L.polygon(Anim.aoi, style).addTo(Anim.drawLayer);
        if (Anim.tripwire) L.polyline(Anim.tripwire, { color: '#00ff88', weight: 2, dashArray: '6,5' }).addTo(Anim.drawLayer);
    }

    function frameGapHrs() {
        const n = Anim.filteredFrames.length;
        if (n < 2) return 2;
        const span = (Date.parse(Anim.filteredFrames[n - 1].timestamp) - Date.parse(Anim.filteredFrames[0].timestamp)) / 3600e3;
        return span / (n - 1);
    }

    function recomputeAreaMetrics() {
        if (Anim.aoi) renderAoiMetrics();
        else if (Anim.tripwire) renderTripwireMetrics();
        else document.getElementById('aoiPanel').style.display = 'none';
    }

    function renderAoiMetrics() {
        buildVesselIndex();
        const ring = Anim.aoi;
        const n = Anim.filteredFrames.length;
        const series = new Array(n).fill(0);
        const entered = new Map();
        Detection.vesselIndex.forEach((recs, mmsi) => {
            let any = false;
            recs.forEach(r => { if (pointInPolygon(r.lat, r.lon, ring)) { series[r.frameIdx]++; any = true; } });
            if (any) entered.set(mmsi, recs[0].type_name || 'unknown');
        });
        const peak = series.reduce((a, b) => Math.max(a, b), 0);
        const dwell = series.reduce((a, b) => a + b, 0) * frameGapHrs();
        const byType = {};
        entered.forEach(t => { byType[t] = (byType[t] || 0) + 1; });

        document.getElementById('aoiTitle').textContent = i18n.t('ais_anim.aoi_title');
        const typeRow = Object.entries(byType).sort((a, b) => b[1] - a[1])
            .map(([t, c]) => '<span class="detect-tag" style="background:rgba(0,245,255,0.12);color:' +
                (VESSEL_COLORS[t] || '#8aa4c8') + '">' + t + ' ' + c + '</span>').join(' ');
        document.getElementById('aoiBody').innerHTML =
            '<div class="aoi-metrics">' +
            '<div class="aoi-metric"><div class="v">' + entered.size + '</div><div class="l">' + i18n.t('ais_anim.aoi_vessels') + '</div></div>' +
            '<div class="aoi-metric"><div class="v">' + Math.round(dwell) + '</div><div class="l">' + i18n.t('ais_anim.aoi_dwell') + ' (h)</div></div>' +
            '<div class="aoi-metric"><div class="v">' + peak + '</div><div class="l">' + i18n.t('ais_anim.aoi_peak') + '</div></div>' +
            '</div>' +
            (typeRow ? '<div class="case-row"><span class="k">' + i18n.t('ais_anim.aoi_bytype') + '</span>' + typeRow + '</div>' : '') +
            (entered.size ? '<div class="case-row"><span class="k">' + i18n.t('ais_anim.aoi_chart') + '</span><div class="aoi-chart-box"><canvas id="aoiChartCanvas"></canvas></div></div>'
                : '<div class="case-row" style="color:#8aa4c8">' + i18n.t('ais_anim.aoi_none') + '</div>');
        document.getElementById('aoiPanel').style.display = 'block';
        if (entered.size) buildAoiChart(series);
    }

    function buildAoiChart(series) {
        if (Anim.aoiChart) { Anim.aoiChart.destroy(); Anim.aoiChart = null; }
        const canvas = document.getElementById('aoiChartCanvas');
        if (!canvas) return;
        const labels = Anim.filteredFrames.map(f => (f.timestamp || '').slice(5, 13).replace('T', ' '));
        Anim.aoiChart = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: { labels, datasets: [{ data: series, backgroundColor: '#00ff88aa', borderWidth: 0 }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                onClick: (e, els) => { if (els.length) { pause(); renderFrame(els[0].index); } },
                scales: {
                    x: { ticks: { color: '#8aa4c8', font: { size: 6 }, maxTicksLimit: 8 }, grid: { display: false } },
                    y: { ticks: { color: '#8aa4c8', font: { size: 8 }, precision: 0 }, grid: { color: 'rgba(0,245,255,0.05)' } }
                }
            }
        });
    }

    function renderTripwireMetrics() {
        buildVesselIndex();
        const tw = Anim.tripwire;
        const crossings = [];
        Detection.vesselIndex.forEach((recs, mmsi) => {
            const sorted = recs.slice().sort((a, b) => a.frameIdx - b.frameIdx);
            for (let i = 1; i < sorted.length; i++) {
                if (segIntersect([sorted[i - 1].lat, sorted[i - 1].lon], [sorted[i].lat, sorted[i].lon], tw[0], tw[1])) {
                    crossings.push({ mmsi, name: sorted[i].name, frameIdx: sorted[i].frameIdx, type: sorted[i].type_name });
                }
            }
        });
        crossings.sort((a, b) => a.frameIdx - b.frameIdx);
        document.getElementById('aoiTitle').textContent = i18n.t('ais_anim.tripwire_title');
        const list = crossings.slice(0, 40).map(c => {
            const ts = (Anim.filteredFrames[c.frameIdx].timestamp || '').slice(5, 16).replace('T', ' ');
            return '<div class="case-evt" onclick="pause();renderFrame(' + c.frameIdx + ')">' +
                '<span class="ce-dot" style="background:' + (VESSEL_COLORS[c.type] || '#8aa4c8') + '"></span>' +
                '<span class="ce-time">' + ts + '</span><span>' + (c.name || c.mmsi) + '</span></div>';
        }).join('');
        document.getElementById('aoiBody').innerHTML =
            '<div class="aoi-metrics"><div class="aoi-metric" style="grid-column:span 3">' +
            '<div class="v">' + crossings.length + '</div><div class="l">' + i18n.t('ais_anim.tripwire_count') + '</div></div></div>' +
            (crossings.length ? list : '<div class="case-row" style="color:#8aa4c8">' + i18n.t('ais_anim.aoi_none') + '</div>');
        document.getElementById('aoiPanel').style.display = 'block';
    }

    // =========================================================================
    // Feature 2 — Reproducible URL state + export
    // =========================================================================
    let _urlTimer = null;
    function syncUrl() {
        clearTimeout(_urlTimer);
        _urlTimer = setTimeout(() => {
            const p = new URLSearchParams();
            if (Anim.selectedRange !== 14) p.set('range', Anim.selectedRange);
            if (Anim.vesselFilter !== 'all') p.set('filter', Anim.vesselFilter);
            if (!Anim.showEvents) p.set('events', '0');
            if (Anim.showDark) p.set('dark', '1');
            if (Anim.focusMmsi) p.set('focus', Anim.focusMmsi);
            if (Anim.currentIdx > 0) p.set('frame', Anim.currentIdx);
            if (Anim.aoi) p.set('aoi', 'area:' + encodeRing(Anim.aoi));
            else if (Anim.tripwire) p.set('aoi', 'line:' + encodeRing(Anim.tripwire));
            const qs = p.toString();
            history.replaceState(null, '', qs ? '?' + qs : location.pathname);
        }, 300);
    }
    function encodeRing(pts) { return pts.map(p => p[0].toFixed(4) + ',' + p[1].toFixed(4)).join(';'); }
    function decodeRing(s) {
        return s.split(';').map(pr => pr.split(',').map(Number)).filter(p => p.length === 2 && !isNaN(p[0]));
    }

    function applyUrlState() {
        const p = new URLSearchParams(location.search);
        const range = parseInt(p.get('range'));
        if ([1, 3, 7, 14].includes(range)) setRange(range);
        const filter = p.get('filter');
        if (filter) { const sel = document.getElementById('vesselFilter'); if (sel) sel.value = filter; setVesselFilter(filter); }
        if (p.get('events') === '0') { document.getElementById('showEvents').checked = false; toggleEvents(); }
        if (p.get('dark') === '1') { document.getElementById('showDark').checked = true; toggleDark(); }
        const aoi = p.get('aoi');
        if (aoi) {
            const [kind, body] = aoi.split(':');
            const pts = decodeRing(body || '');
            if (kind === 'area' && pts.length >= 3) Anim.aoi = pts;
            else if (kind === 'line' && pts.length >= 2) Anim.tripwire = pts.slice(0, 2);
            if (Anim.aoi || Anim.tripwire) { renderDrawLayer(false); document.getElementById('drawClearBtn').style.display = 'inline-block'; recomputeAreaMetrics(); }
        }
        const focus = p.get('focus');
        if (focus) enterFocus(focus);
        const frame = parseInt(p.get('frame'));
        if (!isNaN(frame) && frame >= 0) renderFrame(Math.min(frame, Anim.filteredFrames.length - 1));
    }

    function copyShareLink() {
        const btn = document.getElementById('copyLinkBtn');
        const done = () => { btn.textContent = i18n.t('ais_anim.copied'); setTimeout(() => btn.textContent = i18n.t('ais_anim.copy_link'), 1500); };
        if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(done).catch(done);
        else done();
    }

    function buildTrackRecords(mmsi) {
        buildVesselIndex();
        const recs = (Detection.vesselIndex.get(mmsi) || []).slice().sort((a, b) => a.frameIdx - b.frameIdx);
        return recs.map(r => ({
            ts: Anim.filteredFrames[r.frameIdx].timestamp,
            lat: r.lat, lon: r.lon, speed: r.speed, heading: r.heading
        }));
    }

    function downloadBlob(filename, text, type) {
        const blob = new Blob([text], { type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function doExport(format) {
        const sel = document.getElementById('exportSelect');
        if (sel) sel.value = '';
        if (!format) return;
        const mmsi = Anim.focusMmsi;
        if (!mmsi) { alert(i18n.t('ais_anim.export_need_focus')); return; }
        const recs = buildTrackRecords(mmsi);
        const prof = Gray.profiles.get(mmsi) || {};
        const name = (prof.names && prof.names[prof.names.length - 1]) || (Anim.vesselTrails[mmsi] || {}).name || mmsi;
        const base = 'track_' + mmsi + '_' + Anim.selectedRange + 'd';
        if (format === 'csv') {
            const rows = [['timestamp', 'lat', 'lon', 'speed_kn', 'heading_deg']];
            recs.forEach(r => rows.push([r.ts, r.lat, r.lon, r.speed != null ? r.speed : '', r.heading != null ? r.heading : '']));
            downloadBlob(base + '.csv', rows.map(r => r.join(',')).join('\n'), 'text/csv');
        } else {
            const features = [{
                type: 'Feature',
                geometry: { type: 'LineString', coordinates: recs.map(r => [r.lon, r.lat]) },
                properties: { mmsi, name, risk_score: prof.risk_score, risk_level: prof.risk_level, flags: prof.flags || [] }
            }];
            Gray.events.filter(e => e.mmsis.includes(mmsi)).forEach(e => features.push({
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
                properties: { event: e.type, label: e.label, detail: e.detail, timestamp: e.ts }
            }));
            downloadBlob(base + '.geojson', JSON.stringify({ type: 'FeatureCollection', features }, null, 1), 'application/geo+json');
        }
    }

    // Re-render JS-built strings when language changes
    window.addEventListener('langchange', () => {
        refreshBehTitles();
        const reopen = document.getElementById('gzReopen');
        if (reopen) reopen.textContent = i18n.t('ais_anim.intro_reopen');
        renderTimelineEvents();
        if (Gray.loaded) renderEventLog();
        if (Anim.filteredFrames.length) renderFrame(Anim.currentIdx);
        if (Anim.focusMmsi) renderCaseNarrative(Anim.focusMmsi);
        if (Anim.aoi || Anim.tripwire) recomputeAreaMetrics();
        const hint = document.getElementById('drawHint');
        if (Anim.drawMode && hint) hint.textContent = i18n.t(Anim.drawMode === 'area' ? 'ais_anim.draw_hint_area' : 'ais_anim.draw_hint_line');
    });

    // =========================================================================
    // Keyboard shortcuts
    // =========================================================================
    document.addEventListener('keydown', function (e) {
        if (e.target.tagName === 'INPUT') return;
        if (e.key === 'Enter' && Anim.drawMode) { finishDraw(); return; }
        switch (e.key) {
            case ' ':
                e.preventDefault();
                togglePlay();
                break;
            case 'ArrowRight':
                stepForward();
                break;
            case 'ArrowLeft':
                stepBackward();
                break;
        }
    });

    // =========================================================================
    // Initialize (wait for deferred Leaflet / modules to load)
    // =========================================================================
    document.addEventListener('DOMContentLoaded', function () {
        initMap();
        initIntro();
        initBehaviorTips();
        document.getElementById('caseExit').addEventListener('click', exitFocus);
        // Collapse the researcher-tools group by default on small screens.
        if (window.matchMedia('(max-width: 900px)').matches) toggleTools();
        loadData();
    });

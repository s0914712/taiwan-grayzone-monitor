/**
 * Taiwan Gray Zone Monitor - Chinese fishing vessel animation page logic
 * Extracted verbatim from the page's former inline <script> block.
 * Loaded with defer after i18n.js; bootstraps on DOMContentLoaded.
 */

    // =========================================================================
    // Fishing hotspots (same as fetch_ais_data.py)
    // =========================================================================
    const FISHING_HOTSPOTS = {
        taiwan_bank:   { name: '台灣灘漁場',   bounds: [[22.0, 117.0], [23.5, 119.5]] },
        penghu:        { name: '澎湖漁場',     bounds: [[23.0, 119.0], [24.0, 120.0]] },
        kuroshio_east: { name: '東部黑潮漁場', bounds: [[22.5, 121.0], [24.5, 122.0]] },
        northeast:     { name: '東北漁場',     bounds: [[24.8, 121.5], [25.8, 123.0]] },
        southwest:     { name: '西南沿岸漁場', bounds: [[22.0, 120.0], [23.0, 120.8]] }
    };

    // =========================================================================
    // PLA Sorties & Monitoring Zone
    // =========================================================================
    const CSV_URL = 'https://raw.githubusercontent.com/s0914712/pla-data-dashboard/main/data/JapanandBattleship.csv';
    const MONITOR_ZONE = {
        name: '監控區',
        south: 25.4, north: 26.1,
        west: 121.5, east: 122.5
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
        selectedRange: 7,
        markerLayer: null,
        trailLayer: null,
        showTrails: true,
        vesselFilter: 'all',  // 'all', 'cn_fishing', 'suspicious'
        chart: null,
        vesselTrails: {},  // mmsi -> [{lat, lon, timestamp}, ...]
        sortiesDaily: {},  // date -> number (PLA aircraft sorties)
        navalDaily: {},    // date -> number (PLA naval sorties)
        sortiesChart: null // Chart.js instance for sorties
    };

    let map;

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
                    suspicious: v.suspicious
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
        return tags;
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

        // Monitoring zone rectangle (NE Taiwan)
        L.rectangle([
            [MONITOR_ZONE.south, MONITOR_ZONE.west],
            [MONITOR_ZONE.north, MONITOR_ZONE.east]
        ], {
            color: '#ffd700', weight: 2, opacity: 0.7,
            fillColor: '#ffd700', fillOpacity: 0.08,
            dashArray: '8,5'
        }).addTo(map).bindTooltip(MONITOR_ZONE.name, { permanent: false, direction: 'center' });

        Anim.markerLayer = L.layerGroup().addTo(map);
        Anim.trailLayer = L.layerGroup().addTo(map);
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

    function parseCSV(text) {
        const lines = text.split('\n');
        if (lines.length < 2) return;
        const header = lines[0].replace(/^\uFEFF/, '').split(',');
        const dateIdx = header.indexOf('date');
        const sortiesIdx = header.indexOf('pla_aircraft_sorties');
        const navalIdx = header.indexOf('plan_vessel_sorties');
        if (dateIdx < 0 || sortiesIdx < 0) return;
        for (let i = 1; i < lines.length; i++) {
            const cols = lines[i].split(',');
            if (cols.length <= sortiesIdx) continue;
            const rawDate = (cols[dateIdx] || '').trim();
            if (!rawDate) continue;
            const date = rawDate.replace(/\//g, '-');
            const sorties = parseFloat(cols[sortiesIdx]) || 0;
            const naval = navalIdx >= 0 ? (parseFloat(cols[navalIdx]) || 0) : 0;
            Anim.sortiesDaily[date] = sorties;
            Anim.navalDaily[date] = naval;
        }
    }

    function buildSortiesChart() {
        const dates = Object.keys(Anim.sortiesDaily).sort();
        if (dates.length === 0) return;

        // Show the panels
        document.getElementById('sortiesStats').style.display = 'grid';
        document.getElementById('sortiesChartPanel').style.display = '';

        const labels = dates.map(d => d.slice(5)); // "03-01" etc
        const data = dates.map(d => Anim.sortiesDaily[d] || 0);

        const ctx = document.getElementById('sortiesChart').getContext('2d');
        Anim.sortiesChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: '共機架次',
                    data: data,
                    backgroundColor: data.map(v => {
                        if (v >= 20) return 'rgba(255, 51, 102, 0.7)';
                        if (v >= 10) return 'rgba(255, 165, 0, 0.6)';
                        if (v > 0)  return 'rgba(0, 245, 255, 0.5)';
                        return 'rgba(100, 120, 160, 0.2)';
                    }),
                    borderColor: 'rgba(0, 245, 255, 0.3)',
                    borderWidth: 1,
                    borderRadius: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: items => items[0].label,
                            label: item => '共機架次: ' + item.raw
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#8aa4c8', font: { size: 8, family: 'JetBrains Mono' } },
                        grid: { color: 'rgba(0,245,255,0.05)' }
                    },
                    y: {
                        ticks: { color: '#8aa4c8', font: { size: 9, family: 'JetBrains Mono' } },
                        grid: { color: 'rgba(0,245,255,0.05)' },
                        title: { display: true, text: '架次', color: '#8aa4c8', font: { size: 10 } }
                    }
                }
            }
        });
    }

    async function loadData() {
        try {
            document.getElementById('loadingDetail').textContent =
                '正在下載 AIS 軌跡資料...';

            // Load AIS data and PLA CSV in parallel
            // 優先抓 7 天動畫精簡檔；尚未產生時 fallback 到 14 天完整檔
            let [res, csvRes] = await Promise.all([
                fetch('ais_track_animation.json?' + Date.now()),
                fetch(CSV_URL).catch(() => null)
            ]);
            if (!res.ok) res = await fetch('ais_track_history.json?' + Date.now());
            if (!res.ok) throw new Error('HTTP ' + res.status);

            // Parse CSV sorties data
            if (csvRes && csvRes.ok) {
                const csvText = await csvRes.text();
                parseCSV(csvText);
            }

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
            buildSortiesChart();

            const first = data[0].timestamp || '';
            const last = data[data.length - 1].timestamp || '';
            document.getElementById('dataStatus').textContent =
                '\u2705 ' + first.slice(0, 10) + ' ~ ' + last.slice(0, 10);
            document.getElementById('updateInfo').textContent =
                i18n.t('common.updated') + ' ' + new Date(last).toLocaleString();

            hideLoading();

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
        renderFrame(0);
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
                if (!isCnFishingVessel(v)) return; // only track CN fishing vessels
                if (!Anim.vesselTrails[v.mmsi]) {
                    Anim.vesselTrails[v.mmsi] = { points: [], suspicious: v.suspicious, name: v.name, color: getProvinceColor(v) };
                }
                Anim.vesselTrails[v.mmsi].points.push([v.lat, v.lon]);
                Anim.vesselTrails[v.mmsi].suspicious = v.suspicious;
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

    // Create MarineTraffic-style arrow SVG icon for vessel marker
    function createVesselIcon(color, isSuspicious, heading) {
        const w = isSuspicious ? 10 : 7;
        const h = isSuspicious ? 20 : 16;
        const rotation = heading !== null ? heading : 0;
        const opacity = isSuspicious ? 0.85 : 0.7;
        const sw = isSuspicious ? 1.5 : 0.8;

        let shape;
        if (heading !== null) {
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

    // =========================================================================
    // Frame rendering
    // =========================================================================
    function renderFrame(idx) {
        if (idx < 0 || idx >= Anim.filteredFrames.length) return;
        Anim.currentIdx = idx;

        const frame = Anim.filteredFrames[idx];
        const allVessels = frame.vessels || [];
        const vessels = filterVessels(allVessels);

        // Clear layers
        Anim.markerLayer.clearLayers();
        Anim.trailLayer.clearLayers();

        // Always build trails (needed for heading computation)
        buildTrails(idx);

        // Draw trail lines if enabled
        if (Anim.showTrails) {
            Object.entries(Anim.vesselTrails).forEach(([mmsi, trail]) => {
                if (trail.points.length < 2) return;
                const color = trail.color || '#9370db';
                L.polyline(trail.points, {
                    color: color,
                    weight: 0.8,
                    opacity: 0.25,
                    dashArray: '4,6'
                }).addTo(Anim.trailLayer);
            });
        }

        // Plot vessel markers with province colors
        vessels.forEach(v => {
            const color = getProvinceColor(v);
            const provName = getProvinceName(v);
            const heading = computeHeading(v.mmsi, v.lat, v.lon) ?? (v.heading !== undefined ? v.heading : null);
            const isSuspicious = v.suspicious;

            const icon = createVesselIcon(color, isSuspicious, heading);
            const marker = L.marker([v.lat, v.lon], { icon: icon }).addTo(Anim.markerLayer);

            const headingText = heading !== null ? heading.toFixed(0) + '°' : 'N/A';
            marker.bindPopup(
                '<div style="font-size:14px;line-height:1.8;min-width:180px">' +
                '<b style="color:' + color + ';font-size:15px">' + (v.name || v.mmsi) + '</b><br>' +
                '<span style="color:' + color + ';font-weight:600">省份: ' + provName + '</span><br>' +
                'MMSI: ' + v.mmsi + '<br>' +
                ((v.mmsi || '').startsWith('898') ? '<span style="color:#8aa4c8;font-weight:600">🎣 可能為魚網標記</span><br>' : '') +
                i18n.t('ais_anim.popup_speed') + ': ' + (v.speed || 0).toFixed(1) + ' kn<br>' +
                '航向: ' + headingText + '<br>' +
                (isSuspicious ? '<span style="color:#ff3366;font-weight:600;font-size:14px">⚠ SUSPICIOUS</span>' : '') +
                '</div>'
            );
        });

        // Update UI
        updateDayIndicator(frame, idx);
        updateStatCards(frame, idx);
        updateVesselTable(vessels);
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
        // Update PLA sorties stats if data available
        if (Object.keys(Anim.sortiesDaily).length === 0) return;
        const ts = (frame.timestamp || '').slice(0, 10);
        const sorties = Anim.sortiesDaily[ts] || 0;
        const naval = Anim.navalDaily[ts] || 0;

        // Count vessels in monitoring zone
        const vessels = frame.vessels || [];
        let zoneCount = 0;
        vessels.forEach(v => {
            if (v.lat >= MONITOR_ZONE.south && v.lat <= MONITOR_ZONE.north &&
                v.lon >= MONITOR_ZONE.west && v.lon <= MONITOR_ZONE.east) {
                zoneCount++;
            }
        });

        const elSorties = document.getElementById('statSorties');
        const elNaval = document.getElementById('statNaval');
        const elZone = document.getElementById('statZone');
        if (elSorties) {
            elSorties.textContent = sorties;
            elSorties.className = sorties >= 20 ? 'value alert' : 'value';
        }
        if (elNaval) elNaval.textContent = naval;
        if (elZone) {
            elZone.textContent = zoneCount;
            elZone.className = zoneCount > 50 ? 'value alert' : 'value';
        }
    }

    function updateVesselTable(vessels) {
        const tbody = document.getElementById('vesselTableBody');
        if (!vessels || vessels.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">' + i18n.t('ais_anim.no_data_msg') + '</td></tr>';
            return;
        }

        // Sort: suspicious first, then by speed descending
        const sorted = [...vessels].sort((a, b) => {
            if (a.suspicious !== b.suspicious) return b.suspicious ? 1 : -1;
            return (b.speed || 0) - (a.speed || 0);
        });

        const rows = sorted.slice(0, 30).map(v => {
            const isSusp = v.suspicious;
            const provColor = getProvinceColor(v);
            const provName = getProvinceName(v);
            const statusHtml = isSusp ? '<span class="suspicious-tag">⚠</span>' : 'OK';
            return '<tr>' +
                '<td>' + v.mmsi + '</td>' +
                '<td>' + (v.name || '--') + '</td>' +
                '<td><span style="color:' + provColor + ';font-weight:600">' + provName + '</span></td>' +
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
        document.getElementById('daySlider').value = idx;
    }

    document.getElementById('daySlider').addEventListener('input', function () {
        pause();
        renderFrame(parseInt(this.value));
    });

    // =========================================================================
    // Chart
    // =========================================================================
    function updateChart() {
        const ctx = document.getElementById('dailyChart').getContext('2d');
        if (Anim.chart) Anim.chart.destroy();

        const labels = Anim.filteredFrames.map(f => (f.timestamp || '').slice(5, 16).replace('T', ' '));
        const data = Anim.filteredFrames.map(f => {
            const vessels = f.vessels || [];
            return vessels.filter(v => isCnFishingVessel(v)).length;
        });

        Anim.chart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: '大陸漁船',
                    data: data,
                    backgroundColor: data.map((_, i) =>
                        i === Anim.currentIdx ? 'rgba(255, 99, 71, 0.8)' : 'rgba(255, 99, 71, 0.4)'
                    ),
                    borderColor: data.map((_, i) =>
                        i === Anim.currentIdx ? '#ff6347' : 'rgba(255, 99, 71, 0.6)'
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
            i === idx ? 'rgba(255, 99, 71, 0.8)' : 'rgba(255, 99, 71, 0.4)'
        );
        dataset.borderColor = dataset.data.map((_, i) =>
            i === idx ? '#ff6347' : 'rgba(255, 99, 71, 0.6)'
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

    // =========================================================================
    // Province detection and color mapping
    // =========================================================================
    const PROVINCE_PATTERNS = {
        fujian:    { patterns: [/^MIN/i, /^闽/i, /^閩/i], name: '福建', color: '#ff6347' },
        zhejiang:  { patterns: [/^ZHE/i, /^浙/i], name: '浙江', color: '#4169e1' },
        guangdong: { patterns: [/^YUE/i, /^粤/i, /^粵/i], name: '廣東', color: '#ffa500' },
        shandong:  { patterns: [/^LU\s?YU/i, /^鲁/i, /^魯/i], name: '山東', color: '#32cd32' },
        hainan:    { patterns: [/^QIONG/i, /^琼/i, /^瓊/i], name: '海南', color: '#ff69b4' },
        jiangsu:   { patterns: [/^SU\s?YU/i, /^苏/i, /^蘇/i], name: '江蘇', color: '#00ced1' },
        guangxi:   { patterns: [/^GUI/i, /^桂/i], name: '廣西', color: '#daa520' },
        hunan:     { patterns: [/^XIANG/i, /^湘/i], name: '湖南', color: '#6b8e23' },
        tianjin:   { patterns: [/^JIN\s?YU/i, /^津/i], name: '天津', color: '#708090' },
        liaoning:  { patterns: [/^LIAO/i, /^辽/i, /^遼/i], name: '遼寧', color: '#b0c4de' },
    };

    function getProvince(name) {
        const n = (name || '').toUpperCase();
        for (const [key, prov] of Object.entries(PROVINCE_PATTERNS)) {
            if (prov.patterns.some(p => p.test(n))) return key;
        }
        return null;
    }

    function isCnFishingVessel(v) {
        const name = (v.name || '').toUpperCase();
        if (getProvince(name)) return true;
        if (/YU[.\s]*\d/i.test(name)) return true;
        return false;
    }

    function getProvinceColor(v) {
        const prov = getProvince(v.name);
        if (prov && PROVINCE_PATTERNS[prov]) return PROVINCE_PATTERNS[prov].color;
        return '#9370db'; // other/unknown province
    }

    function getProvinceName(v) {
        const prov = getProvince(v.name);
        if (prov && PROVINCE_PATTERNS[prov]) return PROVINCE_PATTERNS[prov].name;
        return '其他';
    }

    // Always filter to CN fishing first, then by province
    function filterVessels(vessels) {
        const cnVessels = vessels.filter(v => isCnFishingVessel(v));
        const filter = Anim.vesselFilter;
        if (filter === 'all') return cnVessels;
        if (filter === 'other_cn') {
            return cnVessels.filter(v => {
                const prov = getProvince(v.name);
                return !prov || !['fujian','zhejiang','guangdong','shandong','hainan','jiangsu','guangxi'].includes(prov);
            });
        }
        // Province filter
        return cnVessels.filter(v => getProvince(v.name) === filter);
    }

    function setVesselFilter(filter) {
        Anim.vesselFilter = filter;
        renderFrame(Anim.currentIdx);
    }

    // =========================================================================
    // Keyboard shortcuts
    // =========================================================================
    document.addEventListener('keydown', function (e) {
        if (e.target.tagName === 'INPUT') return;
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
        loadData();
    });

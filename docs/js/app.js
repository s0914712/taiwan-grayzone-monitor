/**
 * Taiwan Gray Zone Monitor - Main Application
 * Coordinates map, charts, and UI components
 */

const App = (function () {
    'use strict';

    // State
    let vessels = new Map();
    let rawVesselList = []; // raw AIS vessel array for re-rendering on filter change
    let suspiciousData = null;
    let latestData = null;  // last data.json payload (feeds the brief + metric tiles)
    let cableCounts = null; // { faults, repaired } from cable_status.json

    /**
     * Initialize the application
     */
    function init() {
        // Initialize map
        MapModule.init('map');
        MapModule.drawFishingHotspots();

        // Initialize UI
        setupEventListeners();
        setupMobileNavigation();

        // Load submarine cables by default
        MapModule.loadSubmarineCables().then(() => {
            MapModule.toggleLayer('submarineCables', true);
        });

        // Load data
        loadData();

        // Deep link: ?mmsi=<id> auto-loads that vessel's route (shareable permalink)
        applyMmsiDeepLink();
    }

    /**
     * If the URL has ?mmsi=<5-9 digits>, populate the search box and load the
     * vessel's route automatically (used for shareable per-vessel permalinks).
     */
    function applyMmsiDeepLink() {
        try {
            var mmsi = new URLSearchParams(window.location.search).get('mmsi');
            if (!mmsi || !/^\d{5,9}$/.test(mmsi)) return;
            var input = document.getElementById('mmsiSearchInput');
            if (input) input.value = mmsi;
            if (typeof MapModule !== 'undefined' && MapModule.loadVesselRoute) {
                MapModule.loadVesselRoute(mmsi);
            }
        } catch (_) { /* ignore */ }
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners() {
        // Layer toggle checkboxes
        ['fishingHotspots', 'vessels', 'darkVessels', 'vesselRoutes'].forEach(layer => {
            const checkbox = document.getElementById('show' + layer.charAt(0).toUpperCase() + layer.slice(1));
            if (checkbox) {
                checkbox.addEventListener('change', () => {
                    MapModule.toggleLayer(layer, checkbox.checked);
                });
            }
        });

        // Submarine cable layer toggle (lazy-load on first enable)
        const cableCheckbox = document.getElementById('showSubmarineCables');
        if (cableCheckbox) {
            cableCheckbox.addEventListener('change', async () => {
                if (cableCheckbox.checked) {
                    await MapModule.loadSubmarineCables();
                }
                MapModule.toggleLayer('submarineCables', cableCheckbox.checked);
            });
        }

        // Territorial baseline layer toggle (lazy-draw on first enable)
        const baselineCheckbox = document.getElementById('showTerritorialBaseline');
        if (baselineCheckbox) {
            let baselineDrawn = false;
            baselineCheckbox.addEventListener('change', () => {
                if (baselineCheckbox.checked && !baselineDrawn) {
                    MapModule.drawTerritorialBaseline();
                    baselineDrawn = true;
                }
                MapModule.toggleLayer('territorialBaseline', baselineCheckbox.checked);
            });
        }

        // Seascape bathymetry layer toggle (lazy-load TileJSON on first enable)
        const bathyCheckbox = document.getElementById('showBathymetry');
        if (bathyCheckbox) {
            bathyCheckbox.addEventListener('change', async () => {
                if (bathyCheckbox.checked) {
                    await MapModule.loadBathymetry();
                }
                MapModule.toggleLayer('bathymetry', bathyCheckbox.checked);
            });
        }

        // Legend item click -> locate vessel type on map
        document.querySelectorAll('.legend-clickable').forEach(item => {
            item.addEventListener('click', () => {
                const type = item.getAttribute('data-vessel-type');
                if (type) MapModule.locateVesselType(type);
            });
        });

        // Map pane "圖層" button collapses the on-map layer panel (desktop)
        const layerBtn = document.getElementById('layerPanelToggle');
        const layerPanel = document.getElementById('layerToggles');
        if (layerBtn && layerPanel) {
            layerBtn.addEventListener('click', () => {
                const expanded = layerBtn.getAttribute('aria-expanded') === 'true';
                layerBtn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
                layerPanel.classList.toggle('is-hidden', expanded);
            });
        }

        setupMetricPanels();

        // FOC commercial vessel filter
        const focCheckbox = document.getElementById('filterFocVessels');
        if (focCheckbox) {
            focCheckbox.addEventListener('change', () => {
                MapModule.setFilterFoc(focCheckbox.checked);
                if (rawVesselList.length > 0) {
                    const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                    vessels = result.vessels;
                    updateGovVesselList();
                }
            });
        }
    }

    /**
     * Metric tiles ↔ slide-out detail panels (desktop homepage).
     *
     * The old right-hand sidebar lives in these panels now. Hover opens the
     * matching one (short delays so sweeping across the row doesn't flicker),
     * click pins it so it survives mouse-out, Escape / outside-click closes.
     * Touch pointers skip hover entirely — a tap is the toggle. Keyboard users
     * get the same behaviour for free: the tiles are real <button>s.
     */
    function setupMetricPanels() {
        const row = document.querySelector('.metrics-row');
        const panels = document.getElementById('metricPanels');
        if (!row || !panels) return; // mobile shell / other pages

        const cards = Array.from(row.querySelectorAll('.metric-card[data-panel]'));
        const OPEN_DELAY = 120;
        const CLOSE_DELAY = 250;
        let openKey = null;
        let pinnedKey = null;
        let timer = null;

        const panelOf = (key) => document.getElementById('panel-' + key);
        const cardOf = (key) => row.querySelector('.metric-card[data-panel="' + key + '"]');
        const clearTimer = () => { if (timer) { clearTimeout(timer); timer = null; } };

        function apply(key) {
            if (openKey === key) return;
            openKey = key;
            cards.forEach((card) => {
                const k = card.getAttribute('data-panel');
                const isOpen = k === key;
                const panel = panelOf(k);
                card.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
                if (panel) {
                    panel.classList.toggle('is-open', isOpen);
                    panel.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
                }
            });
        }

        function open(key, immediate) {
            const card = cardOf(key);
            if (!card || card.disabled) return;
            clearTimer();
            if (immediate || openKey !== null) { apply(key); return; }
            timer = setTimeout(() => { timer = null; apply(key); }, OPEN_DELAY);
        }

        function close(immediate) {
            clearTimer();
            if (immediate) { pinnedKey = null; apply(null); return; }
            timer = setTimeout(() => { timer = null; if (!pinnedKey) apply(null); }, CLOSE_DELAY);
        }

        cards.forEach((card) => {
            const key = card.getAttribute('data-panel');
            const panel = panelOf(key);

            card.addEventListener('mouseenter', (e) => {
                if (e.sourceCapabilities && e.sourceCapabilities.firesTouchEvents) return;
                if (pinnedKey) return; // a pinned panel stays put until unpinned
                open(key);
            });
            card.addEventListener('mouseleave', () => { if (!pinnedKey) close(); });

            // Click (and Enter/Space, which buttons turn into click) pins/unpins
            card.addEventListener('click', () => {
                if (pinnedKey === key) { pinnedKey = null; close(true); return; }
                pinnedKey = key;
                open(key, true);
            });

            if (panel) {
                panel.addEventListener('mouseenter', clearTimer);
                panel.addEventListener('mouseleave', () => { if (!pinnedKey) close(); });
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && openKey) close(true);
        });

        document.addEventListener('click', (e) => {
            if (!openKey) return;
            if (row.contains(e.target) || panels.contains(e.target)) return;
            close(true);
        });
    }

    /**
     * Inject index-specific content into the shared mobile navigation.
     * The bottom nav / popover / bottom sheet shell is built by mobile-nav.js
     * (loaded before app.js); on desktop viewports the shell doesn't exist
     * (mobile-nav.js bails out) and this is a no-op.
     */
    function setupMobileNavigation() {
        if (!window.MobileNav) return;

        const closeAll = window.MobileNav.closeAll;
        const hasMap = document.getElementById('map');
        let sheetHTML = '';

        // Route search section
        if (hasMap) {
            sheetHTML += `
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.route_search">航跡查詢</div>
                <div class="bs-route-search">
                    <input type="text" id="bsMmsiSearchInput" placeholder="MMSI" maxlength="9" inputmode="numeric" pattern="[0-9]*">
                    <button id="bsMmsiSearchBtn" data-i18n="app.search">查詢</button>
                </div>
            </div>`;
        }

        // Layer controls section
        if (hasMap) {
            sheetHTML += `
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.layers">圖層控制</div>
                <label class="layer-toggle"><input type="checkbox" id="bsShowFishingHotspots" checked> <span data-i18n="idx.layer_fishing">漁撈熱點</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowVessels" checked> <span data-i18n="idx.layer_vessels">AIS 船隻</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowDarkVessels" checked> <span data-i18n="idx.layer_dark">暗船 (SAR)</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowSubmarineCables" checked> <span data-i18n="ais_anim.layer_cables">海底電纜</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowVesselRoutes" checked> <span data-i18n="idx.layer_routes">船隻航跡</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowBathymetry"> <span data-i18n="idx.layer_bathymetry">海床水深 (Seascape)</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsFilterFocVessels"> <span data-i18n="idx.filter_foc">過濾權宜船</span></label>
            </div>`;
        }

        // Stats + gov/research vessels + suspicious sections
        sheetHTML += `
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.realtime_stats">即時統計</div>
                <div class="bs-stats-grid">
                    <div class="bs-stat-item"><div class="bs-stat-value" id="bsVesselCount">--</div><div class="bs-stat-label" data-i18n="idx.total_vessels">總船隻</div></div>
                    <div class="bs-stat-item"><div class="bs-stat-value" id="bsFishingCount">--</div><div class="bs-stat-label" data-i18n="vessel.fishing">漁船</div></div>
                    <div class="bs-stat-item"><div class="bs-stat-value" id="bsCargoCount">--</div><div class="bs-stat-label" data-i18n="vessel.cargo">貨船</div></div>
                    <div class="bs-stat-item"><div class="bs-stat-value alert" id="bsSuspCount">--</div><div class="bs-stat-label" data-i18n="idx.suspicious">可疑</div></div>
                </div>
            </div>
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="idx.gov_title">🛡️ 中國公務／科研船追蹤</div>
                <div id="bsGovList" class="bs-lng-list"><span style="color:var(--text-secondary);font-size:13px">載入中...</span></div>
            </div>
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.suspicious">可疑船隻</div>
                <div id="bsSuspiciousList"></div>
            </div>`;

        window.MobileNav.addSheetSection(sheetHTML);

        // Sync bottom sheet checkboxes with page controls
        if (hasMap) {
            syncCheckbox('bsShowFishingHotspots', 'showFishingHotspots', layer => MapModule.toggleLayer('fishingHotspots', layer));
            syncCheckbox('bsShowVessels', 'showVessels', layer => MapModule.toggleLayer('vessels', layer));
            syncCheckbox('bsShowDarkVessels', 'showDarkVessels', layer => MapModule.toggleLayer('darkVessels', layer));
            syncCheckbox('bsShowSubmarineCables', 'showSubmarineCables', async checked => {
                if (checked) await MapModule.loadSubmarineCables();
                MapModule.toggleLayer('submarineCables', checked);
            });
            syncCheckbox('bsShowVesselRoutes', 'showVesselRoutes', layer => MapModule.toggleLayer('vesselRoutes', layer));
            syncCheckbox('bsShowBathymetry', 'showBathymetry', async checked => {
                if (checked) await MapModule.loadBathymetry();
                MapModule.toggleLayer('bathymetry', checked);
            });
            syncCheckbox('bsShowTerritorialBaseline', 'showTerritorialBaseline', checked => {
                if (checked) MapModule.drawTerritorialBaseline();
                MapModule.toggleLayer('territorialBaseline', checked);
            });
            syncCheckbox('bsFilterFocVessels', 'filterFocVessels', checked => {
                MapModule.setFilterFoc(checked);
                if (rawVesselList.length > 0) {
                    const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                    vessels = result.vessels;
                    updateBottomSheetStats(result.stats);
                    updateGovVesselList();
                }
            });
        }

        // Wire bottom-sheet route search
        var bsMmsiInput = document.getElementById('bsMmsiSearchInput');
        var bsMmsiBtn = document.getElementById('bsMmsiSearchBtn');
        if (bsMmsiInput && bsMmsiBtn) {
            bsMmsiBtn.addEventListener('click', function() {
                var val = bsMmsiInput.value.trim();
                if (val) {
                    // Copy value to main input and trigger search
                    var mainInput = document.getElementById('mmsiSearchInput');
                    if (mainInput) mainInput.value = val;
                    closeAll();
                    MapModule.loadVesselRoute(val);
                }
            });
            bsMmsiInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') bsMmsiBtn.click();
            });
        }

        // (Drag-to-dismiss and open/close handlers live in mobile-nav.js)

        if (typeof i18n !== 'undefined') i18n.applyAll();
    }

    /**
     * Sync a bottom sheet checkbox with its page counterpart
     */
    function syncCheckbox(bsId, pageId, onChange) {
        const bsCb = document.getElementById(bsId);
        const pageCb = document.getElementById(pageId);
        if (!bsCb) return;

        // Sync initial state from page checkbox
        if (pageCb) bsCb.checked = pageCb.checked;

        bsCb.addEventListener('change', () => {
            if (pageCb) pageCb.checked = bsCb.checked;
            onChange(bsCb.checked);
        });
    }

    /**
     * Update bottom sheet stats display
     */
    function updateBottomSheetStats(stats) {
        const ids = { bsVesselCount: 'total', bsFishingCount: 'fishing', bsCargoCount: 'cargo', bsSuspCount: 'suspicious' };
        Object.entries(ids).forEach(([elId, key]) => {
            const el = document.getElementById(elId);
            if (el) el.textContent = stats[key] || 0;
        });
    }

    /**
     * Update bottom sheet suspicious list
     */
    function updateBottomSheetSuspicious() {
        const list = document.getElementById('bsSuspiciousList');
        if (!list || !suspiciousData || !suspiciousData.suspicious_vessels) return;

        list.innerHTML = suspiciousData.suspicious_vessels.slice(0, 5).map(sv => {
            const name = (sv.names && sv.names[0]) || sv.mmsi;
            return `<div class="bs-suspect-item" onclick="App.showVesselDetail('${sv.mmsi}')">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name.substring(0, 16)}</span>
                <span class="risk-badge risk-${sv.risk_level}">${sv.risk_level}</span>
            </div>`;
        }).join('');
    }

    /**
     * Update China gov / research vessel tracking list (metric panel + bottom sheet).
     * Scans the full AIS snapshot (rawVesselList) so vessels are listed even
     * when the map is zoomed out into cluster mode.
     */
    function updateGovVesselList() {
        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const getGov = MapModule.getGovType;
        const govLabel = MapModule.govLabel;
        const icon = MapModule.GOV_BADGE_ICON || {};

        const govVessels = (rawVesselList || [])
            .map(v => ({ v: v, cat: getGov(v) }))
            .filter(o => o.cat);

        // Order by category (coastguard → msa → rescue → research)
        const order = MapModule.GOV_TYPES || ['coastguard', 'msa', 'rescue', 'research'];
        govVessels.sort((a, b) => order.indexOf(a.cat) - order.indexOf(b.cat));

        const section = document.getElementById('govVesselSection');
        if (section) section.style.display = govVessels.length ? '' : 'none';

        const rowHtml = (o) => {
            const v = o.v;
            const color = MapModule.VESSEL_COLORS[o.cat] || MapModule.VESSEL_COLORS.other;
            return '<div class="suspicious-item" onclick="App.focusVessel(\'' + v.mmsi + '\')" ' +
                'title="' + govLabel(o.cat) + '">' +
                '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
                    (icon[o.cat] || '') + ' ' + (v.name || 'Unknown').substring(0, 16) +
                '</span>' +
                '<span class="risk-badge" style="background:' + color + ';color:#0a0f1c">' +
                    govLabel(o.cat) +
                '</span>' +
            '</div>';
        };

        const emptyMsg = '<div class="panel-empty">' + t('idx.gov_none') + '</div>';

        const sideList = document.getElementById('govVesselList');
        if (sideList) {
            sideList.innerHTML = govVessels.length
                ? govVessels.slice(0, 15).map(rowHtml).join('')
                : emptyMsg;
        }
        const bsList = document.getElementById('bsGovList');
        if (bsList) {
            bsList.innerHTML = govVessels.length
                ? govVessels.slice(0, 10).map(rowHtml).join('')
                : emptyMsg;
        }

        // Gov count feeds a metric tile; the FOC filter can change it
        updateBriefAndMetrics();
    }

    /**
     * Update the suspicious vessel list (inside the 可疑船舶 detail panel)
     */
    function updateSuspiciousList() {
        const list = document.getElementById('suspiciousList');
        if (!list) return;

        if (!suspiciousData || !suspiciousData.suspicious_vessels || suspiciousData.suspicious_vessels.length === 0) {
            const summary = suspiciousData && suspiciousData.summary;
            if (summary && summary.total_analyzed > 0) {
                const msg = typeof i18n !== 'undefined'
                    ? i18n.t('app.analyzed', summary.total_analyzed)
                    : `已分析 ${summary.total_analyzed} 艘，暫無達到門檻的可疑船隻`;
                list.innerHTML = `<div class="panel-empty">${msg}</div>`;
            }
            return;
        }

        list.innerHTML = suspiciousData.suspicious_vessels.slice(0, 10).map(sv => {
            const name = (sv.names && sv.names[0]) || sv.mmsi;
            const riskClass = 'risk-' + sv.risk_level;

            return `
                <div class="suspicious-item" onclick="App.showVesselDetail('${sv.mmsi}')">
                    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                        ${name.substring(0, 12)}
                    </span>
                    <span class="risk-badge ${riskClass}">${sv.risk_level}</span>
                </div>`;
        }).join('');
    }

    /**
     * Enable/disable a metric tile (a tile with an empty panel isn't expandable)
     */
    function setCardEnabled(key, enabled) {
        const card = document.querySelector('.metric-card[data-panel="' + key + '"]');
        if (!card) return;
        card.disabled = !enabled;
        if (!enabled) {
            card.setAttribute('aria-expanded', 'false');
            const panel = document.getElementById('panel-' + key);
            if (panel) {
                panel.classList.remove('is-open');
                panel.setAttribute('aria-hidden', 'true');
            }
        }
    }

    /**
     * SAR×AIS funnel inside the SAR 暗船 panel: how many raw detections survive
     * infrastructure filtering and local-AIS re-matching to stay "dark".
     */
    function renderDarkFunnel(dark, sar) {
        const el = document.getElementById('darkFunnel');
        if (!el) return;
        const t = (typeof i18n !== 'undefined') ? i18n.t.bind(i18n) : (k) => k;
        const num = (n) => (n || 0).toLocaleString();

        const rows = [
            ['', t('dark.funnel_total'), num(dark.total_detections || (sar && sar.dark_total))],
            ['', t('dark.funnel_dark'), num(dark.dark_vessels || (sar && sar.dark_total))],
        ];
        if (sar) {
            rows.push(['', t('dark.funnel_infra'), num(sar.infrastructure_filtered)]);
            rows.push(['is-cleared', t('dark.funnel_cleared'), (sar.false_dark_removed_pct || 0) + '%']);
            rows.push(['is-residual', t('dark.funnel_residual'), num(sar.residual_dark)]);
            if (sar.no_ais_coverage) {
                rows.push(['', t('dark.funnel_nocover'), num(sar.no_ais_coverage)]);
            }
        }

        el.innerHTML = rows.map(([cls, label, value]) =>
            '<div class="dark-funnel-row ' + cls + '">' +
            '<span class="dark-funnel-label">' + label + '</span>' +
            '<span class="dark-funnel-value">' + value + '</span>' +
            '</div>'
        ).join('');
    }

    /**
     * 今日態勢 + 今日關鍵指標（首頁電腦版的上半部）
     *
     * Everything here is read straight off the loaded datasets — no derived
     * "vs. 7-day average" style claims, because the homepage payload doesn't
     * carry a comparable historical baseline for these four numbers.
     *
     * Situation level (documented on the badge's title attribute):
     *   警戒 alert     — ≥2 unrepaired MODA cable faults
     *   注意 attention — exactly 1 fault, or no fault but suspicious vessels present
     *   一般 normal    — no faults and no suspicious vessels
     */
    function updateBriefAndMetrics() {
        const badge = document.getElementById('briefBadge');
        if (!badge || !latestData) return; // not the desktop homepage / no data yet

        const t = (typeof i18n !== 'undefined') ? i18n.t.bind(i18n) : (k) => k;
        const num = (n) => (n || 0).toLocaleString();
        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        };

        const summary = (latestData.suspicious_analysis && latestData.suspicious_analysis.summary) || {};
        const risk = summary.risk_distribution || {};
        const suspCount = summary.suspicious_count || 0;
        const dark = (latestData.dark_vessels && latestData.dark_vessels.overall) || {};
        const sar = (latestData.sar_ais_matching && latestData.sar_ais_matching.summary) || null;
        const faults = cableCounts ? cableCounts.faults : 0;

        // ── Tile 1: cable status (waits for cable_status.json) ──
        if (cableCounts) {
            const cableEl = document.getElementById('metricCable');
            if (cableEl) cableEl.className = 'metric-value ' + (faults > 0 ? 'alert' : 'ok');
            setText('metricCable', t('metric.cable_value', faults));
            setText('metricCableSub', t('metric.cable_sub', cableCounts.repaired));
        }

        // ── Tile 2: suspicious vessels (score ≥8) ──
        setText('metricSuspicious', num(suspCount));
        setText('metricSuspiciousSub', t('metric.suspicious_sub', num(risk.critical), num(risk.high)));

        // ── Tile 3: China gov / research vessels in the current AIS snapshot ──
        const govCounts = {};
        (rawVesselList || []).forEach((v) => {
            const cat = MapModule.getGovType(v);
            if (cat) govCounts[cat] = (govCounts[cat] || 0) + 1;
        });
        const govTotal = Object.values(govCounts).reduce((a, b) => a + b, 0);
        setText('metricGov', num(govTotal));
        setText('metricGovSub', govTotal
            ? (MapModule.GOV_TYPES || Object.keys(govCounts))
                .filter((c) => govCounts[c])
                .map((c) => MapModule.govLabel(c) + ' ' + govCounts[c])
                .join(' · ')
            : t('metric.gov_none'));

        // ── Tile 4: SAR dark vessels ──
        setText('metricDark', num(dark.dark_vessels));
        setText('metricDarkSub', sar
            ? t('metric.dark_sub', num(sar.residual_dark), sar.false_dark_removed_pct)
            : t('metric.dark_sub_alt', num(dark.total_detections), dark.dark_ratio));
        renderDarkFunnel(dark, sar);

        // Panels with nothing to show are not expandable
        setCardEnabled('gov', govTotal > 0);
        setCardEnabled('dark', !!(dark.dark_vessels || sar));

        // ── Situation badge + one-line summary ──
        let level = 'normal';
        if (faults >= 2) level = 'alert';
        else if (faults === 1 || suspCount > 0) level = 'attention';

        badge.className = 'brief-badge level-' + level;
        badge.textContent = t('brief.level_' + level);
        badge.title = t('brief.level_rule');

        setText('briefSummary', t(
            faults > 0 ? 'brief.summary' : 'brief.summary_nofault',
            faults, num(suspCount), num(risk.critical), num(dark.dark_vessels)
        ));

        const fresh = lastUpdatedAt ? formatFreshness(lastUpdatedAt) : null;
        setText('briefUpdated', fresh && fresh.label ? t('brief.updated', fresh.label) : '');
    }

    // 資料新鮮度：data.json 的 updated_at（loadData 時記錄，每分鐘重繪）
    let lastUpdatedAt = null;
    let freshnessTimer = null;
    const STALE_THRESHOLD_HOURS = 4;

    /**
     * Parse a pipeline timestamp. Older payloads carry '…+00:00Z' (offset AND
     * Z — Date() rejects it); the trailing Z is dropped when an explicit
     * offset is present so those files still render a time.
     */
    function parseTs(isoStr) {
        if (!isoStr) return new Date(NaN);
        const s = String(isoStr).replace(/([+-]\d{2}:\d{2})Z$/, '$1');
        return new Date(s);
    }

    /**
     * Format data freshness relative label, e.g. "(12 分鐘前)"
     * Returns { label, isStale }
     */
    function formatFreshness(isoStr) {
        try {
            const diffMin = Math.floor((Date.now() - parseTs(isoStr).getTime()) / 60000);
            if (isNaN(diffMin)) return { label: '', isStale: false };
            const t = (k, v) => typeof i18n !== 'undefined'
                ? i18n.t(k).replace('{0}', v)
                : ('' + v);
            if (diffMin < 1) return { label: t('common.ago_just_now'), isStale: false };
            if (diffMin < 60) return { label: t('common.ago_m', diffMin), isStale: false };
            const hours = Math.floor(diffMin / 60);
            return { label: t('common.ago_h', hours), isStale: hours >= STALE_THRESHOLD_HOURS };
        } catch (_) {
            return { label: '', isStale: false };
        }
    }

    /**
     * Re-render the update-time labels + stale warning from lastUpdatedAt
     */
    function refreshFreshness() {
        if (!lastUpdatedAt) return;
        const updateTime = parseTs(lastUpdatedAt).toLocaleString();
        const fresh = formatFreshness(lastUpdatedAt);
        const updateLabel = (typeof i18n !== 'undefined' ? i18n.t('common.updated') : '更新:')
            + ' ' + updateTime + (fresh.label ? ' (' + fresh.label + ')' : '');
        const updateEl = document.getElementById('updateInfo');
        if (updateEl) updateEl.textContent = updateLabel;
        const bsUpdate = document.getElementById('bsUpdateInfo');
        if (bsUpdate) bsUpdate.textContent = updateLabel;

        const statusEl = document.getElementById('dataStatus');
        if (statusEl) {
            statusEl.classList.toggle('stale', fresh.isStale);
            if (fresh.isStale) {
                const hours = Math.floor((Date.now() - parseTs(lastUpdatedAt).getTime()) / 3600000);
                statusEl.textContent = typeof i18n !== 'undefined'
                    ? i18n.t('common.stale_warning').replace('{0}', hours)
                    : '⚠️ 資料逾 ' + hours + ' 小時未更新';
                statusEl.classList.remove('live');
            }
        }
    }

    // 各資料源新鮮度狀態表（data.json 的 data_sources）
    let dataSources = null;

    /**
     * Classify a source's freshness: ok / late / stale / unknown.
     * late = age > 1.5×interval, stale = age > 3×interval.
     */
    function sourceStatus(updatedAt, intervalHours) {
        if (!updatedAt) return { key: 'unknown', ageH: null };
        const ts = parseTs(updatedAt).getTime();
        if (isNaN(ts)) return { key: 'unknown', ageH: null };
        const ageH = (Date.now() - ts) / 3600000;
        const iv = intervalHours || 24;
        let key = 'ok';
        if (ageH > iv * 3) key = 'stale';
        else if (ageH > iv * 1.5) key = 'late';
        return { key: key, ageH: ageH };
    }

    function intervalLabel(h) {
        const t = (k, v) => typeof i18n !== 'undefined' ? i18n.t(k).replace('{0}', v) : ('' + v);
        if (h % 24 === 0 && h >= 24) return t('freshness.every_d', h / 24);
        return t('freshness.every_h', h);
    }

    /**
     * Render the per-source data-health table into #dataFreshnessBody.
     * Computed client-side so status stays accurate long after build time.
     */
    function renderDataSources() {
        const body = document.getElementById('dataFreshnessBody');
        if (!body) return;
        const panel = body.closest('.data-health');
        if (!dataSources || !dataSources.length) {
            if (panel) panel.style.display = 'none';
            return;
        }
        if (panel) panel.style.display = '';
        const lang = (typeof i18n !== 'undefined' && i18n.getLang) ? i18n.getLang() : 'zh';
        const tt = (k) => typeof i18n !== 'undefined' ? i18n.t(k) : k;
        body.innerHTML = dataSources.map(function (s) {
            const st = sourceStatus(s.updated_at, s.interval_hours);
            const name = (lang === 'en' && s.label_en) ? s.label_en : s.label;
            const fresh = s.updated_at ? formatFreshness(s.updated_at) : { label: tt('common.unknown') };
            const ago = fresh.label || tt('common.unknown');
            return '<tr>'
                + '<td>' + name + '</td>'
                + '<td>' + ago + '</td>'
                + '<td>' + intervalLabel(s.interval_hours) + '</td>'
                + '<td><span class="fresh-badge fresh-' + st.key + '">'
                + tt('freshness.' + st.key) + '</span></td>'
                + '</tr>';
        }).join('');
    }

    /**
     * Load data from JSON
     */
    async function loadData() {
        try {
            // Resolve a build-time cache key so data.json stays CDN/browser
            // cacheable within an update window; only the tiny manifest is
            // always-fresh. Falls back to a timestamp bust if manifest absent.
            let dataUrl = 'data.json?t=' + Date.now();
            try {
                const mres = await fetch('data-manifest.json?t=' + Date.now(), { cache: 'no-store' });
                if (mres.ok) {
                    const mf = await mres.json();
                    if (mf && mf.data_version) dataUrl = 'data.json?v=' + encodeURIComponent(mf.data_version);
                }
            } catch (_) { /* keep timestamp fallback */ }
            const res = await fetch(dataUrl);
            const data = await res.json();

            latestData = data;
            lastUpdatedAt = data.updated_at;
            dataSources = data.data_sources || null;
            refreshFreshness();
            renderDataSources();
            if (!freshnessTimer) {
                freshnessTimer = setInterval(function () {
                    refreshFreshness();
                    renderDataSources();
                    updateBriefAndMetrics();
                }, 60000);
                window.addEventListener('langchange', function () {
                    refreshFreshness();
                    renderDataSources();
                    updateBriefAndMetrics();
                });
            }

            // Plot dark vessels on map
            if (data.dark_vessels && data.dark_vessels.regions) {
                MapModule.displayDarkVessels(data.dark_vessels);
                ChartsModule.updateZoneCounts({}, data.dark_vessels);
            }

            // Load CSIS suspicious vessel analysis
            if (data.suspicious_analysis) {
                suspiciousData = data.suspicious_analysis;
                MapModule.setSuspiciousData(suspiciousData);
                updateSuspiciousList();
                updateBottomSheetSuspicious();

                // Update suspicious vessel count (score ≥8) — the old
                // top_10pct_count was a quota (fleet ÷ 10), not a risk measure
                const suspEl = document.getElementById('suspiciousCount');
                if (suspEl && suspiciousData.summary) {
                    suspEl.textContent = suspiciousData.summary.suspicious_count || 0;
                }
            }

            // Load AIS real-time vessels (zoom-based: clusters when zoomed out, details when zoomed in)
            const hasAis = data.ais_snapshot && data.ais_snapshot.vessels && data.ais_snapshot.vessels.length > 0;
            console.log('[Monitor] AIS check:', hasAis, 'vessels:', data.ais_snapshot?.vessels?.length || 0);
            if (hasAis) {
                rawVesselList = data.ais_snapshot.vessels;
                const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                vessels = result.vessels;
                console.log('[Monitor] AIS rendered:', result.stats);

                // White-circle markers for gov/research vessels (always visible)
                MapModule.displayGovVessels(rawVesselList);

                updateBottomSheetStats(result.stats);
                updateGovVesselList();

                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.ais_sat_loaded') : '✅ AIS + 衛星資料已載入', true);
            } else if (data.vessel_monitoring) {
                ChartsModule.updateOverlayCards(data, false);
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.sat_loaded') : '🛰️ 衛星資料已載入', true);
            } else {
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.no_data') : '⚠️ 尚無資料', false);
            }

            // Display suspicious vessels on map (after AIS vessels to avoid being cleared)
            if (suspiciousData) {
                MapModule.displaySuspiciousVessels(suspiciousData);
            }

            // 今日態勢 + 關鍵指標（海纜那格會在 loadCableStatus 回來後再補上）
            updateBriefAndMetrics();

            // Load cable fault status
            loadCableStatus();

        } catch (e) {
            console.error('Load data.json failed:', e);
            setDataStatus(typeof i18n !== 'undefined' ? i18n.t('common.error_load') : '❌ 資料載入失敗', false);
            const updateEl = document.getElementById('updateInfo');
            if (updateEl) updateEl.textContent = typeof i18n !== 'undefined' ? i18n.t('app.load_fail_msg') : '請確認 data.json 是否存在';
        }
    }

    /**
     * Load and display cable fault status card
     */
    async function loadCableStatus() {
        const section = document.getElementById('cableStatusSection');
        if (!section) return;
        try {
            const res = await fetch('cable_status.json?' + Date.now());
            if (!res.ok) return;
            const data = await res.json();
            const faults = (data.faults || []).filter(f => f.status === 'fault');
            const repaired = (data.faults || []).filter(f => f.status === 'repaired');

            cableCounts = { faults: faults.length, repaired: repaired.length };
            updateBriefAndMetrics();

            section.style.display = 'block';

            const t = (typeof i18n !== 'undefined') ? i18n.t.bind(i18n) : (k, ...a) => k;
            const summaryEl = document.getElementById('cableStatusSummary');
            if (summaryEl) {
                summaryEl.innerHTML =
                    '<div class="cable-stat-row">' +
                    '<div class="cable-stat is-fault">' +
                    '<div class="cable-stat-value">' + faults.length + '</div>' +
                    '<div class="cable-stat-label">' + t('cable.fault') + '</div></div>' +
                    '<div class="cable-stat is-repaired">' +
                    '<div class="cable-stat-value">' + repaired.length + '</div>' +
                    '<div class="cable-stat-label">' + t('cable.repaired') + '</div></div>' +
                    '</div>';
            }

            const listEl = document.getElementById('cableFaultList');
            if (listEl && faults.length > 0) {
                listEl.innerHTML = faults.map(f => {
                    const repairInfo = f.estimated_repair
                        ? '<br><span class="cable-fault-repair">⏱ ' + t('cable.est_repair') + ' ' + f.estimated_repair + '</span>'
                        : '';
                    const altInfo = f.alt_route
                        ? '<br><span class="cable-fault-alt">↔ ' + f.alt_route + '</span>'
                        : '';
                    return '<div class="cable-fault-row">' +
                        '<span class="cable-fault-name">⚠ ' + f.segment + '</span> ' +
                        '<span style="opacity:0.7">' + (f.name_zh || '') + '</span><br>' +
                        '<span class="cable-fault-meta">' + f.fault_date + ' | ' + (f.location_zh || '') + '</span>' +
                        repairInfo + altInfo +
                        '</div>';
                }).join('');
            } else if (listEl) {
                listEl.innerHTML = '<div class="cable-all-normal">' + t('cable.all_normal') + '</div>';
            }
        } catch (e) {
            // Cable status not available, hide section
        }
    }

    /**
     * Set data status indicator
     */
    function setDataStatus(text, isLive) {
        const statusEl = document.getElementById('dataStatus');
        if (statusEl) {
            statusEl.textContent = text;
            statusEl.classList.toggle('live', isLive);
        }
    }

    /**
     * Focus on a vessel
     */
    function focusVessel(mmsi) {
        MapModule.focusVessel(mmsi, vessels);
    }

    /**
     * Focus on a suspicious vessel position
     */
    function focusSuspicious(lat, lon) {
        MapModule.focusPosition(lat, lon, 10);
    }

    /**
     * Show vessel detail info card + focus on map
     */
    function showVesselDetail(mmsi) {
        if (suspiciousData && suspiciousData.suspicious_vessels) {
            const sv = suspiciousData.suspicious_vessels.find(v => v.mmsi === mmsi);
            if (sv && sv.last_lat && sv.last_lon) {
                MapModule.focusPosition(sv.last_lat, sv.last_lon, 10);
            }
        }
        MapModule.showVesselInfoCard(mmsi);
    }

    /**
     * Toggle a layer
     */
    function toggleLayer(name) {
        const checkbox = document.getElementById('show' + name.charAt(0).toUpperCase() + name.slice(1));
        if (checkbox) {
            MapModule.toggleLayer(name, checkbox.checked);
        }
    }

    /**
     * Populate the Today's Overview dashboard
     */
    function updateOverview(data) {
        const ov = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };

        // AIS vessel count
        const aisCount = data.ais_snapshot?.vessels?.length || 0;
        ov('ovAisCount', aisCount.toLocaleString());

        // Dark vessels
        const darkTotal = data.dark_vessels?.overall?.dark_vessels || 0;
        ov('ovDarkVessels', darkTotal.toLocaleString());

        // Suspicious
        const suspCount = data.suspicious_analysis?.summary?.suspicious_count || 0;
        ov('ovSuspicious', suspCount);

        // Identity changes (24h)
        const idEvents = data.identity_events?.events || [];
        const now = Date.now();
        const h24 = idEvents.filter(e => (now - new Date(e.timestamp).getTime()) < 86400000).length;
        ov('ovIdentity', h24);

        // Update time
        const updEl = document.getElementById('overviewUpdated');
        if (updEl && data.updated_at) {
            const ago = Math.round((now - new Date(data.updated_at).getTime()) / 60000);
            const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
            updEl.textContent = ago < 60
                ? (ago + ' min ago')
                : (Math.round(ago / 60) + ' hr ago');
        }

        // Alert bar — show top alerts
        const alertBar = document.getElementById('overviewAlerts');
        if (alertBar) {
            const alerts = [];
            if (darkTotal > 0) {
                alerts.push('SAR 偵測到 ' + darkTotal + ' 艘暗船於台灣周邊海域');
            }
            if (suspCount > 0) {
                alerts.push(suspCount + ' 艘船隻疑似海纜威脅（鄰近海纜+異常移動）');
            }
            if (h24 > 0) {
                alerts.push('過去 24 小時內 ' + h24 + ' 次 AIS 身分變更事件');
            }
            alertBar.innerHTML = alerts.map(a =>
                '<div class="overview-alert-item">' + a + '</div>'
            ).join('');
        }
    }

    /**
     * Share functions
     */
    function shareToTwitter() {
        const text = buildShareText();
        const url = 'https://s0914712.github.io/taiwan-grayzone-monitor/';
        window.open('https://twitter.com/intent/tweet?text=' + encodeURIComponent(text) + '&url=' + encodeURIComponent(url), '_blank');
    }

    function shareToLine() {
        const text = buildShareText();
        const url = 'https://s0914712.github.io/taiwan-grayzone-monitor/';
        window.open('https://social-plugins.line.me/lineit/share?url=' + encodeURIComponent(url) + '&text=' + encodeURIComponent(text), '_blank');
    }

    function copyShareLink() {
        const text = buildShareText() + '\nhttps://s0914712.github.io/taiwan-grayzone-monitor/';
        navigator.clipboard.writeText(text).then(() => {
            const btn = document.querySelector('.share-btn:last-child');
            if (btn) { btn.textContent = '✓'; setTimeout(() => { btn.textContent = '🔗'; }, 1500); }
        });
    }

    function buildShareText() {
        const ais = document.getElementById('ovAisCount')?.textContent || '--';
        const dark = document.getElementById('ovDarkVessels')?.textContent || '--';
        const susp = document.getElementById('ovSuspicious')?.textContent || '--';
        const date = new Date().toLocaleDateString('zh-TW');
        return '🛰️ 台灣灰色地帶與海底電纜監測 ' + date +
               '\nAIS 船隻: ' + ais +
               ' | 暗船: ' + dark +
               ' | 可疑: ' + susp +
               '\n#TaiwanSecurity #GrayZone #OSINT';
    }

    // Public API
    return {
        init,
        toggleLayer,
        focusVessel,
        focusSuspicious,
        showVesselDetail,
        loadData,
        shareToTwitter,
        shareToLine,
        copyShareLink
    };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', App.init);

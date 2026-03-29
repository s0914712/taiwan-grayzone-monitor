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
    let sidebarOpen = false;

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

        // Legend item click -> locate vessel type on map
        document.querySelectorAll('.legend-clickable').forEach(item => {
            item.addEventListener('click', () => {
                const type = item.getAttribute('data-vessel-type');
                if (type) MapModule.locateVesselType(type);
            });
        });

        // FOC commercial vessel filter
        const focCheckbox = document.getElementById('filterFocVessels');
        if (focCheckbox) {
            focCheckbox.addEventListener('change', () => {
                MapModule.setFilterFoc(focCheckbox.checked);
                if (rawVesselList.length > 0) {
                    const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                    vessels = result.vessels;
                    ChartsModule.updateAisStats(result.stats);
                    const vc = document.getElementById('vesselCount');
                    if (vc) vc.textContent = result.stats.total;
                    updateVesselList();
                    updateBottomSheetLng();
                }
            });
        }
    }

    /**
     * Setup mobile navigation - 5-tab bottom nav with popover & bottom sheet
     */
    function setupMobileNavigation() {
        if (document.querySelector('.mobile-bottom-nav')) return;

        const currentPage = window.location.pathname.split('/').pop() || 'index.html';
        const animPages = ['weekly-animation.html', 'ais-animation.html', 'cn-fishing-animation.html', 'identity-history.html'];
        const isAnimPage = animPages.includes(currentPage);

        // --- Bottom Nav (5 tabs) ---
        const bottomNav = document.createElement('nav');
        bottomNav.className = 'mobile-bottom-nav';
        bottomNav.innerHTML = `
            <a href="index.html" ${currentPage === 'index.html' ? 'class="active"' : ''}>
                <span class="nav-icon">🛰️</span>
                <span data-i18n="nav.mob_monitor">監測</span>
            </a>
            <a href="dark-vessels.html" ${currentPage === 'dark-vessels.html' ? 'class="active"' : ''}>
                <span class="nav-icon">🔦</span>
                <span data-i18n="nav.mob_dark">暗船</span>
            </a>
            <a href="statistics.html" ${currentPage === 'statistics.html' ? 'class="active"' : ''}>
                <span class="nav-icon">📊</span>
                <span data-i18n="nav.mob_stats">統計</span>
            </a>
            <button id="navAnimBtn" ${isAnimPage ? 'class="active"' : ''}>
                <span class="nav-icon">🎬</span>
                <span data-i18n="nav.mob_anim">動畫</span>
            </button>
            <button id="navToolsBtn">
                <span class="nav-icon">⚙️</span>
                <span data-i18n="nav.mob_tools">工具</span>
            </button>
        `;
        document.body.appendChild(bottomNav);

        // --- Animation Popover ---
        const popover = document.createElement('div');
        popover.className = 'nav-popover';
        popover.innerHTML = `
            <a href="weekly-animation.html" ${currentPage === 'weekly-animation.html' ? 'class="active"' : ''}>
                <span class="pop-icon">🎬</span>
                <span data-i18n="nav.animation">軌跡動畫</span>
            </a>
            <a href="ais-animation.html" ${currentPage === 'ais-animation.html' ? 'class="active"' : ''}>
                <span class="pop-icon">📡</span>
                <span data-i18n="nav.ais_anim">船位動畫</span>
            </a>
            <a href="cn-fishing-animation.html" ${currentPage === 'cn-fishing-animation.html' ? 'class="active"' : ''}>
                <span class="pop-icon">🐟</span>
                <span data-i18n="nav.cn_fishing">大陸漁船</span>
            </a>
            <a href="identity-history.html" ${currentPage === 'identity-history.html' ? 'class="active"' : ''}>
                <span class="pop-icon">🔄</span>
                <span data-i18n="nav.identity">身分追蹤</span>
            </a>
        `;
        document.body.appendChild(popover);

        // --- Bottom Sheet Overlay ---
        const sheetOverlay = document.createElement('div');
        sheetOverlay.className = 'bottom-sheet-overlay';
        document.body.appendChild(sheetOverlay);

        // --- Bottom Sheet ---
        const sheet = document.createElement('div');
        sheet.className = 'bottom-sheet';
        sheet.id = 'bottomSheet';

        const t = typeof i18n !== 'undefined' ? i18n.t.bind(i18n) : k => k;
        const isIndex = currentPage === 'index.html';

        let sheetHTML = `<div class="bottom-sheet-handle"></div>`;

        // Route search section (only on pages with maps)
        const hasMap = document.getElementById('map');
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

        // Layer controls section (only on pages with maps)
        if (hasMap) {
            sheetHTML += `
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.layers">圖層控制</div>
                <label class="layer-toggle"><input type="checkbox" id="bsShowFishingHotspots" checked> <span data-i18n="idx.layer_fishing">漁撈熱點</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowVessels" checked> <span data-i18n="idx.layer_vessels">AIS 船隻</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowDarkVessels" checked> <span data-i18n="idx.layer_dark">暗船 (SAR)</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowSubmarineCables" checked> <span data-i18n="ais_anim.layer_cables">海底電纜</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsShowVesselRoutes" checked> <span data-i18n="idx.layer_routes">船隻航跡</span></label>
                <label class="layer-toggle"><input type="checkbox" id="bsFilterFocVessels"> <span data-i18n="idx.filter_foc">過濾權宜船</span></label>
            </div>`;
        }

        // Stats + suspicious + LNG section (only on index)
        if (isIndex) {
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
                <div class="bottom-sheet-title">⛽ LNG/Gas 船隻</div>
                <div id="bsLngList" class="bs-lng-list"><span style="color:var(--text-secondary);font-size:13px">載入中...</span></div>
            </div>
            <div class="bottom-sheet-section">
                <div class="bottom-sheet-title" data-i18n="bs.suspicious">可疑船隻</div>
                <div id="bsSuspiciousList"></div>
            </div>`;
        }

        // Update info
        sheetHTML += `
        <div class="bottom-sheet-section">
            <div style="font-size:12px;color:var(--text-secondary)" id="bsUpdateInfo"></div>
        </div>`;

        sheet.innerHTML = sheetHTML;
        document.body.appendChild(sheet);

        // --- Event Handlers ---
        let popoverOpen = false;
        let sheetOpen = false;

        function closeAll() {
            popover.classList.remove('open');
            sheet.classList.remove('open');
            sheetOverlay.classList.remove('active');
            popoverOpen = false;
            sheetOpen = false;
        }

        document.getElementById('navAnimBtn').addEventListener('click', () => {
            if (sheetOpen) { sheet.classList.remove('open'); sheetOpen = false; }
            popoverOpen = !popoverOpen;
            popover.classList.toggle('open', popoverOpen);
            sheetOverlay.classList.toggle('active', popoverOpen);
        });

        document.getElementById('navToolsBtn').addEventListener('click', () => {
            if (popoverOpen) { popover.classList.remove('open'); popoverOpen = false; }
            sheetOpen = !sheetOpen;
            sheet.classList.toggle('open', sheetOpen);
            sheetOverlay.classList.toggle('active', sheetOpen);
        });

        sheetOverlay.addEventListener('click', closeAll);

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
            syncCheckbox('bsShowTerritorialBaseline', 'showTerritorialBaseline', checked => {
                if (checked) MapModule.drawTerritorialBaseline();
                MapModule.toggleLayer('territorialBaseline', checked);
            });
            syncCheckbox('bsFilterFocVessels', 'filterFocVessels', checked => {
                MapModule.setFilterFoc(checked);
                if (rawVesselList.length > 0) {
                    const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                    vessels = result.vessels;
                    ChartsModule.updateAisStats(result.stats);
                    const vc = document.getElementById('vesselCount');
                    if (vc) vc.textContent = result.stats.total;
                    updateVesselList();
                    updateBottomSheetStats(result.stats);
                    updateBottomSheetLng();
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

        // Touch drag to dismiss bottom sheet
        let startY = 0;
        sheet.querySelector('.bottom-sheet-handle').addEventListener('touchstart', e => {
            startY = e.touches[0].clientY;
        }, { passive: true });
        sheet.addEventListener('touchmove', e => {
            if (startY === 0) return;
            const dy = e.touches[0].clientY - startY;
            if (dy > 60) { closeAll(); startY = 0; }
        }, { passive: true });
        sheet.addEventListener('touchend', () => { startY = 0; }, { passive: true });

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
            return `<div class="bs-suspect-item" onclick="App.focusSuspicious(${sv.last_lat}, ${sv.last_lon})">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name.substring(0, 16)}</span>
                <span class="risk-badge risk-${sv.risk_level}">${sv.risk_level}</span>
            </div>`;
        }).join('');
    }

    /**
     * Update bottom sheet LNG vessel list
     */
    function updateBottomSheetLng() {
        const list = document.getElementById('bsLngList');
        if (!list) return;

        const lngVessels = Array.from(vessels.values()).filter(v =>
            v.is_lng || /\b(LNG|LPG|FSRU|GAS)\b/i.test(v.name || '')
        );

        if (lngVessels.length === 0) {
            list.innerHTML = '<span style="color:var(--text-secondary);font-size:13px">目前無 LNG/Gas 船隻</span>';
            return;
        }

        list.innerHTML = lngVessels.slice(0, 5).map((v, i) =>
            '<div class="bs-lng-item" onclick="App.focusVessel(\'' + v.mmsi + '\')">' +
                '<span class="bs-lng-num">' + (i + 1) + '</span>' +
                '<span class="bs-lng-name">' + (v.name || 'Unknown').substring(0, 18) + '</span>' +
                '<span class="bs-lng-speed">' + (v.speed || 0).toFixed(1) + ' kn</span>' +
            '</div>'
        ).join('');
    }

    /**
     * Toggle sidebar visibility (mobile)
     */
    function toggleSidebar() {
        sidebarOpen = !sidebarOpen;
        const sidebar = document.querySelector('.sidebar');
        const overlay = document.querySelector('.sidebar-overlay');

        if (sidebar) {
            sidebar.classList.toggle('open', sidebarOpen);
        }
        if (overlay) {
            overlay.classList.toggle('active', sidebarOpen);
        }
    }

    /**
     * Update vessel list in sidebar
     */
    function updateVesselList() {
        const list = document.getElementById('vesselList');
        if (!list) return;

        const recent = Array.from(vessels.values()).slice(0, 12);

        list.innerHTML = recent.map(v => `
            <div class="vessel-item" onclick="App.focusVessel('${v.mmsi}')">
                <span style="color:${MapModule.VESSEL_COLORS[v.type_name] || MapModule.VESSEL_COLORS.other}">${(v.name || 'Unknown').substring(0, 14)}</span>
                <span style="font-size:11px;color:var(--text-secondary)">${v.type_name || '未知'}</span>
            </div>
        `).join('');
    }

    /**
     * Update suspicious vessels list in sidebar
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
                list.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);padding:8px">${msg}</div>`;
            }
            return;
        }

        list.innerHTML = suspiciousData.suspicious_vessels.slice(0, 10).map(sv => {
            const name = (sv.names && sv.names[0]) || sv.mmsi;
            const riskClass = 'risk-' + sv.risk_level;

            return `
                <div class="suspicious-item" onclick="App.focusSuspicious(${sv.last_lat}, ${sv.last_lon})">
                    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                        ${name.substring(0, 12)}
                    </span>
                    <span class="risk-badge ${riskClass}">${sv.risk_level}</span>
                </div>`;
        }).join('');
    }

    /**
     * Format time-ago string
     */
    function timeAgo(isoStr) {
        try {
            const diff = Date.now() - new Date(isoStr).getTime();
            const hours = Math.floor(diff / 3600000);
            if (hours < 1) return typeof i18n !== 'undefined' ? i18n.t('idx.identity_just_now') : '剛才';
            if (hours < 24) {
                const tpl = typeof i18n !== 'undefined' ? i18n.t('idx.identity_ago_h') : '{0}小時前';
                return tpl.replace('{0}', hours);
            }
            const days = Math.floor(hours / 24);
            const tpl = typeof i18n !== 'undefined' ? i18n.t('idx.identity_ago_d') : '{0}天前';
            return tpl.replace('{0}', days);
        } catch (_) {
            return '--';
        }
    }

    /**
     * Update identity change alerts section
     */
    function updateIdentitySection(data) {
        const section = document.getElementById('identitySection');
        const list = document.getElementById('identityList');
        if (!section || !list) return;

        const idData = data.identity_events;
        if (!idData || !idData.summary) return;

        const summary = idData.summary;
        if (summary.count_7d === 0 && summary.count_24h === 0) return;

        // Show section
        section.style.display = '';

        // Update counters
        const el24h = document.getElementById('identity24h');
        const el7d = document.getElementById('identity7d');
        const elVessels = document.getElementById('identityVessels7d');
        if (el24h) el24h.textContent = summary.count_24h || 0;
        if (el7d) el7d.textContent = summary.count_7d || 0;
        if (elVessels) elVessels.textContent = summary.vessels_7d || 0;

        // Use 24h events if available, otherwise 7d
        const events = (idData.events_24h && idData.events_24h.length > 0)
            ? idData.events_24h
            : (idData.events_7d || []);

        if (events.length === 0) return;

        list.innerHTML = events.slice(0, 10).map(ev => {
            const changes = ev.changes || [];
            const desc = changes.map(c => {
                const oldShort = (c.old || '').substring(0, 10);
                const newShort = (c.new || '').substring(0, 10);
                return `${oldShort} → ${newShort}`;
            }).join(', ');

            const multiBadge = ev.multi_field
                ? `<span class="risk-badge risk-high" style="font-size:9px;margin-left:4px">${typeof i18n !== 'undefined' ? i18n.t('idx.identity_multi') : '多欄位'}</span>`
                : '';

            const hasCoords = ev.lat != null && ev.lon != null;
            const onclick = hasCoords ? `onclick="App.focusSuspicious(${ev.lat}, ${ev.lon})"` : '';

            return `
                <div class="vessel-item" ${onclick} style="cursor:${hasCoords ? 'pointer' : 'default'}">
                    <div style="flex:1;overflow:hidden">
                        <div style="font-size:12px;color:var(--accent-cyan);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                            MMSI ${ev.mmsi}${multiBadge}
                        </div>
                        <div style="font-size:11px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                            ${desc}
                        </div>
                    </div>
                    <span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;margin-left:4px">${timeAgo(ev.timestamp)}</span>
                </div>`;
        }).join('');

        if (typeof i18n !== 'undefined') i18n.applyAll();
    }

    /**
     * Load data from JSON
     */
    async function loadData() {
        try {
            const res = await fetch('data.json?' + Date.now());
            const data = await res.json();

            const updateTime = new Date(data.updated_at).toLocaleString();
            const updateLabel = (typeof i18n !== 'undefined' ? i18n.t('common.updated') : '更新:') + ' ' + updateTime;
            const updateEl = document.getElementById('updateInfo');
            if (updateEl) updateEl.textContent = updateLabel;
            const bsUpdate = document.getElementById('bsUpdateInfo');
            if (bsUpdate) bsUpdate.textContent = updateLabel;

            // Load GFW satellite monitoring data
            if (data.vessel_monitoring) {
                ChartsModule.displayGfwStats(data.vessel_monitoring, {
                    section: 'gfwSection',
                    darkVessels: 'gfwDarkVessels',
                    trend: 'gfwTrend',
                    chnHours: 'gfwChnHours',
                    fishingHours: 'gfwFishingHours',
                    dataDays: 'gfwDataDays',
                    sparkline: 'gfwSparkline',
                    alerts: 'gfwAlerts'
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
                updateSuspiciousList();
                updateBottomSheetSuspicious();

                // Update suspicious count
                const suspEl = document.getElementById('suspiciousCount');
                if (suspEl && suspiciousData.summary) {
                    suspEl.textContent = suspiciousData.summary.suspicious_count || 0;
                }
            }

            // Load identity change alerts
            updateIdentitySection(data);

            // Load AIS real-time vessels (zoom-based: clusters when zoomed out, details when zoomed in)
            const hasAis = data.ais_snapshot && data.ais_snapshot.vessels && data.ais_snapshot.vessels.length > 0;
            console.log('[Monitor] AIS check:', hasAis, 'vessels:', data.ais_snapshot?.vessels?.length || 0);
            if (hasAis) {
                rawVesselList = data.ais_snapshot.vessels;
                const result = MapModule.renderVesselsForZoom(rawVesselList, vessels);
                vessels = result.vessels;
                console.log('[Monitor] AIS rendered:', result.stats);

                ChartsModule.updateAisStats(result.stats);

                // Update overlay cards
                const vesselCountEl = document.getElementById('vesselCount');
                if (vesselCountEl) vesselCountEl.textContent = result.stats.total;

                updateVesselList();
                updateBottomSheetStats(result.stats);
                updateBottomSheetLng();

                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.ais_sat_loaded') : '✅ AIS + 衛星資料已載入', true);
            } else if (data.vessel_monitoring) {
                const aisSection = document.getElementById('aisStatsSection');
                if (aisSection) aisSection.style.display = 'none';

                ChartsModule.updateOverlayCards(data, false);
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.sat_loaded') : '🛰️ 衛星資料已載入', true);
            } else {
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.no_data') : '⚠️ 尚無資料', false);
            }

            // Display suspicious vessels on map (after AIS vessels to avoid being cleared)
            if (suspiciousData) {
                MapModule.displaySuspiciousVessels(suspiciousData);
            }

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

            section.style.display = 'block';

            const t = (typeof i18n !== 'undefined') ? i18n.t.bind(i18n) : (k, ...a) => k;
            const summaryEl = document.getElementById('cableStatusSummary');
            if (summaryEl) {
                summaryEl.innerHTML =
                    '<div style="display:flex;gap:12px;margin-bottom:8px">' +
                    '<div style="text-align:center;flex:1;padding:6px;background:rgba(255,0,0,0.15);border-radius:6px">' +
                    '<div style="font-size:20px;font-weight:700;color:#ff0000">' + faults.length + '</div>' +
                    '<div style="font-size:11px;opacity:0.7" data-i18n="cable.fault">' + t('cable.fault') + '</div></div>' +
                    '<div style="text-align:center;flex:1;padding:6px;background:rgba(0,255,136,0.1);border-radius:6px">' +
                    '<div style="font-size:20px;font-weight:700;color:#00ff88">' + repaired.length + '</div>' +
                    '<div style="font-size:11px;opacity:0.7" data-i18n="cable.repaired">' + t('cable.repaired') + '</div></div>' +
                    '</div>';
            }

            const listEl = document.getElementById('cableFaultList');
            if (listEl && faults.length > 0) {
                listEl.innerHTML = faults.map(f =>
                    '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05)">' +
                    '<span style="color:#ff0000;font-weight:600">⚠ ' + f.segment + '</span> ' +
                    '<span style="opacity:0.7">' + (f.name_zh || '') + '</span><br>' +
                    '<span style="font-size:11px;opacity:0.5">' + f.fault_date + ' | ' + (f.location_zh || '') + '</span>' +
                    '</div>'
                ).join('');
            } else if (listEl) {
                listEl.innerHTML = '<div style="color:#00ff88;padding:8px 0" data-i18n="cable.all_normal">所有海纜正常運作</div>';
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
        return '🛰️ 台灣灰色地帶監測 ' + date +
               '\nAIS 船隻: ' + ais +
               ' | 暗船: ' + dark +
               ' | 可疑: ' + susp +
               '\n#TaiwanSecurity #GrayZone #OSINT';
    }

    // Public API
    return {
        init,
        toggleSidebar,
        toggleLayer,
        focusVessel,
        focusSuspicious,
        loadData,
        shareToTwitter,
        shareToLine,
        copyShareLink
    };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', App.init);

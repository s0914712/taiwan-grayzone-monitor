/**
 * Taiwan Gray Zone Monitor - Main Application
 * Coordinates map, charts, and UI components
 */

const App = (function () {
    'use strict';

    // State
    let vessels = new Map();
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

        // Load data
        loadData();
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners() {
        // Layer toggle checkboxes
        ['fishingHotspots', 'vessels'].forEach(layer => {
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
    }

    /**
     * Setup mobile navigation
     */
    function setupMobileNavigation() {
        // Create mobile menu button if it doesn't exist
        const header = document.querySelector('.header-info');
        if (header && !document.querySelector('.mobile-menu-btn')) {
            const menuBtn = document.createElement('button');
            menuBtn.className = 'mobile-menu-btn';
            menuBtn.innerHTML = '☰';
            menuBtn.onclick = toggleSidebar;
            header.insertBefore(menuBtn, header.firstChild);
        }

        // Create sidebar overlay
        if (!document.querySelector('.sidebar-overlay')) {
            const overlay = document.createElement('div');
            overlay.className = 'sidebar-overlay';
            overlay.onclick = toggleSidebar;
            document.body.appendChild(overlay);
        }

        // Create mobile bottom nav if it doesn't exist
        if (!document.querySelector('.mobile-bottom-nav')) {
            const bottomNav = document.createElement('nav');
            bottomNav.className = 'mobile-bottom-nav';
            bottomNav.innerHTML = `
                <a href="index.html">
                    <span class="nav-icon">🛰️</span>
                    <span data-i18n="nav.mob_monitor">監測</span>
                </a>
                <a href="dark-vessels.html">
                    <span class="nav-icon">🔦</span>
                    <span data-i18n="nav.mob_dark">暗船</span>
                </a>
                <a href="statistics.html">
                    <span class="nav-icon">📊</span>
                    <span data-i18n="nav.mob_stats">統計</span>
                </a>
                <a href="weekly-animation.html">
                    <span class="nav-icon">🎬</span>
                    <span data-i18n="nav.mob_anim">動畫</span>
                </a>
                <a href="identity-history.html">
                    <span class="nav-icon">🔄</span>
                    <span data-i18n="nav.mob_identity">身分</span>
                </a>
            `;
            document.body.appendChild(bottomNav);

            // Detect current page and set active state
            const currentPage = window.location.pathname.split('/').pop() || 'index.html';
            bottomNav.querySelectorAll('a').forEach(a => {
                const href = a.getAttribute('href');
                if (href === currentPage) a.classList.add('active');
            });

            if (typeof i18n !== 'undefined') i18n.applyAll();
        }
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
                <span style="font-size:7px;color:var(--text-secondary)">${v.type_name || '未知'}</span>
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
                list.innerHTML = `<div style="font-size:8px;color:var(--text-secondary);padding:4px">${msg}</div>`;
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

        // Also display on map
        MapModule.displaySuspiciousVessels(suspiciousData);
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
                ? `<span class="risk-badge risk-high" style="font-size:7px;margin-left:3px">${typeof i18n !== 'undefined' ? i18n.t('idx.identity_multi') : '多欄位'}</span>`
                : '';

            const hasCoords = ev.lat != null && ev.lon != null;
            const onclick = hasCoords ? `onclick="App.focusSuspicious(${ev.lat}, ${ev.lon})"` : '';

            return `
                <div class="vessel-item" ${onclick} style="cursor:${hasCoords ? 'pointer' : 'default'}">
                    <div style="flex:1;overflow:hidden">
                        <div style="font-size:8px;color:var(--accent-cyan);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                            MMSI ${ev.mmsi}${multiBadge}
                        </div>
                        <div style="font-size:7px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                            ${desc}
                        </div>
                    </div>
                    <span style="font-size:7px;color:var(--text-secondary);white-space:nowrap;margin-left:4px">${timeAgo(ev.timestamp)}</span>
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
            const updateEl = document.getElementById('updateInfo');
            if (updateEl) updateEl.textContent = (typeof i18n !== 'undefined' ? i18n.t('common.updated') : '更新:') + ' ' + updateTime;

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

                // Update suspicious count
                const suspEl = document.getElementById('suspiciousCount');
                if (suspEl && suspiciousData.summary) {
                    suspEl.textContent = suspiciousData.summary.suspicious_count || 0;
                }
            }

            // Load identity change alerts
            updateIdentitySection(data);

            // Load AIS real-time vessels
            const hasAis = data.ais_snapshot && data.ais_snapshot.vessels && data.ais_snapshot.vessels.length > 0;
            if (hasAis) {
                const result = MapModule.displayVessels(data.ais_snapshot.vessels, vessels);
                vessels = result.vessels;

                ChartsModule.updateAisStats(result.stats);

                // Update overlay cards
                document.getElementById('vesselCount').textContent = result.stats.total;

                updateVesselList();

                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.ais_sat_loaded') : '✅ AIS + 衛星資料已載入', true);
            } else if (data.vessel_monitoring) {
                const aisSection = document.getElementById('aisStatsSection');
                if (aisSection) aisSection.style.display = 'none';

                ChartsModule.updateOverlayCards(data, false);
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.sat_loaded') : '🛰️ 衛星資料已載入', true);
            } else {
                setDataStatus(typeof i18n !== 'undefined' ? i18n.t('app.no_data') : '⚠️ 尚無資料', false);
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

    // Public API
    return {
        init,
        toggleSidebar,
        toggleLayer,
        focusVessel,
        focusSuspicious,
        loadData
    };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', App.init);

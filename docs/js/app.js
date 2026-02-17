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
        MapModule.drawDrillZones();
        MapModule.drawFishingHotspots();

        // Initialize UI
        updateZoneList();
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
        ['drillZones', 'fishingHotspots', 'vessels'].forEach(layer => {
            const checkbox = document.getElementById('show' + layer.charAt(0).toUpperCase() + layer.slice(1));
            if (checkbox) {
                checkbox.addEventListener('change', () => {
                    MapModule.toggleLayer(layer, checkbox.checked);
                });
            }
        });
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
                <a href="index.html" class="active">
                    <span class="nav-icon">🛰️</span>
                    <span>監測</span>
                </a>
                <a href="dark-vessels.html">
                    <span class="nav-icon">🔦</span>
                    <span>暗船</span>
                </a>
                <a href="statistics.html">
                    <span class="nav-icon">📊</span>
                    <span>統計</span>
                </a>
            `;
            document.body.appendChild(bottomNav);
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
     * Update zone list in sidebar
     */
    function updateZoneList() {
        const list = document.getElementById('zoneList');
        if (!list) return;

        list.innerHTML = Object.entries(MapModule.DRILL_ZONES).map(([key, zone]) => `
            <div class="zone-item" onclick="App.focusZone('${key}')" style="border-left: 3px solid ${zone.color}">
                <span>${zone.name}</span>
                <span class="zone-count" id="zone-${key}" style="color:${zone.color}">0</span>
            </div>
        `).join('');
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
                list.innerHTML = `<div style="font-size:8px;color:var(--text-secondary);padding:4px">
                    已分析 ${summary.total_analyzed} 艘，暫無達到門檻的可疑船隻</div>`;
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
     * Load data from JSON
     */
    async function loadData() {
        try {
            const res = await fetch('data.json?' + Date.now());
            const data = await res.json();

            const updateTime = new Date(data.updated_at).toLocaleString('zh-TW');
            const updateEl = document.getElementById('updateInfo');
            if (updateEl) updateEl.textContent = '更新: ' + updateTime;

            // Load GFW satellite monitoring data
            if (data.vessel_monitoring) {
                ChartsModule.displayGfwStats(data.vessel_monitoring, {
                    section: 'gfwSection',
                    darkVessels: 'gfwDarkVessels',
                    trend: 'gfwTrend',
                    chnHours: 'gfwChnHours',
                    drillRecords: 'gfwDrillRecords',
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

            // Load AIS real-time vessels
            const hasAis = data.ais_snapshot && data.ais_snapshot.vessels && data.ais_snapshot.vessels.length > 0;
            if (hasAis) {
                const result = MapModule.displayVessels(data.ais_snapshot.vessels, vessels);
                vessels = result.vessels;

                ChartsModule.updateAisStats(result.stats);
                ChartsModule.updateZoneCounts(result.zoneCounts);

                // Update overlay cards
                document.getElementById('vesselCount').textContent = result.stats.total;
                document.getElementById('drillZoneCount').textContent = result.stats.inZone;

                updateVesselList();

                setDataStatus('✅ AIS + 衛星資料已載入', true);
            } else if (data.vessel_monitoring) {
                // Hide empty AIS stats section when no AIS data
                const aisSection = document.getElementById('aisStatsSection');
                if (aisSection) aisSection.style.display = 'none';

                ChartsModule.updateOverlayCards(data, false);
                setDataStatus('🛰️ 衛星資料已載入', true);
            } else {
                setDataStatus('⚠️ 尚無資料', false);
            }

        } catch (e) {
            console.error('載入 data.json 失敗:', e);
            setDataStatus('❌ 資料載入失敗', false);
            const updateEl = document.getElementById('updateInfo');
            if (updateEl) updateEl.textContent = '請確認 data.json 是否存在';
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
     * Focus on a zone
     */
    function focusZone(key) {
        MapModule.focusZone(key);
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
        focusZone,
        focusVessel,
        focusSuspicious,
        loadData
    };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', App.init);

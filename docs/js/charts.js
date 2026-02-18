/**
 * Taiwan Gray Zone Monitor - Charts Module
 * Handles Chart.js visualizations and statistics display
 */

const ChartsModule = (function () {
    'use strict';

    let charts = {};

    /**
     * Format large numbers compactly
     */
    function formatCompact(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 10000) return (num / 10000).toFixed(1) + '萬';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return String(num);
    }

    /**
     * Render sparkline bar chart
     */
    function renderSparkline(containerId, dailyData) {
        const container = document.getElementById(containerId);
        if (!container || !dailyData || dailyData.length === 0) return;

        const maxVal = Math.max(...dailyData.map(d => d.total_detections));

        container.innerHTML = dailyData.map(d => {
            const h = maxVal > 0 ? Math.max(2, (d.total_detections / maxVal) * 30) : 2;
            const darkRatio = d.dark_vessels / Math.max(1, d.total_detections);
            const color = darkRatio > 0.8 ? 'var(--accent-red)' :
                darkRatio > 0.5 ? 'var(--accent-orange)' : 'var(--accent-cyan)';
            return `<div class="sparkline-bar" style="height:${h}px;background:${color}" title="${d.date}: ${d.total_detections} 偵測, ${d.dark_vessels} 暗船"></div>`;
        }).join('');
    }

    /**
     * Render daily dark vessel bar chart
     */
    function renderDailyChart(canvasId, darkByDate) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        const dates = Object.keys(darkByDate).sort();
        const counts = dates.map(d => darkByDate[d]);

        // Destroy existing chart if any
        if (charts[canvasId]) {
            charts[canvasId].destroy();
        }

        charts[canvasId] = new Chart(ctx.getContext('2d'), {
            type: 'bar',
            data: {
                labels: dates.map(d => d.slice(5)), // MM-DD format
                datasets: [{
                    label: '暗船數量',
                    data: counts,
                    backgroundColor: 'rgba(255, 51, 102, 0.6)',
                    borderColor: '#ff3366',
                    borderWidth: 1,
                    borderRadius: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: { color: '#8aa4c8', font: { size: 10 } }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#8aa4c8', font: { size: 8 }, maxRotation: 45 },
                        grid: { color: 'rgba(0,245,255,0.05)' }
                    },
                    y: {
                        ticks: { color: '#8aa4c8', font: { size: 9 } },
                        grid: { color: 'rgba(0,245,255,0.08)' },
                        beginAtZero: true
                    }
                }
            }
        });

        return charts[canvasId];
    }

    /**
     * Render trend line chart
     */
    function renderTrendChart(canvasId, data, options = {}) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        if (charts[canvasId]) {
            charts[canvasId].destroy();
        }

        const defaultOptions = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#8aa4c8', font: { size: 10 } }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#8aa4c8', font: { size: 8 } },
                    grid: { color: 'rgba(0,245,255,0.05)' }
                },
                y: {
                    ticks: { color: '#8aa4c8', font: { size: 9 } },
                    grid: { color: 'rgba(0,245,255,0.08)' },
                    beginAtZero: true
                }
            }
        };

        charts[canvasId] = new Chart(ctx.getContext('2d'), {
            type: 'line',
            data: data,
            options: { ...defaultOptions, ...options }
        });

        return charts[canvasId];
    }

    /**
     * Render pie/doughnut chart
     */
    function renderPieChart(canvasId, labels, values, colors) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        if (charts[canvasId]) {
            charts[canvasId].destroy();
        }

        charts[canvasId] = new Chart(ctx.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderColor: '#0a0f1c',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            color: '#8aa4c8',
                            font: { size: 10 },
                            padding: 10
                        }
                    }
                }
            }
        });

        return charts[canvasId];
    }

    /**
     * Update GFW statistics display
     */
    function displayGfwStats(vm, elementIds) {
        const section = document.getElementById(elementIds.section || 'gfwSection');
        if (section) section.style.display = '';

        const s = vm.summary;

        // Dark vessels
        const darkEl = document.getElementById(elementIds.darkVessels || 'gfwDarkVessels');
        if (darkEl) darkEl.textContent = Math.round(s.avg_daily_dark_vessels);

        // Trend
        const trendPct = s.trend_pct || 0;
        const trendEl = document.getElementById(elementIds.trend || 'gfwTrend');
        if (trendEl) {
            if (trendPct > 0) {
                trendEl.textContent = '+' + trendPct.toFixed(1) + '%';
                trendEl.className = 'value trend-up';
            } else if (trendPct < 0) {
                trendEl.textContent = trendPct.toFixed(1) + '%';
                trendEl.className = 'value trend-down';
            } else {
                trendEl.textContent = '0%';
                trendEl.className = 'value trend-flat';
            }
        }

        // CHN hours
        const chnEl = document.getElementById(elementIds.chnHours || 'gfwChnHours');
        if (chnEl) chnEl.textContent = (s.chn_presence_hours / 10000).toFixed(1);

        // Drill zone records
        const drillEl = document.getElementById(elementIds.drillRecords || 'gfwDrillRecords');
        if (drillEl) drillEl.textContent = formatCompact(s.chn_drill_zone_records);

        // Fishing hours
        const fishEl = document.getElementById(elementIds.fishingHours || 'gfwFishingHours');
        if (fishEl) fishEl.textContent = (s.total_fishing_hours / 10000).toFixed(1);

        // Data days
        const daysEl = document.getElementById(elementIds.dataDays || 'gfwDataDays');
        if (daysEl) daysEl.textContent = s.total_days;

        // Sparkline
        if (vm.daily && vm.daily.length > 0) {
            renderSparkline(elementIds.sparkline || 'gfwSparkline', vm.daily);
        }

        // Alerts
        if (vm.alerts && vm.alerts.length > 0) {
            const alertsEl = document.getElementById(elementIds.alerts || 'gfwAlerts');
            if (alertsEl) {
                alertsEl.innerHTML = vm.alerts.map(a => `
                    <div class="alert-item">⚠️ ${a.message}</div>
                `).join('');
            }
        }
    }

    /**
     * Update AIS statistics display
     */
    function updateAisStats(stats, elementIds = {}) {
        const setEl = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };

        setEl(elementIds.total || 'statTotal', stats.total);
        setEl(elementIds.fishing || 'statFishing', stats.fishing);
        setEl(elementIds.cargo || 'statCargo', stats.cargo);
        setEl(elementIds.tanker || 'statTanker', stats.tanker);
    }

    /**
     * Update overlay cards
     */
    function updateOverlayCards(data, hasAis) {
        if (hasAis) return;

        const vm = data.vessel_monitoring;
        const dv = data.dark_vessels;
        if (!vm) return;

        const s = vm.summary;

        // Vessel count card
        const vesselCountEl = document.getElementById('vesselCount');
        if (vesselCountEl) {
            vesselCountEl.textContent = formatCompact(s.avg_daily_detections * s.total_days);
            const label = vesselCountEl.parentElement.querySelector('.label');
            if (label) label.textContent = 'SAR 總偵測';
        }

        // Drill zone card
        const drillEl = document.getElementById('drillZoneCount');
        if (drillEl) {
            drillEl.textContent = formatCompact(s.chn_drill_zone_records);
            const label = drillEl.parentElement.querySelector('.label');
            if (label) label.textContent = '軍演區記錄';
        }

        // Dark vessel card
        if (dv && dv.overall) {
            const suspEl = document.getElementById('suspiciousCount');
            if (suspEl) {
                suspEl.textContent = formatCompact(dv.overall.dark_vessels);
                const label = suspEl.parentElement.querySelector('.label');
                if (label) label.textContent = '暗船總數';
            }
        }
    }

    /**
     * Update zone counts in sidebar
     */
    function updateZoneCounts(zoneCounts, darkData = null) {
        if (darkData && darkData.regions) {
            const zoneMapping = {
                north: 'east_china_sea',
                east: 'east_taiwan',
                south: 'south_china_sea',
                west: 'taiwan_strait'
            };

            Object.entries(zoneMapping).forEach(([zoneKey, regionKey]) => {
                const region = darkData.regions[regionKey];
                if (region) {
                    const el = document.getElementById('zone-' + zoneKey);
                    if (el) el.textContent = formatCompact(region.dark_vessels);
                }
            });
        } else {
            Object.keys(zoneCounts).forEach(key => {
                const el = document.getElementById('zone-' + key);
                if (el) el.textContent = zoneCounts[key];
            });
        }
    }

    /**
     * Destroy a specific chart
     */
    function destroyChart(canvasId) {
        if (charts[canvasId]) {
            charts[canvasId].destroy();
            delete charts[canvasId];
        }
    }

    /**
     * Destroy all charts
     */
    function destroyAllCharts() {
        Object.keys(charts).forEach(key => {
            charts[key].destroy();
        });
        charts = {};
    }

    // Public API
    return {
        formatCompact,
        renderSparkline,
        renderDailyChart,
        renderTrendChart,
        renderPieChart,
        displayGfwStats,
        updateAisStats,
        updateOverlayCards,
        updateZoneCounts,
        destroyChart,
        destroyAllCharts
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ChartsModule;
}

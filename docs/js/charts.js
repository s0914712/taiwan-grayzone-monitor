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
        if (num >= 10000) return (num / 10000).toFixed(1) + (typeof i18n !== 'undefined' ? i18n.t('chart.unit_wan') : '萬');
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return String(num);
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
                    label: typeof i18n !== 'undefined' ? i18n.t('dark.count') : '暗船數量',
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
            if (label) label.textContent = typeof i18n !== 'undefined' ? i18n.t('dark.total_detect_s') : 'SAR 總偵測';
        }

        // Dark vessel card — only when the count still lives in a map overlay
        // card (index now shows it inline in the sidebar section title, where
        // repurposing it as a dark-vessel total would mislead)
        if (dv && dv.overall) {
            const suspEl = document.getElementById('suspiciousCount');
            if (suspEl && suspEl.closest('.overlay-card')) {
                suspEl.textContent = formatCompact(dv.overall.dark_vessels);
                const label = suspEl.parentElement.querySelector('.label');
                if (label) label.textContent = typeof i18n !== 'undefined' ? i18n.t('dark.dark_total') : '暗船總數';
            }
        }
    }

    /**
     * Update zone counts in sidebar
     */
    function updateZoneCounts(zoneCounts, darkData = null) {
        if (darkData && darkData.regions) {
            // New layout: single `taiwan_region` region with a `sub_zones` dict
            // containing pre-bucketed north/east/south/west counts.
            const taiwanRegion = darkData.regions.taiwan_region;
            if (taiwanRegion && taiwanRegion.sub_zones) {
                ['north', 'east', 'south', 'west'].forEach(zoneKey => {
                    const sub = taiwanRegion.sub_zones[zoneKey];
                    const el = document.getElementById('zone-' + zoneKey);
                    if (el && sub) el.textContent = formatCompact(sub.dark_vessels || 0);
                });
                return;
            }

            // Legacy layout: four separate sub-region entries.
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
        renderDailyChart,
        renderTrendChart,
        renderPieChart,
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

/**
 * Taiwan Gray Zone Monitor — 徘徊熱區圖層（weekly-report.html 與 statistics.html 共用）
 *
 * 色階與分位切檔法是「同一份資料在哪都要同一個顏色」的單一來源：
 *  - 前端兩個頁面都用這裡的 HEAT_COLORS / quantileScale
 *  - Python 端 `src/report_charts.py` 是同一組值（LINE 推送的 PNG），
 *    由 tests/test_report_charts.py 的 test_heat_palette_matches_frontend 守住
 *
 * **分位數而非等距**：離島與本島的滯留時數量級差極大（實測單格 258h vs
 * 中位數個位數），等距分箱會把絕大多數格子擠進同一色。
 *
 * 須在使用它的頁面 script 之前載入。
 */
var HotspotLayer = (function () {
    'use strict';

    var HEAT_COLORS = ['#3d3520', '#7a5c1e', '#b3701f', '#e05a33', '#ff2d55'];
    var CELL_DEG = 0.1;          // 與 grid_utils.grid_cell 相同
    var FILL_OPACITY = 0.45;

    /** 分位切檔：回傳 value → 色階 index 的函式。 */
    function quantileScale(values, buckets) {
        var sorted = values.slice().sort(function (a, b) { return a - b; });
        if (!sorted.length) return function () { return 0; };
        var cuts = [];
        for (var i = 1; i < buckets; i++) {
            cuts.push(sorted[Math.min(sorted.length - 1,
                Math.floor(sorted.length * i / buckets))]);
        }
        return function (v) {
            for (var j = 0; j < cuts.length; j++) if (v <= cuts[j]) return j;
            return buckets - 1;
        };
    }

    /** 熱區格的矩形範圍（格中心 ± 半格）。 */
    function cellBounds(cell, cellDeg) {
        var half = (cellDeg || CELL_DEG) / 2;
        return [[cell.lat - half, cell.lon - half],
                [cell.lat + half, cell.lon + half]];
    }

    /** 均速可能是 null（該格沒有可用 SOG）—— 顯示破折號，絕不顯示 0。 */
    function speedLabel(v) {
        return (v === null || v === undefined) ? '—' : v + ' kn';
    }

    /**
     * 把熱區格畫進 Leaflet layerGroup。
     * opts.popup(cell) 可自訂 popup 內容；未提供則不綁 popup。
     * 回傳畫出的格數。
     */
    function plotCells(L, layerGroup, cells, opts) {
        opts = opts || {};
        layerGroup.clearLayers();
        var list = cells || [];
        var scale = quantileScale(list.map(function (c) {
            return c.loiter_hours || 0;
        }), HEAT_COLORS.length);
        var drawn = 0;
        list.forEach(function (c) {
            if (c.lat === null || c.lat === undefined ||
                c.lon === null || c.lon === undefined) return;
            var color = HEAT_COLORS[scale(c.loiter_hours || 0)];
            var rect = L.rectangle(cellBounds(c, opts.cellDeg), {
                color: color, weight: 1, opacity: 0.9,
                fillColor: color, fillOpacity: opts.fillOpacity || FILL_OPACITY
            });
            if (opts.popup) rect.bindPopup(opts.popup(c));
            rect.addTo(layerGroup);
            drawn++;
        });
        return drawn;
    }

    return {
        HEAT_COLORS: HEAT_COLORS,
        CELL_DEG: CELL_DEG,
        FILL_OPACITY: FILL_OPACITY,
        quantileScale: quantileScale,
        cellBounds: cellBounds,
        speedLabel: speedLabel,
        plotCells: plotCells
    };
})();

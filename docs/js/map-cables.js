/**
 * Taiwan Gray Zone Monitor - Submarine Cable Module
 * Submarine cable GeoJSON layer + MODA fault status overlay.
 * Factory invoked by map.js init() once the Leaflet map + layer groups exist.
 * Load order (HTML): map-data.js → map-baseline.js → map-vessels.js →
 * map-routes.js → map-cables.js → map.js
 */

var MapCablesFactory = function(map, layers) {
    'use strict';

    /**
     * Load and display submarine cable layer
     */
    // Cable fault status cache
    let cableFaults = null; // { faultedSlugs: Set, faultsBySlug: Map }

    async function loadCableFaultStatus() {
        if (cableFaults) return cableFaults;
        try {
            const res = await fetch('cable_status.json?' + Date.now());
            if (!res.ok) return null;
            const data = await res.json();
            const faultedSlugs = new Set();
            const faultsBySlug = {};
            (data.faults || []).forEach(f => {
                if (f.status === 'fault') {
                    faultedSlugs.add(f.slug);
                    if (!faultsBySlug[f.slug]) faultsBySlug[f.slug] = [];
                    faultsBySlug[f.slug].push(f);
                }
            });
            cableFaults = { faultedSlugs, faultsBySlug, raw: data };
            return cableFaults;
        } catch (e) {
            console.error('Cable status load failed:', e);
            return null;
        }
    }

    async function loadSubmarineCables() {
        if (layers.submarineCables.getLayers().length > 0) return; // already loaded
        try {
            const [cableRes, faultStatus] = await Promise.all([
                fetch('taiwan_cables.json?' + Date.now()),
                loadCableFaultStatus()
            ]);
            if (!cableRes.ok) return;
            const geoData = await cableRes.json();
            const faulted = faultStatus ? faultStatus.faultedSlugs : new Set();
            const faultDetails = faultStatus ? faultStatus.faultsBySlug : {};

            const lang = (typeof i18n !== 'undefined' && i18n.lang === 'en') ? 'en' : 'zh';
            const L_ = {
                status:  lang === 'en' ? 'Status'      : '\u72c0\u614b',
                type:    lang === 'en' ? 'Type'        : '\u985e\u578b',
                length:  lang === 'en' ? 'Length'      : '\u9577\u5ea6',
                rfs:     lang === 'en' ? 'In service'  : '\u555f\u7528',
                owners:  lang === 'en' ? 'Owners'      : '\u696d\u4e3b',
                twland:  lang === 'en' ? 'TW landing'  : '\u53f0\u7063\u767b\u9678',
                cnland:  lang === 'en' ? 'CN landing'  : '\u4e2d\u570b\u767b\u9678',
                faulted: lang === 'en' ? 'FAULT'       : '\u6545\u969c'
            };

            L.geoJSON(geoData, {
                style: f => {
                    const p = f.properties || {};
                    const slug = p.slug || '';
                    const isFaulted = faulted.has(slug);
                    const isPlanned = (p.status || '').indexOf('\u898f\u5283') >= 0;
                    return {
                        color: isFaulted ? '#ff2d55' : '#' + (p.color || 'ffd700'),
                        weight: isFaulted ? 3 : 2,
                        opacity: isFaulted ? 0.9 : (isPlanned ? 0.55 : 0.75),
                        dashArray: isPlanned ? '6,6' : null
                    };
                },
                onEachFeature: (f, layer) => {
                    const p = f.properties || {};
                    const slug = p.slug || '';
                    const name = p.name || slug.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    const faults = faultDetails[slug];

                    // Hover tooltip: name (+ fault summary)
                    let tip = name;
                    if (faults && faults.length > 0) {
                        const details = faults.map(ft =>
                            '\u26a0 ' + ft.segment + ': ' + (ft.description_zh || ft.description_en)
                        ).join('<br>');
                        tip = '<b style="color:#ff2d55">' + name + '</b><br>' + details;
                    }
                    layer.bindTooltip(tip, { sticky: true });

                    // Click popup: status / owners / landing points etc.
                    const rows = [];
                    const add = (label, val) => { if (val) rows.push('<div><b>' + label + '</b>: ' + val + '</div>'); };
                    const statusTxt = (faults && faults.length > 0)
                        ? '<span style="color:#ff3366;font-weight:700">\u26a0 ' + L_.faulted + '</span>'
                        : (p.status || '');
                    add(L_.status, statusTxt);
                    add(L_.type, p.cable_type);
                    add(L_.length, p.length);
                    add(L_.rfs, p.rfs);
                    add(L_.owners, p.owners);
                    add(L_.twland, p.tw_landings);
                    add(L_.cnland, p.cn_landings);
                    let popup = '<div style="font-size:12px;max-width:240px;line-height:1.5">'
                        + '<div style="font-weight:700;margin-bottom:4px;color:#00f5ff">' + name + '</div>'
                        + rows.join('');
                    if (faults && faults.length > 0) {
                        popup += '<div style="margin-top:4px;color:#ff6b6b">'
                            + faults.map(ft => '\u26a0 ' + ft.segment + ': ' + (ft.description_zh || ft.description_en)).join('<br>')
                            + '</div>';
                    }
                    popup += '</div>';
                    layer.bindPopup(popup);
                }
            }).addTo(layers.submarineCables);
        } catch (e) {
            console.error('Cable data load failed:', e);
        }
    }

    function getCableFaultStatus() {
        return cableFaults;
    }
    return {
        loadSubmarineCables,
        loadCableFaultStatus,
        getCableFaultStatus,
    };
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MapCablesFactory;
}

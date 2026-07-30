/**
 * Taiwan Gray Zone Monitor - Shared Mobile Navigation
 * Creates the bottom nav bar and the tools bottom sheet on all pages.
 * Loaded by every HTML page so the navigation persists across page switches.
 *
 * Nav model: 4 direct links (監測／暗船／統計／動畫) + 1 sheet (工具).
 * Every other page lives in the tools sheet and appears in exactly one place —
 * the old animation popover duplicated its own tab ("動畫" → "軌跡動畫") and had
 * swallowed the tool pages, leaving the tools tab opening an empty sheet.
 */
(function() {
    'use strict';

    if (window.innerWidth > 900) return;

    function init() {
        if (document.querySelector('.mobile-bottom-nav')) return;

        const path = window.location.pathname;
        const currentPage = path.split('/').pop() || 'index.html';
        const isBlogPage = currentPage === 'blog.html' || currentPage.startsWith('blog-');

        // /en/ only mirrors the static content pages (generate_i18n_pages.py
        // STATIC_PAGES). Everything else has to climb back out of the directory,
        // otherwise the whole bottom nav 404s on an English article.
        const inEnDir = /\/en\/[^/]*$/.test(path);
        const EN_MIRRORED = ['blog.html', 'intro.html', 'research-submarine-cable-legal.html'];
        const url = href => (inEnDir && EN_MIRRORED.indexOf(href) === -1) ? '../' + href : href;

        // 底部列的「動畫」直接連到軌跡動畫；其餘頁面全部收在「工具」面板，
        // 一個連結只出現在一個入口（底部列有的就不再列進面板）。
        const TOOL_PAGES = [
            { href: 'cn-fishing-animation.html', icon: '🐟', i18n: 'nav.cn_fishing', zh: '大陸漁船' },
            { href: 'identity-history.html', icon: '🔄', i18n: 'nav.identity', zh: '身分追蹤' },
            { href: 'ship-transfers.html', icon: '🚢', i18n: 'nav.transfers', zh: '旁靠偵測' },
            { href: 'network-traffic.html', icon: '📶', i18n: 'nav.network', zh: '網路流量' },
            { href: 'sar-ais-match.html', icon: '🛰️', i18n: 'nav.sar_match', zh: 'SAR×AIS 比對' },
            { href: 'blog.html', icon: '📖', i18n: 'nav.blog', zh: '深度文章', match: () => isBlogPage },
            { href: 'research-submarine-cable-legal.html', icon: '📜', i18n: 'nav.research', zh: '研究報告' },
            { href: 'intro.html', icon: 'ℹ️', i18n: 'nav.about', zh: '關於本站' }
        ];
        const isToolPage = TOOL_PAGES.some(p => p.match ? p.match() : p.href === currentPage);

        // --- Bottom Nav (5 tabs) ---
        const bottomNav = document.createElement('nav');
        bottomNav.className = 'mobile-bottom-nav';
        bottomNav.innerHTML = `
            <a href="${url('index.html')}" ${currentPage === 'index.html' ? 'class="active"' : ''}>
                <span class="nav-icon">🛰️</span>
                <span data-i18n="nav.mob_monitor">監測</span>
            </a>
            <a href="${url('dark-vessels.html')}" ${currentPage === 'dark-vessels.html' ? 'class="active"' : ''}>
                <span class="nav-icon">🔦</span>
                <span data-i18n="nav.mob_dark">暗船</span>
            </a>
            <a href="${url('statistics.html')}" ${currentPage === 'statistics.html' ? 'class="active"' : ''}>
                <span class="nav-icon">📊</span>
                <span data-i18n="nav.mob_stats">統計</span>
            </a>
            <a href="${url('ais-animation.html')}" ${currentPage === 'ais-animation.html' ? 'class="active"' : ''}>
                <span class="nav-icon">🎬</span>
                <span data-i18n="nav.mob_anim">動畫</span>
            </a>
            <button id="navToolsBtn" ${isToolPage ? 'class="active"' : ''}>
                <span class="nav-icon">⚙️</span>
                <span data-i18n="nav.mob_tools">工具</span>
            </button>
        `;
        document.body.appendChild(bottomNav);

        // --- Bottom Sheet Overlay ---
        const sheetOverlay = document.createElement('div');
        sheetOverlay.className = 'bottom-sheet-overlay';
        document.body.appendChild(sheetOverlay);

        // --- Bottom Sheet ---
        const sheet = document.createElement('div');
        sheet.className = 'bottom-sheet';
        sheet.id = 'bottomSheet';

        let sheetHTML = `<div class="bottom-sheet-handle"></div>`;

        // Page navigation — the tools tab must never open an empty sheet. On
        // index these links sit below the page-specific sections (app.js injects
        // above this block); on every other page they are the sheet's content.
        sheetHTML += `
        <div class="bottom-sheet-section bs-nav-section">
            <div class="bottom-sheet-title" data-i18n="bs.pages">其他頁面</div>
            <div class="bs-nav-grid">
                ${TOOL_PAGES.map(p => `
                <a href="${url(p.href)}" class="bs-nav-item${(p.match ? p.match() : p.href === currentPage) ? ' active' : ''}">
                    <span class="bs-nav-icon">${p.icon}</span>
                    <span data-i18n="${p.i18n}">${p.zh}</span>
                </a>`).join('')}
            </div>
        </div>`;

        // Page info section (always the sheet's last child)
        sheetHTML += `
        <div class="bottom-sheet-section">
            <div style="font-size:12px;color:var(--text-secondary)" id="bsUpdateInfo"></div>
        </div>`;

        sheet.innerHTML = sheetHTML;
        document.body.appendChild(sheet);
        const navSection = sheet.querySelector('.bs-nav-section');

        // --- Event Handlers ---
        let sheetOpen = false;

        function closeAll() {
            sheet.classList.remove('open');
            sheetOverlay.classList.remove('active');
            sheetOpen = false;
        }

        document.getElementById('navToolsBtn').addEventListener('click', () => {
            sheetOpen = !sheetOpen;
            sheet.classList.toggle('open', sheetOpen);
            sheetOverlay.classList.toggle('active', sheetOpen);
        });

        sheetOverlay.addEventListener('click', closeAll);

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

        // Public hook so a page (index/app.js) can inject page-specific sheet
        // sections without rebuilding the shared shell. New sections land above
        // the page-navigation block, so the page's own controls stay on top.
        window.MobileNav = {
            sheet: sheet,
            closeAll: closeAll,
            addSheetSection: function (html) {
                (navSection || sheet.lastElementChild).insertAdjacentHTML('beforebegin', html);
            }
        };

        if (typeof i18n !== 'undefined') i18n.applyAll();
    }

    // Run on DOMContentLoaded or immediately if already loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

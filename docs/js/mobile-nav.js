/**
 * Taiwan Gray Zone Monitor - Shared Mobile Navigation
 * Creates bottom nav bar, animation popover, and bottom sheet on all pages.
 * Loaded by every HTML page so the navigation persists across page switches.
 */
(function() {
    'use strict';

    if (window.innerWidth > 900) return;

    function init() {
        if (document.querySelector('.mobile-bottom-nav')) return;

        const currentPage = window.location.pathname.split('/').pop() || 'index.html';
        const animPages = ['weekly-animation.html', 'ais-animation.html', 'cn-fishing-animation.html', 'identity-history.html', 'ship-transfers.html'];
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
            <a href="ship-transfers.html" ${currentPage === 'ship-transfers.html' ? 'class="active"' : ''}>
                <span class="pop-icon">🚢</span>
                <span data-i18n="nav.transfers">旁靠偵測</span>
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

        let sheetHTML = `<div class="bottom-sheet-handle"></div>`;

        // Page info section
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

    // Run on DOMContentLoaded or immediately if already loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

#!/usr/bin/env python3
"""Generate /en/ localized static pages for true URL-level i18n.

The site is a zero-build static site whose pages carry both languages inline
(``lang-zh-only`` / ``lang-en-only`` blocks + ``data-i18n`` spans) and toggle
language client-side. To give answer engines and search crawlers genuine
per-language URLs (``/en/<page>.html``) instead of only the ``?lang=en`` query
variant, this script mirrors the *static content pages* into ``docs/en/`` with:

  * ``<html lang="en">`` and ``<body class="lang-en">`` so English renders
    immediately (i18n.js also detects the ``/en/`` path and forces English);
  * relative ``css/`` / ``js/`` / ``manifest`` / image paths rewritten to
    ``../`` so they resolve one directory deeper;
  * internal links pointing at pages that also have an ``/en/`` copy kept
    relative, others rewritten to ``../`` (root, where localStorage keeps EN);
  * ``canonical`` / ``og:url`` pointing at the ``/en/`` URL (hreflang on the
    source pages already declares the reciprocal relationship).

Only static pages are mirrored. The interactive dashboard / data pages fetch
JSON relative to their own URL, so an ``/en/`` copy would 404 on data; those
pages intentionally keep the ``?lang=en`` variant instead.

Run from anywhere; idempotent (``docs/en/`` is rebuilt each time).
"""
import re
import pathlib

BASE = "https://s0914712.github.io/taiwan-grayzone-monitor/"
DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
EN_DIR = DOCS / "en"

# Static content pages safe to mirror — verified to load no JSON-fetching
# modules (only i18n.js + mobile-nav.js). Keep in sync with the app-page set:
# index, dark-vessels, statistics, identity-history, ship-transfers,
# ais-animation, cn-fishing-animation are deliberately NOT mirrored.
STATIC_PAGES = [
    "blog.html",
    "intro.html",
    "research-submarine-cable-legal.html",
    "blog-methodology.html",
    "blog-what-is-ais.html",
    "blog-what-is-submarine-cable.html",
    "blog-cable-threats.html",
    "blog-taiwan-cable-status.html",
    "blog-taiwan-enforcement.html",
    "blog-what-is-dark-vessel.html",
    "blog-what-is-ship-to-ship-transfer.html",
    "blog-what-is-ais-spoofing.html",
    "blog-what-is-maritime-gray-zone.html",
    "blog-gray-zone-glossary.html",
]
EN_SET = set(STATIC_PAGES)

SITE = "Taiwan Gray Zone Monitor"

# English <head> metadata for each mirrored page. `title` -> <title>;
# `social` -> og:title / twitter:title; `desc` -> description / og:description /
# twitter:description; `kw` -> keywords. A missing source tag is a no-op.
EN_META = {
    "blog.html": {
        "title": f"In-Depth Articles | {SITE}",
        "social": "In-Depth Articles | Taiwan Gray Zone & Submarine Cable Monitor",
        "desc": "In-depth articles on Taiwan submarine-cable security, AIS vessel monitoring, gray-zone tactics, and this project's methodology.",
        "kw": "submarine cable, AIS, gray zone, shadow fleet, Taiwan Strait security, dark vessel",
    },
    "intro.html": {
        "title": f"About | {SITE}",
        "social": "About | Taiwan Gray Zone & Submarine Cable Monitor",
        "desc": "An open-source OSINT project monitoring maritime gray-zone activity and submarine-cable security around Taiwan, with FAQs and a usage guide.",
        "kw": "gray zone, Taiwan Strait, submarine cable, dark vessel, AIS, maritime surveillance, OSINT",
    },
    "research-submarine-cable-legal.html": {
        "title": f"Critical Nodes of an Invisible War: PRC Threats to Taiwan's Submarine Cables | {SITE}",
        "social": "PRC Threats to Taiwan's Submarine Cables",
        "desc": "Research on the legal framework and enforcement challenges around PRC gray-zone threats to Taiwan's submarine cables (UNCLOS, flag-state jurisdiction).",
        "kw": "submarine cable, UNCLOS, flag state, gray zone, Taiwan, legal enforcement",
    },
    "blog-methodology.html": {
        "title": f"Our Methodology — How Vessels Are Scored | {SITE}",
        "social": "Our Methodology — How Vessels Are Scored",
        "desc": "How the Taiwan Gray Zone Monitor scores suspicious vessels: data sources, the 8-criterion threat-scoring engine, vessel-type weighting, maritime-zone scoring, and limitations.",
        "kw": "AIS analysis, threat scoring, CSIS methodology, OSINT, GFW SAR, submarine cable monitoring",
    },
    "blog-what-is-ais.html": {
        "title": f"What Is AIS? Why It Matters for Submarine-Cable Security | {SITE}",
        "social": "What Is AIS? Why It Matters for Cable Security",
        "desc": "AIS is a ship's digital passport. Learn how AIS works, why vessels switch it off to 'go dark', how SAR satellites find them, and the link to submarine-cable security.",
        "kw": "AIS, automatic identification system, dark vessel, SAR satellite, MMSI, going dark, vessel tracking",
    },
    "blog-what-is-submarine-cable.html": {
        "title": f"What Is a Submarine Cable? Why It Matters | {SITE}",
        "social": "What Is a Submarine Cable? Why It Matters",
        "desc": "Submarine cables carry over 95% of international internet traffic. Learn how they work, how they are laid and repaired, and why they are Taiwan's digital lifeline.",
        "kw": "submarine cable, fiber optic, internet infrastructure, Taiwan, cable laying, cable repair",
    },
    "blog-cable-threats.html": {
        "title": f"Threats to Submarine Cables: Shadow Fleets & Gray-Zone Sabotage | {SITE}",
        "social": "Threats to Submarine Cables",
        "desc": "Natural, accidental, and deliberate threats to submarine cables — shadow fleets, anchor dragging vs sabotage, and why it is the perfect gray-zone tactic near Taiwan.",
        "kw": "submarine cable threats, shadow fleet, gray zone warfare, anchor dragging, sabotage, Taiwan",
    },
    "blog-taiwan-cable-status.html": {
        "title": f"Taiwan's Submarine Cable Situation | {SITE}",
        "social": "Taiwan's Submarine Cable Situation",
        "desc": "Taiwan's strategic cable density, landing stations, outer-island vulnerability, break trends, repair challenges, and government resilience efforts.",
        "kw": "Taiwan submarine cable, Matsu cable, landing station, cable break, resilience",
    },
    "blog-taiwan-enforcement.html": {
        "title": f"Taiwan's Enforcement Framework & Challenges | {SITE}",
        "social": "Taiwan's Enforcement Framework & Challenges",
        "desc": "Taiwan's legal toolbox for cable protection, the flag-state jurisdiction gap, why perpetrators are rarely prosecuted, and how other countries are responding.",
        "kw": "submarine cable law, UNCLOS, flag state jurisdiction, gray zone enforcement, Taiwan",
    },
    "blog-what-is-dark-vessel.html": {
        "title": f"What Is a Dark Vessel? SAR vs AIS Detection Near Taiwan | {SITE}",
        "social": "What Is a Dark Vessel?",
        "desc": "A dark vessel is a ship that turns off AIS but is still detected by SAR satellites. Learn how SAR and AIS are cross-referenced and why dark vessels matter around Taiwan.",
        "kw": "dark vessel, AIS off, SAR satellite detection, Taiwan Strait, Global Fishing Watch, maritime gray zone",
    },
    "blog-what-is-ship-to-ship-transfer.html": {
        "title": f"What Is a Ship-to-Ship (STS) Transfer? How It's Detected | {SITE}",
        "social": "What Is a Ship-to-Ship (STS) Transfer?",
        "desc": "An STS transfer is two ships moving cargo alongside at sea. Learn how STS is detected, lawful pair-trawling vs suspicious transfers, and the link to sanctions evasion.",
        "kw": "ship-to-ship transfer, STS, rendezvous, pair trawling, sanctions evasion, shadow fleet, Taiwan Strait",
    },
    "blog-what-is-ais-spoofing.html": {
        "title": f"What Is AIS Spoofing? Detecting Fake Positions & Identity Fraud | {SITE}",
        "social": "What Is AIS Spoofing?",
        "desc": "AIS spoofing is a vessel broadcasting a false position, speed, or another ship's identity. Learn how it differs from going dark, how it is detected (impossible speed, box/circle tracks, registry mismatch), and why it matters near Taiwan.",
        "kw": "AIS spoofing, fake GPS position, identity fraud, MMSI, IMO, impossible speed, submarine cable, gray zone, Taiwan Strait",
    },
    "blog-what-is-maritime-gray-zone.html": {
        "title": f"What Is Maritime Gray-Zone Activity? Tactics Around Taiwan | {SITE}",
        "social": "What Is Maritime Gray-Zone Activity?",
        "desc": "Maritime gray-zone activity is coercion kept below the threshold of open conflict. An overview of dark vessels, AIS spoofing, ship-to-ship transfers, shadow fleets, and cable threats — and how OSINT tracks them around Taiwan.",
        "kw": "maritime gray zone, gray zone operations, dark vessel, AIS spoofing, ship-to-ship transfer, shadow fleet, submarine cable, Taiwan Strait, OSINT",
    },
    "blog-gray-zone-glossary.html": {
        "title": f"Taiwan Maritime Gray-Zone Glossary (Bilingual) | {SITE}",
        "social": "Taiwan Maritime Gray-Zone Glossary (Bilingual)",
        "desc": "Bilingual (English/Chinese) definitions of maritime gray-zone, OSINT, AIS/SAR, and law-of-the-sea terms: dark vessel, AIS spoofing, MMSI, STS, SAR, flag of convenience, territorial baseline.",
        "kw": "gray zone glossary, dark vessel, AIS spoofing, MMSI, ship-to-ship transfer, SAR, flag of convenience, territorial baseline",
    },
}

_SKIP_PREFIXES = ("http://", "https://", "//", "#", "mailto:", "tel:",
                  "javascript:", "data:", "../")
_ATTR_RE = re.compile(r'\b(href|src)="([^"]+)"')
_HTML_LINK_RE = re.compile(r'^([^/?#]+\.html)(\?[^#]*)?(#.*)?$')


def _rewrite_url(value: str) -> str:
    """Rewrite a single href/src value for a page living under docs/en/."""
    if value.startswith(_SKIP_PREFIXES):
        return value
    m = _HTML_LINK_RE.match(value)
    if m:
        fname, query, frag = m.group(1), m.group(2) or "", m.group(3) or ""
        if fname in EN_SET:
            return value  # sibling /en/ page — relative link still resolves
        return f"../{fname}{query}{frag}"  # app/other page lives at root
    # any other relative asset (css/, js/, manifest.json, *.png, *.ico, …)
    return f"../{value}"


def _rewrite_attrs(html: str) -> str:
    return _ATTR_RE.sub(lambda m: f'{m.group(1)}="{_rewrite_url(m.group(2))}"', html)


def _esc(s: str) -> str:
    """Escape a value for use inside HTML text / a double-quoted attribute."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _sub_once(html: str, pattern: str, value: str) -> str:
    """Replace the inner content of the first match (groups 1+2 bracket it)."""
    return re.sub(pattern, lambda m: m.group(1) + _esc(value) + m.group(2),
                  html, count=1)


def _rewrite_en_head(html: str, meta: dict) -> str:
    """Translate the <head> metadata of a mirror page to English."""
    html = _sub_once(html, r"(<title>).*?(</title>)", meta["title"])
    html = _sub_once(html, r'(<meta name="description" content=").*?(">)', meta["desc"])
    html = _sub_once(html, r'(<meta name="keywords" content=").*?(">)', meta["kw"])
    html = _sub_once(html, r'(<meta property="og:title" content=").*?(">)', meta["social"])
    html = _sub_once(html, r'(<meta property="og:description" content=").*?(">)', meta["desc"])
    html = _sub_once(html, r'(<meta name="twitter:title" content=").*?(">)', meta["social"])
    html = _sub_once(html, r'(<meta name="twitter:description" content=").*?(">)', meta["desc"])
    # Locale: an /en/ page is en_US, with zh_TW as the alternate.
    html = html.replace('<meta property="og:locale" content="zh_TW">',
                        '<meta property="og:locale" content="en_US">', 1)
    html = html.replace('<meta property="og:locale:alternate" content="en_US">',
                        '<meta property="og:locale:alternate" content="zh_TW">', 1)
    return html


def _rewrite_en_jsonld(html: str, page: str) -> str:
    """Point JSON-LD url/item at the /en/ page and set inLanguage to en."""
    src = re.escape(BASE + page)
    html = re.sub(
        r'("(?:url|item)"\s*:\s*")' + src + r'(")',
        lambda m: m.group(1) + BASE + "en/" + page + m.group(2), html)
    html = re.sub(r'"inLanguage"\s*:\s*(?:\[[^\]]*\]|"[^"]*")',
                  '"inLanguage":"en"', html)
    return html


def generate_page(page: str) -> str:
    html = (DOCS / page).read_text(encoding="utf-8")

    # Force English document + initial paint.
    html = html.replace('<html lang="zh-TW">', '<html lang="en">', 1)
    if "<body class=" in html[:html.find("<body") + 200] and "<body>" not in html:
        html = re.sub(r'<body class="([^"]*)"',
                      lambda m: f'<body class="{m.group(1)} lang-en"', html, count=1)
    else:
        html = html.replace("<body>", '<body class="lang-en">', 1)

    # Canonical + og:url should point at the /en/ URL for this page.
    html = html.replace(f'canonical" href="{BASE}{page}"',
                        f'canonical" href="{BASE}en/{page}"')
    html = html.replace(f'og:url" content="{BASE}{page}"',
                        f'og:url" content="{BASE}en/{page}"')

    # English <head> metadata + JSON-LD url/inLanguage (clean SEO for /en/).
    if page in EN_META:
        html = _rewrite_en_head(html, EN_META[page])
    html = _rewrite_en_jsonld(html, page)

    # Fix relative asset/link paths for the deeper directory.
    html = _rewrite_attrs(html)

    marker = (f"<!-- AUTO-GENERATED English mirror of /{page} by "
              f"src/generate_i18n_pages.py — do not edit by hand. -->\n")
    html = html.replace("<!DOCTYPE html>", "<!DOCTYPE html>\n" + marker, 1)
    return html


def main() -> None:
    EN_DIR.mkdir(parents=True, exist_ok=True)
    # Clean stale generated pages (only .html; keep nothing else there).
    for old in EN_DIR.glob("*.html"):
        old.unlink()

    written = []
    for page in STATIC_PAGES:
        src = DOCS / page
        if not src.exists():
            print(f"  WARN source missing, skipped: {page}")
            continue
        (EN_DIR / page).write_text(generate_page(page), encoding="utf-8")
        written.append(page)

    print(f"Generated {len(written)} English page(s) into docs/en/:")
    for p in written:
        print(f"  en/{p}")


if __name__ == "__main__":
    main()

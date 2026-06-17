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
]
EN_SET = set(STATIC_PAGES)

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

"""Tests for generate_dashboard sitemap lastmod scoping (issue 2.1)."""
import generate_dashboard as gd

BASE = "https://s0914712.github.io/taiwan-grayzone-monitor/"


def _url(loc, lastmod="2026-01-01"):
    return (f"  <url>\n    <loc>{loc}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>daily</changefreq>\n  </url>\n")


SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset>\n'
    + _url(BASE)                                   # root dashboard -> bump
    + _url(BASE + "dark-vessels.html")             # data -> bump
    + _url(BASE + "ship-transfers.html")           # data -> bump
    + _url(BASE + "ais-animation.html")            # data -> bump
    + _url(BASE + "reports/")                       # daily -> bump
    + _url(BASE + "blog-methodology.html")         # evergreen -> keep
    + _url(BASE + "blog-what-is-dark-vessel.html") # evergreen -> keep
    + _url(BASE + "research-submarine-cable-legal.html")  # evergreen -> keep
    + _url(BASE + "en/blog-methodology.html")      # /en mirror -> keep
    + "</urlset>\n"
)


def test_data_pages_get_today():
    out = gd.bump_sitemap_lastmod(SITEMAP, "2026-06-20")
    for loc in (BASE, BASE + "dark-vessels.html", BASE + "ship-transfers.html",
                BASE + "ais-animation.html", BASE + "reports/"):
        block = out.split(f"<loc>{loc}</loc>")[1].split("</url>")[0]
        assert "<lastmod>2026-06-20</lastmod>" in block, loc


def test_evergreen_pages_unchanged():
    out = gd.bump_sitemap_lastmod(SITEMAP, "2026-06-20")
    for loc in (BASE + "blog-methodology.html",
                BASE + "blog-what-is-dark-vessel.html",
                BASE + "research-submarine-cable-legal.html",
                BASE + "en/blog-methodology.html"):
        block = out.split(f"<loc>{loc}</loc>")[1].split("</url>")[0]
        assert "<lastmod>2026-01-01</lastmod>" in block, loc


def test_root_not_confused_with_subpaths():
    # the root suffix 'taiwan-grayzone-monitor/' must not match blog URLs
    out = gd.bump_sitemap_lastmod(SITEMAP, "2026-06-20")
    blog = out.split(f"<loc>{BASE}blog-methodology.html</loc>")[1].split("</url>")[0]
    assert "2026-06-20" not in blog

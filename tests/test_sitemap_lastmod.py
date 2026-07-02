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


def test_report_entries_added_once(tmp_path):
    (tmp_path / "2026-06-30.html").write_text("x")
    (tmp_path / "2026-07-01.html").write_text("x")
    (tmp_path / "index.html").write_text("x")  # non-dated -> skipped

    out = gd.sync_report_sitemap_entries(SITEMAP, reports_dir=tmp_path)
    for name in ("2026-06-30", "2026-07-01"):
        block = out.split(f"<loc>{BASE}reports/{name}.html</loc>")[1].split("</url>")[0]
        assert f"<lastmod>{name}</lastmod>" in block
    assert f"{BASE}reports/index.html" not in out
    assert out.rstrip().endswith("</urlset>")

    # idempotent: running again adds nothing
    assert gd.sync_report_sitemap_entries(out, reports_dir=tmp_path) == out


def test_report_entries_dated_lastmod_not_bumped(tmp_path):
    (tmp_path / "2026-06-30.html").write_text("x")
    out = gd.sync_report_sitemap_entries(SITEMAP, reports_dir=tmp_path)
    out = gd.bump_sitemap_lastmod(out, "2026-07-02")
    block = out.split(f"<loc>{BASE}reports/2026-06-30.html</loc>")[1].split("</url>")[0]
    assert "<lastmod>2026-06-30</lastmod>" in block

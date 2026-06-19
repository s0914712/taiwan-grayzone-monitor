"""Tests for src/generate_i18n_pages.py English-mirror metadata rewrites."""
import re
import json

import generate_i18n_pages as gi

BASE = gi.BASE


def _ld_blocks(html):
    return re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)


def test_dark_vessel_mirror_has_english_head():
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    title = re.search(r"<title>(.*?)</title>", out).group(1)
    assert "Dark Vessel" in title and "暗船" not in title
    assert '<html lang="en">' in out
    assert 'og:locale" content="en_US"' in out
    assert 'og:locale:alternate" content="zh_TW"' in out
    # description / og:title are English
    desc = re.search(r'<meta name="description" content="(.*?)">', out).group(1)
    assert "dark vessel" in desc.lower()
    og = re.search(r'<meta property="og:title" content="(.*?)">', out).group(1)
    assert "Dark Vessel" in og


def test_dark_vessel_mirror_jsonld_url_and_inlanguage():
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    blocks = _ld_blocks(out)
    assert blocks, "expected JSON-LD"
    joined = "".join(blocks)
    # the article's own url/item must be the /en/ URL, never the bare zh page
    assert f'"{BASE}en/blog-what-is-dark-vessel.html"' in joined
    assert f'"url":"{BASE}blog-what-is-dark-vessel.html"' not in joined
    assert f'"item":"{BASE}blog-what-is-dark-vessel.html"' not in joined
    # inLanguage normalized to en, all blocks still valid JSON
    for b in blocks:
        d = json.loads(b)
        assert d  # parses
    assert '"inLanguage":"en"' in joined
    assert '"inLanguage":["zh-TW","en"]' not in joined.replace(" ", "")


def test_missing_meta_tag_is_graceful():
    # blog.html lacks twitter:description / og:locale — must not raise, and the
    # tags that DO exist become English.
    out = gi.generate_page("blog.html")
    title = re.search(r"<title>(.*?)</title>", out).group(1)
    assert "Articles" in title and "深度文章" not in title


def test_canonical_points_to_en():
    out = gi.generate_page("blog-methodology.html")
    assert f'canonical" href="{BASE}en/blog-methodology.html"' in out


def test_all_static_pages_have_en_meta():
    # every mirrored page should have an EN_META entry so none ships zh head
    assert set(gi.STATIC_PAGES) <= set(gi.EN_META)

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


_CJK = re.compile(r"[一-鿿]")


def _walk(node):
    """Yield every dict in a JSON-LD structure (handles @graph / lists)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_faqpage_is_translated_to_english():
    # FAQ questions/answers on a mirror page must be English, not Chinese.
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    faqs = [d for b in _ld_blocks(out) for d in _walk(json.loads(b))
            if d.get("@type") == "FAQPage"]
    assert faqs, "expected a FAQPage block"
    for q in faqs[0]["mainEntity"]:
        assert not _CJK.search(q["name"]), q["name"]
        assert not _CJK.search(q["acceptedAnswer"]["text"])
    assert faqs[0]["mainEntity"][0]["name"] == "What is a dark vessel?"


def test_graph_faqpage_translated():
    # intro.html nests FAQPage inside @graph — must also be translated.
    out = gi.generate_page("intro.html")
    faqs = [d for b in _ld_blocks(out) for d in _walk(json.loads(b))
            if d.get("@type") == "FAQPage"]
    assert faqs
    for q in faqs[0]["mainEntity"]:
        assert not _CJK.search(q["acceptedAnswer"]["text"])


def test_breadcrumb_names_translated():
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    crumbs = [d for b in _ld_blocks(out) for d in _walk(json.loads(b))
              if d.get("@type") == "BreadcrumbList"][0]
    names = [i["name"] for i in crumbs["itemListElement"]]
    assert names == ["Home", "In-Depth Articles", "What Is a Dark Vessel?"]


def test_article_headline_description_english_with_mainentityofpage():
    out = gi.generate_page("blog-what-is-ais-spoofing.html")
    arts = [d for b in _ld_blocks(out) for d in _walk(json.loads(b))
            if (set(d["@type"]) if isinstance(d.get("@type"), list)
                else {d.get("@type")}) & gi._ARTICLE_TYPES
            and d.get("url", "").endswith("en/blog-what-is-ais-spoofing.html")]
    assert arts, "expected the page's own Article node"
    art = arts[0]
    assert not _CJK.search(art["headline"])
    assert not _CJK.search(art["description"])
    assert art["mainEntityOfPage"]["@id"].endswith(
        "en/blog-what-is-ais-spoofing.html")
    # speakable only when the page exposes an .ai-summary block
    assert art["speakable"]["cssSelector"] == [".ai-summary"]


def test_blog_index_itemlist_translated():
    out = gi.generate_page("blog.html")
    lists = [d for b in _ld_blocks(out) for d in _walk(json.loads(b))
             if d.get("@type") == "ItemList"][0]
    assert not _CJK.search(lists["name"])
    for item in lists["itemListElement"]:
        assert not _CJK.search(item["name"]), item["name"]


def test_all_faq_pages_have_en_faq_entry():
    # any mirrored page whose source carries a FAQPage must have an EN_FAQ.
    import pathlib
    for page in gi.STATIC_PAGES:
        src = (gi.DOCS / page).read_text(encoding="utf-8")
        if "FAQPage" in src:
            assert page in gi.EN_FAQ, f"{page} has FAQPage but no EN_FAQ"


_BODY_CJK = re.compile(r"[一-鿿]")


def _visible_text(html):
    body = html[html.find("<body"):]
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


def test_mirror_dom_is_english_only():
    # No Chinese-only DOM blocks survive, and data-i18n nav text is resolved.
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    assert re.search(r'class="[^"]*lang-zh-only', out) is None
    # data-i18n nav resolved to English, Chinese gone
    assert "Gray Zone Monitor" in out and "灰色地帶監測" not in out
    assert "🛰️ Taiwan Gray Zone" in out
    # the only Chinese left in visible text is the 中 language toggle
    leftover = [c for c in _BODY_CJK.findall(_visible_text(out)) if c != "中"]
    assert leftover == [], leftover


def test_mirror_static_labels_translated():
    # Related-chip / series labels (no data-i18n, no en variant) are translated.
    out = gi.generate_page("blog-what-is-shadow-fleet.html")
    assert "RELATED →" in out and "延伸閱讀" not in out
    assert "Cable Threats" in out
    leftover = [c for c in _BODY_CJK.findall(_visible_text(out)) if c != "中"]
    assert leftover == [], leftover


def test_blog_index_dom_english_only():
    # blog.html has the heaviest UI chrome (filters, cards, series dots).
    out = gi.generate_page("blog.html")
    assert re.search(r'class="[^"]*lang-zh-only', out) is None
    leftover = [c for c in _BODY_CJK.findall(_visible_text(out)) if c != "中"]
    assert leftover == [], leftover


def test_glossary_gzh_terms_removed():
    out = gi.generate_page("blog-gray-zone-glossary.html")
    assert 'class="g-zh"' not in out          # Chinese term labels dropped
    assert 'class="g-en"' in out              # English term labels kept


def test_shadow_fleet_article_registered():
    page = "blog-what-is-shadow-fleet.html"
    assert page in gi.STATIC_PAGES
    assert page in gi.EN_META and page in gi.EN_FAQ
    assert page in gi.EN_BREADCRUMB_LAST
    # the source page exists and mirrors without error
    out = gi.generate_page(page)
    assert "What Is a Shadow Fleet?" in out
    assert '<html lang="en">' in out


def test_maritime_zones_article_registered():
    page = "blog-taiwan-maritime-zones.html"
    assert page in gi.STATIC_PAGES
    assert page in gi.EN_META and page in gi.EN_FAQ
    assert page in gi.EN_BREADCRUMB_LAST
    out = gi.generate_page(page)
    assert "Taiwan's Maritime Zones" in out
    leftover = [c for c in _BODY_CJK.findall(_visible_text(out)) if c != "中"]
    assert leftover == [], leftover


def test_topic_cluster_footer_english_only():
    # The shared topic-cluster footer must render in English on the mirror.
    out = gi.generate_page("blog-what-is-dark-vessel.html")
    assert "Related gray-zone topics" in out
    assert "相關灰色地帶主題" not in out
    assert "Maritime zones" in out and "Shadow fleets" in out


def test_mirror_lang_toggle_is_safe_link():
    # On the English-only mirror the toggle must NOT call i18n.toggle()
    # (which would blank the page); it must be a link to the Chinese page.
    out = gi.generate_page("blog-taiwan-maritime-zones.html")
    assert "i18n.toggle()" not in out
    m = re.search(r'<a id="langToggle"[^>]*href="([^"]+)"', out)
    assert m, "langToggle should be an anchor"
    assert m.group(1) == "../blog-taiwan-maritime-zones.html"


def test_mirror_english_content_not_gated_on_body_class():
    # The dangerous rule (hide .lang-en-only when body isn't .lang-en) must be
    # removed on the mirror so the page can never blank out.
    out = gi.generate_page("blog-what-is-shadow-fleet.html")
    assert "body:not(.lang-en) .lang-en-only{display:none" not in out
    assert ".lang-zh-only{display:none!important}" in out


def test_glossary_definedterm_is_english_only():
    # P0.4 — DefinedTermSet/DefinedTerm in the mirror must be English-only:
    # no Chinese alternateName and no bilingual "EN / 中文" descriptions.
    out = gi.generate_page("blog-gray-zone-glossary.html")
    block = [b for b in _ld_blocks(out)
             if json.loads(b).get("@type") == "DefinedTermSet"][0]
    d = json.loads(block)
    assert "alternateName" not in d
    for term in d["hasDefinedTerm"]:
        assert "alternateName" not in term, term["termCode"]
        assert not _BODY_CJK.search(term["description"]), term["termCode"]

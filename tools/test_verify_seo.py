import json, subprocess, sys, tempfile, unittest
from pathlib import Path

VERIFY = Path(__file__).parent / "verify-seo.py"
SITE = "https://spookwerk.app"
ORG_ID = f"{SITE}/#organization"
SITE_ID = f"{SITE}/#website"

ORG = {"@type": "Organization", "@id": ORG_ID, "name": "Spookwerk",
       "logo": f"{SITE}/logo.png"}
WEB = {"@type": "WebSite", "@id": SITE_ID,
       "publisher": {"@id": ORG_ID}}


def sitewide(org=None):
    g = {"@context": "https://schema.org", "@graph": [org or ORG, WEB]}
    return f'<script type="application/ld+json">{json.dumps(g)}</script>'


def second(*nodes):
    g = {"@context": "https://schema.org", "@graph": list(nodes)}
    return f'<script type="application/ld+json">{json.dumps(g)}</script>'


def crumbs(canonical, items):
    # items: list of (name, url_or_None); last entry should be (name, None)
    return {"@type": "BreadcrumbList", "@id": canonical + "#breadcrumbs",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": n,
                 **({"item": u} if u else {})}
                for i, (n, u) in enumerate(items)]}


def blogposting(canonical, lang, author=None):
    return {"@type": "BlogPosting", "@id": canonical + "#blogposting",
            "headline": "T", "description": "D", "inLanguage": lang,
            "datePublished": "2026-06-07",
            "image": f"{SITE}/og/default.png", "url": canonical,
            "mainEntityOfPage": canonical,
            "author": author or {"@id": ORG_ID},
            "publisher": {"@id": ORG_ID}, "isPartOf": {"@id": SITE_ID}}


def swapp(canonical):
    return {"@type": "SoftwareApplication", "@id": canonical + "#app",
            "name": "Demo", "description": "D", "url": canonical,
            "operatingSystem": "iOS",
            "applicationCategory": "UtilitiesApplication",
            "publisher": {"@id": ORG_ID},
            "offers": {"@type": "Offer", "price": "0",
                       "priceCurrency": "EUR"}}


def blogentity(canonical, lang):
    return {"@type": "Blog", "@id": f"{SITE}/blog/#blog", "name": "B",
            "description": "D", "inLanguage": lang, "url": canonical,
            "publisher": {"@id": ORG_ID}}


def page(canonical, *, og_type="website", alts=None, extra_ld=None,
         drop=None, org=None, raw=None):
    drop = drop or set()
    L = []
    if "canonical" not in drop:
        L.append(f'<link rel="canonical" href="{canonical}">')
    for hl, href in (alts or []):
        L.append(f'<link rel="alternate" hreflang="{hl}" href="{href}">')
    if "description" not in drop:
        L.append('<meta name="description" content="D">')
    if "og" not in drop:
        L += [f'<meta property="og:type" content="{og_type}">',
              '<meta property="og:title" content="T">',
              '<meta property="og:description" content="D">',
              f'<meta property="og:url" content="{canonical}">',
              f'<meta property="og:image" content="{SITE}/og/default.png">',
              '<meta property="og:site_name" content="Spookwerk">']
        if og_type == "article":
            L.append('<meta property="article:published_time" content="2026-06-07">')
    if "tw" not in drop:
        L += ['<meta name="twitter:card" content="summary_large_image">',
              '<meta name="twitter:title" content="T">',
              '<meta name="twitter:description" content="D">',
              f'<meta name="twitter:image" content="{SITE}/og/default.png">']
    if "jsonld" not in drop:
        L.append(sitewide(org))
    for x in (extra_ld or []):
        L.append(second(*x) if isinstance(x, list) else second(x))
    for r in (raw or []):
        L.append(r)
    head = "\n".join(L)
    return f"<!DOCTYPE html><html><head>{head}</head><body>x</body></html>"


HOME = f"{SITE}/"
BLOG_C = f"{SITE}/blog/"
EN_P = f"{SITE}/blog/posts/en/p.html"
NL_P = f"{SITE}/blog/posts/nl/p.html"
APP_C = f"{SITE}/apps/demo/"
POST_ALTS = [("en", EN_P), ("nl", NL_P), ("x-default", EN_P)]


def write(d, rel, content):
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def post_page(canonical, lang, **kw):
    extra = kw.pop("extra_ld", [[blogposting(canonical, lang),
                                 crumbs(canonical, [("Home", HOME),
                                                    ("Blog", BLOG_C),
                                                    ("Post", None)])]])
    return page(canonical, og_type="article", alts=POST_ALTS,
                extra_ld=extra, **kw)


def scaffold(d):
    write(d, "index.html", page(HOME))
    write(d, "blog/index.html",
          page(BLOG_C, extra_ld=[[blogentity(BLOG_C, "en"),
                                  crumbs(BLOG_C, [("Home", HOME),
                                                  ("Blog", None)])]]))
    write(d, "blog/posts/en/p.html", post_page(EN_P, "en"))
    write(d, "blog/posts/nl/p.html", post_page(NL_P, "nl"))
    write(d, "apps/demo/index.html",
          page(APP_C, extra_ld=[[swapp(APP_C),
                                 crumbs(APP_C, [("Home", HOME),
                                                ("Demo", None)])]]))
    # noindex redirect stub: must be skipped by discovery
    write(d, "apps/old/index.html",
          '<html><head><meta name="robots" content="noindex"></head>'
          '<body>moved</body></html>')
    (d / "logo.png").write_bytes(b"x")
    (d / "og").mkdir(exist_ok=True)
    (d / "og" / "default.png").write_bytes(b"x")


class T(unittest.TestCase):
    def _run(self, root):
        r = subprocess.run([sys.executable, str(VERIFY), "--root", str(root)],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def _case(self, mutate=None):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            scaffold(d)
            if mutate:
                mutate(d)
            return self._run(d)

    # ---- A-era intents, preserved ----
    def test_good_site_passes_and_stub_skipped(self):
        code, out = self._case()
        self.assertEqual(code, 0, out)
        self.assertNotIn("apps/old", out)

    def test_missing_canonical_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html", post_page(EN_P, "en", drop={"canonical"})))
        self.assertNotEqual(code, 0)
        self.assertIn("canonical", out)

    def test_missing_jsonld_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html",
            page(EN_P, og_type="article", alts=POST_ALTS, drop={"jsonld"})))
        self.assertNotEqual(code, 0)

    def test_missing_og_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html", post_page(EN_P, "en", drop={"og"})))
        self.assertNotEqual(code, 0)

    def test_multitoken_rel_canonical_recognized(self):
        code, out = self._case(lambda d: write(
            d, "index.html",
            page(HOME, drop={"canonical"},
                 raw=[f'<link rel="canonical preload" href="{HOME}">'])))
        self.assertEqual(code, 0, out)

    def test_broken_reciprocity_fails(self):
        bad_alts = [("nl", NL_P), ("x-default", NL_P)]
        code, out = self._case(lambda d: write(
            d, "blog/posts/nl/p.html",
            page(NL_P, og_type="article", alts=bad_alts,
                 extra_ld=[[blogposting(NL_P, "nl"),
                            crumbs(NL_P, [("Home", HOME), ("Blog", BLOG_C),
                                          ("Post", None)])]])))
        self.assertNotEqual(code, 0)
        self.assertIn("reciprocal", out)

    def test_jsonld_missing_org_id_fails(self):
        bad_org = {"@type": "Organization", "name": "Spookwerk"}
        code, out = self._case(lambda d: write(
            d, "index.html", page(HOME, org=bad_org)))
        self.assertNotEqual(code, 0)
        self.assertIn("Organization", out)

    def test_missing_asset_fails(self):
        code, out = self._case(lambda d: (d / "og" / "default.png").unlink())
        self.assertNotEqual(code, 0)
        self.assertIn("og:image", out)

    # ---- B: new checks ----
    def test_missing_description_fails(self):
        code, out = self._case(lambda d: write(
            d, "index.html", page(HOME, drop={"description"})))
        self.assertNotEqual(code, 0)
        self.assertIn("description", out)

    def test_post_missing_blogposting_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html",
            post_page(EN_P, "en",
                      extra_ld=[[crumbs(EN_P, [("Home", HOME), ("Blog", BLOG_C),
                                               ("Post", None)])]])))
        self.assertNotEqual(code, 0)
        self.assertIn("BlogPosting", out)

    def test_person_in_jsonld_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html",
            post_page(EN_P, "en",
                      extra_ld=[[blogposting(EN_P, "en",
                                             author={"@type": "Person",
                                                     "name": "X"}),
                                 crumbs(EN_P, [("Home", HOME), ("Blog", BLOG_C),
                                               ("Post", None)])]])))
        self.assertNotEqual(code, 0)
        self.assertIn("Person", out)

    def test_missing_breadcrumbs_fails(self):
        code, out = self._case(lambda d: write(
            d, "apps/demo/index.html", page(APP_C, extra_ld=[[swapp(APP_C)]])))
        self.assertNotEqual(code, 0)
        self.assertIn("BreadcrumbList", out)

    def test_breadcrumb_dead_item_fails(self):
        bad = crumbs(EN_P, [("Home", HOME), ("Blog", f"{SITE}/nope/"),
                            ("Post", None)])
        code, out = self._case(lambda d: write(
            d, "blog/posts/en/p.html",
            post_page(EN_P, "en", extra_ld=[[blogposting(EN_P, "en"), bad]])))
        self.assertNotEqual(code, 0)
        self.assertIn("breadcrumb", out)

    def test_app_index_missing_swapp_fails(self):
        code, out = self._case(lambda d: write(
            d, "apps/demo/index.html",
            page(APP_C, extra_ld=[[crumbs(APP_C, [("Home", HOME),
                                                  ("Demo", None)])]])))
        self.assertNotEqual(code, 0)
        self.assertIn("SoftwareApplication", out)

    def test_blog_index_missing_blog_entity_fails(self):
        code, out = self._case(lambda d: write(
            d, "blog/index.html",
            page(BLOG_C, extra_ld=[[crumbs(BLOG_C, [("Home", HOME),
                                                    ("Blog", None)])]])))
        self.assertNotEqual(code, 0)
        self.assertIn("Blog", out)

    def test_org_block_drift_fails(self):
        drifted = dict(ORG, email="other@example.com")
        code, out = self._case(lambda d: write(
            d, "index.html", page(HOME, org=drifted)))
        self.assertNotEqual(code, 0)
        self.assertIn("differs", out)

    def test_new_page_auto_discovered(self):
        code, out = self._case(lambda d: write(
            d, "newpage.html", "<html><head></head><body>x</body></html>"))
        self.assertNotEqual(code, 0)
        self.assertIn("newpage.html", out)


if __name__ == "__main__":
    unittest.main()

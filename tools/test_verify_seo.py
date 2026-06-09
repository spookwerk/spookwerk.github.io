import subprocess, sys, tempfile, unittest
from pathlib import Path

VERIFY = Path(__file__).parent / "verify-seo.py"

GOOD_JSONLD = """<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","@id":"https://spookwerk.app/#organization",
  "logo":"https://spookwerk.app/logo.png"},
 {"@type":"WebSite","@id":"https://spookwerk.app/#website"}]}
</script>"""

def post(slug, lang, *, canonical=None, drop=None):
    drop = drop or set()
    c = canonical or f"https://spookwerk.app/blog/posts/{lang}/{slug}.html"
    lines = []
    if "canonical" not in drop:
        lines.append(f'<link rel="canonical" href="{c}">')
    if "hreflang" not in drop:
        lines += [
          f'<link rel="alternate" hreflang="en" href="https://spookwerk.app/blog/posts/en/{slug}.html">',
          f'<link rel="alternate" hreflang="nl" href="https://spookwerk.app/blog/posts/nl/{slug}.html">',
          f'<link rel="alternate" hreflang="x-default" href="https://spookwerk.app/blog/posts/en/{slug}.html">',
        ]
    if "og" not in drop:
        lines += [
          '<meta property="og:type" content="article">',
          '<meta property="og:title" content="T">',
          '<meta property="og:description" content="D">',
          f'<meta property="og:url" content="{c}">',
          '<meta property="og:image" content="https://spookwerk.app/og/default.png">',
          '<meta property="og:site_name" content="Spookwerk">',
          '<meta property="article:published_time" content="2026-06-07">',
        ]
    if "tw" not in drop:
        lines += [
          '<meta name="twitter:card" content="summary_large_image">',
          '<meta name="twitter:title" content="T">',
          '<meta name="twitter:description" content="D">',
          '<meta name="twitter:image" content="https://spookwerk.app/og/default.png">',
        ]
    if "jsonld" not in drop:
        lines.append(GOOD_JSONLD)
    head = "\n".join(lines)
    return f"<!DOCTYPE html><html><head>{head}</head><body>x</body></html>"

class T(unittest.TestCase):
    def _run(self, root):
        # Pass explicit fixture paths (not --all): --all targets the production
        # DEFAULT_PAGES set, which doesn't exist in the temp fixture dir.
        r = subprocess.run([sys.executable, str(VERIFY), "--root", str(root),
                            "blog/posts/en/p.html", "blog/posts/nl/p.html"],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def _scaffold(self, d, en_extra_drop=None):
        slug = "p"
        for lang in ("en", "nl"):
            pdir = d / "blog" / "posts" / lang
            pdir.mkdir(parents=True, exist_ok=True)
            drop = en_extra_drop if (lang == "en" and en_extra_drop) else None
            (pdir / f"{slug}.html").write_text(post(slug, lang, drop=drop))
        (d / "logo.png").write_bytes(b"x")
        (d / "og").mkdir(exist_ok=True)
        (d / "og" / "default.png").write_bytes(b"x")

    def test_good_passes(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d)
            code, out = self._run(d)
            self.assertEqual(code, 0, out)

    def test_missing_canonical_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d, en_extra_drop={"canonical"})
            code, out = self._run(d)
            self.assertNotEqual(code, 0)
            self.assertIn("canonical", out)

    def test_missing_jsonld_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d, en_extra_drop={"jsonld"})
            code, out = self._run(d)
            self.assertNotEqual(code, 0)

    def test_missing_og_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d, en_extra_drop={"og"})
            code, out = self._run(d)
            self.assertNotEqual(code, 0)

    def test_multitoken_rel_canonical_recognized(self):
        # HTML rel is a token list: rel="canonical alternate" must still be seen.
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d)
            en = d / "blog" / "posts" / "en" / "p.html"
            en.write_text(en.read_text().replace(
                'rel="canonical"', 'rel="canonical alternate"'))
            code, out = self._run(d)
            self.assertEqual(code, 0, out)

    def test_broken_reciprocity_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for lang in ("en", "nl"):
                pdir = d / "blog" / "posts" / lang
                pdir.mkdir(parents=True, exist_ok=True)
                drop = {"hreflang"} if lang == "nl" else None
                (pdir / "p.html").write_text(post("p", lang, drop=drop))
            (d / "logo.png").write_bytes(b"x")
            (d / "og").mkdir(); (d / "og" / "default.png").write_bytes(b"x")
            code, out = self._run(d)
            self.assertNotEqual(code, 0)
            self.assertIn("reciprocal", out)

    def test_jsonld_missing_org_id_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d)
            en = d / "blog" / "posts" / "en" / "p.html"
            bad = ('<script type="application/ld+json">'
                   '{"@context":"https://schema.org","@graph":['
                   '{"@type":"WebSite","@id":"https://spookwerk.app/#website"}]}'
                   '</script>')
            en.write_text(en.read_text().replace(GOOD_JSONLD, bad))
            code, out = self._run(d)
            self.assertNotEqual(code, 0)
            self.assertIn("Organization", out)

    def test_missing_asset_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._scaffold(d)
            (d / "og" / "default.png").unlink()
            code, out = self._run(d)
            self.assertNotEqual(code, 0)
            self.assertIn("og:image", out)

if __name__ == "__main__":
    unittest.main()

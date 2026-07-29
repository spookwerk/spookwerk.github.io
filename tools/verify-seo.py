#!/usr/bin/env python3
"""Head-kit + per-page-type JSON-LD verifier for spookwerk.app. Stdlib only.
Usage: python3 tools/verify-seo.py [--root DIR] [--all] [--write-sitemap] [FILE ...]
Pages are auto-discovered (every *.html under root, minus meta-refresh
redirect stubs and robots-noindex stubs); pass explicit FILEs to scope.
--all is accepted for back-compat.
Exit 0 = all checked pages pass; non-zero = failures (printed)."""
import argparse, json, re, sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from html.parser import HTMLParser
from pathlib import Path

SKIP_DIRS = {"tools"}  # non-site dirs: anything here is not a deployed page

SITE = "https://spookwerk.app"
ORG_ID = f"{SITE}/#organization"
SITE_ID = f"{SITE}/#website"
BLOG_ID = f"{SITE}/blog/#blog"

OG_REQUIRED = ["og:type", "og:title", "og:description", "og:url",
               "og:image", "og:site_name"]
TW_REQUIRED = ["twitter:card", "twitter:title", "twitter:description",
               "twitter:image"]

APP_INDEX_RE = re.compile(r"^apps/[^/]+/index\.html$")
APP_SUBPAGE_RE = re.compile(r"^apps/[^/]+/(privacy|support)/(nl/)?index\.html$")


def twin_of(relpath: str):
    """The NL<->EN counterpart path for bilingual page families, else None.

    App *index* pages (apps/<slug>/index.html) are single-locale by design
    and deliberately have no twin mapping."""
    if relpath == "blog/index.html":
        return "blog/nl/index.html"
    if relpath == "blog/nl/index.html":
        return "blog/index.html"
    m = re.match(r"^(apps/[^/]+/(?:privacy|support)/)index\.html$", relpath)
    if m:
        return m.group(1) + "nl/index.html"
    m = re.match(r"^(apps/[^/]+/(?:privacy|support)/)nl/index\.html$", relpath)
    if m:
        return m.group(1) + "index.html"
    m = re.match(r"^blog/posts/en/(.+)$", relpath)
    if m:
        return "blog/posts/nl/" + m.group(1)
    m = re.match(r"^blog/posts/nl/(.+)$", relpath)
    if m:
        return "blog/posts/en/" + m.group(1)
    return None


def twin_is_indexable(root: Path, twin: str, parsed_set: dict) -> bool:
    if twin in parsed_set:
        return True
    f = root / twin
    if not f.exists():
        return False
    # scoped runs: twin may exist but not be in parsed_set — parse ad hoc
    return "noindex" not in parse_file(f).robots.lower()


def page_type(relpath: str) -> str:
    if relpath == "index.html":
        return "landing"
    if relpath in ("blog/index.html", "blog/nl/index.html"):
        return "blog-index"
    if relpath.startswith("blog/posts/"):
        return "blog-post"
    if APP_INDEX_RE.match(relpath):
        return "app-index"
    if APP_SUBPAGE_RE.match(relpath):
        return "app-subpage"
    return "base"


class Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = None
        self.alts = []          # (hreflang, href)
        self.og = {}            # og:*/article:* -> content
        self.tw = {}            # twitter:* -> content
        self.description = None
        self.robots = ""
        self.refresh = None     # meta http-equiv="refresh" content, if any
        self.ld_raw = []
        self._in_ld = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link":
            rels = (a.get("rel") or "").lower().split()  # rel is a token list
            if "canonical" in rels:
                self.canonical = a.get("href")
            elif "alternate" in rels and a.get("hreflang"):
                self.alts.append((a["hreflang"], a.get("href")))
        elif tag == "meta":
            if (a.get("http-equiv") or "").lower() == "refresh":
                self.refresh = a.get("content", "")
            prop = a.get("property", "")
            if prop.startswith("og:") or prop.startswith("article:"):
                self.og[prop] = a.get("content", "")
            name = a.get("name", "")
            if name.startswith("twitter:"):
                self.tw[name] = a.get("content", "")
            elif name == "description":
                self.description = a.get("content", "")
            elif name == "robots":
                self.robots = a.get("content", "")
        elif tag == "script" and (a.get("type") == "application/ld+json"):
            self._in_ld = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_raw.append("".join(self._buf).strip())

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def parse_file(f: Path) -> Head:
    h = Head()
    # errors="replace": a stray non-UTF-8 file must not abort the whole run
    h.feed(f.read_text(encoding="utf-8", errors="replace"))
    return h


def expected_canonical(relpath: str) -> str:
    if relpath.endswith("index.html"):
        return f"{SITE}/{relpath[:-len('index.html')]}"
    return f"{SITE}/{relpath}"


def href_to_relpath(href: str):
    pre = SITE + "/"
    if not href.startswith(pre):
        return None
    rest = href[len(pre):]
    if rest == "" or rest.endswith("/"):
        return rest + "index.html"
    return rest


def ld_nodes(raw_blocks):
    nodes = []
    for raw in raw_blocks:
        data = json.loads(raw)  # raises -> caught by caller
        if isinstance(data, dict) and "@graph" in data:
            nodes.extend(data["@graph"])
        elif isinstance(data, list):
            nodes.extend(data)
        else:
            nodes.append(data)
    return nodes


def find_nodes(nodes, t):
    return [n for n in nodes if isinstance(n, dict) and n.get("@type") == t]


def ref_id(v):
    return v.get("@id") if isinstance(v, dict) else None


def contains_person(obj) -> bool:
    if isinstance(obj, dict):
        if obj.get("@type") == "Person":
            return True
        return any(contains_person(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_person(v) for v in obj)
    return False


def check_breadcrumbs(root, nodes, exp, errs):
    bcs = find_nodes(nodes, "BreadcrumbList")
    if not bcs:
        errs.append("missing BreadcrumbList")
        return
    if len(bcs) > 1:
        errs.append("multiple BreadcrumbList nodes")
    expected_id = exp + "#breadcrumbs"
    if bcs[0].get("@id") != expected_id:
        errs.append(f"BreadcrumbList @id {bcs[0].get('@id')!r} != {expected_id!r}")
    items = bcs[0].get("itemListElement", [])
    if not items:
        errs.append("BreadcrumbList has no items")
        return
    if items[0].get("name") != "Home" or items[0].get("item") != SITE + "/":
        errs.append("breadcrumb must start at Home (https://spookwerk.app/)")
    for i, it in enumerate(items, 1):
        if it.get("position") != i:
            errs.append(f"breadcrumb position {it.get('position')!r} != {i}")
        url = it.get("item")
        last = (i == len(items))
        if url is None:
            if not last:
                errs.append(f"breadcrumb item {i} missing 'item' URL")
        else:
            rp = href_to_relpath(url)
            if rp is None or not (root / rp).exists():
                errs.append(f"breadcrumb item URL missing: {url}")
            if last and url != exp:
                errs.append(f"breadcrumb last item {url!r} != canonical")


def check_page(root: Path, relpath: str, parsed_set: dict) -> list:
    errs = []
    h = parsed_set[relpath]
    exp = expected_canonical(relpath)
    ptype = page_type(relpath)

    # 1. canonical
    if not h.canonical:
        errs.append("missing canonical")
    elif h.canonical != exp:
        errs.append(f"canonical {h.canonical!r} != expected {exp!r}")

    # 1b. meta description (also feeds og:description)
    if not h.description:
        errs.append("missing meta description")

    # 2. hreflang — only checked when the page declares alternates. Single-
    # locale pages legitimately have none; bilingual twins must declare them.
    if h.alts:
        hrefs = {hl: hr for hl, hr in h.alts}
        if "x-default" not in hrefs:
            errs.append("hreflang missing x-default")
        for hl, hr in h.alts:
            rp = href_to_relpath(hr)
            if rp is None or not (root / rp).exists():
                errs.append(f"hreflang {hl} target missing: {hr}")
        if exp not in hrefs.values():
            errs.append(f"hreflang does not list self ({exp})")
        for hl, hr in h.alts:
            if hl == "x-default":
                continue
            rp = href_to_relpath(hr)
            if rp and rp != relpath and rp in parsed_set:
                back = {v for _, v in parsed_set[rp].alts}
                if exp not in back:
                    errs.append(f"hreflang not reciprocal with {rp}")

    # 2b. hreflang twin: if the NL/EN counterpart exists and is indexable,
    # hreflang is mandatory and must list the twin (closes B's silent pass).
    twin = twin_of(relpath)
    if twin and twin_is_indexable(root, twin, parsed_set):
        twin_url = expected_canonical(twin)
        if not h.alts:
            errs.append(f"twin exists ({twin}) but no hreflang declared")
        elif twin_url not in {hr for _, hr in h.alts}:
            errs.append(f"hreflang does not list twin {twin_url}")

    # 3. Open Graph
    for k in OG_REQUIRED:
        if k not in h.og or not h.og[k]:
            errs.append(f"missing {k}")
    if h.og.get("og:url") and h.canonical and h.og["og:url"] != h.canonical:
        errs.append("og:url != canonical")
    if h.og.get("og:type") == "article" and not h.og.get("article:published_time"):
        errs.append("article missing article:published_time")

    # 4. Twitter
    for k in TW_REQUIRED:
        if k not in h.tw or not h.tw[k]:
            errs.append(f"missing {k}")

    # 5. JSON-LD: sitewide spine + per-type assertions
    nodes = []
    if not h.ld_raw:
        errs.append("missing JSON-LD")
    else:
        try:
            nodes = ld_nodes(h.ld_raw)
        except json.JSONDecodeError as e:
            errs.append(f"invalid JSON-LD: {e}")
            nodes = []
        ids = {n.get("@id") for n in nodes if isinstance(n, dict)}
        if ORG_ID not in ids:
            errs.append(f"JSON-LD missing Organization @id {ORG_ID}")
        if SITE_ID not in ids:
            errs.append(f"JSON-LD missing WebSite @id {SITE_ID}")
        if contains_person(nodes):
            errs.append("Person found in JSON-LD (name-privacy violation)")
        for n in nodes:
            if isinstance(n, dict) and n.get("@id") == ORG_ID:
                logo = n.get("logo")
                rp = href_to_relpath(logo) if logo else None
                if rp and not (root / rp).exists():
                    errs.append(f"logo asset missing: {logo}")

    # 5b. per-type assertions
    if ptype != "landing":
        check_breadcrumbs(root, nodes, exp, errs)
    if ptype == "blog-post":
        bp = find_nodes(nodes, "BlogPosting")
        if not bp:
            errs.append("missing BlogPosting")
        else:
            if len(bp) > 1:
                errs.append("multiple BlogPosting nodes")
            b = bp[0]
            for k in ("headline", "datePublished", "inLanguage", "description"):
                if not b.get(k):
                    errs.append(f"BlogPosting missing {k}")
            if b.get("mainEntityOfPage") != exp:
                errs.append("BlogPosting mainEntityOfPage != canonical")
            if ref_id(b.get("author")) != ORG_ID:
                errs.append("BlogPosting author must reference #organization")
            if ref_id(b.get("publisher")) != ORG_ID:
                errs.append("BlogPosting publisher must reference #organization")
    elif ptype == "app-index":
        sa = find_nodes(nodes, "SoftwareApplication")
        if not sa:
            errs.append("missing SoftwareApplication")
        else:
            if len(sa) > 1:
                errs.append("multiple SoftwareApplication nodes")
            s = sa[0]
            for k in ("name", "description", "operatingSystem",
                      "applicationCategory", "offers"):
                if not s.get(k):
                    errs.append(f"SoftwareApplication missing {k}")
            if ref_id(s.get("publisher")) != ORG_ID:
                errs.append("SoftwareApplication publisher must reference #organization")
    elif ptype == "blog-index":
        bl = find_nodes(nodes, "Blog")
        if not bl:
            errs.append("missing Blog entity")
        else:
            if len(bl) > 1:
                errs.append("multiple Blog nodes")
            if bl[0].get("@id") != BLOG_ID:
                errs.append(f"Blog @id != {BLOG_ID}")
            if ref_id(bl[0].get("publisher")) != ORG_ID:
                errs.append("Blog publisher must reference #organization")

    # 6. asset existence
    img = h.og.get("og:image")
    rp = href_to_relpath(img) if img else None
    if rp and not (root / rp).exists():
        errs.append(f"og:image asset missing: {img}")

    return errs


def sitewide_block(h: Head):
    """Return the parsed first ld+json block, or None if missing/invalid."""
    if not h.ld_raw:
        return None
    try:
        return json.loads(h.ld_raw[0])
    except (json.JSONDecodeError, TypeError):
        return None


def discover(root: Path) -> dict:
    """Every deployable *.html under root, minus SKIP_DIRS, redirect stubs and
    noindex stubs."""
    parsed = {}
    for f in sorted(root.rglob("*.html")):
        rel = f.relative_to(root).as_posix()
        # non-site dirs: anything here is not a deployed page
        if rel.split("/")[0] in SKIP_DIRS:
            continue
        h = parse_file(f)
        # A meta-refresh page is a redirect stub for a moved URL, not a page:
        # never head-kit-verified, never in the sitemap. Detected on the
        # refresh itself so the stub does NOT need noindex — noindex would
        # tell Google to drop the old URL, fighting the canonical that asks it
        # to consolidate into the new one.
        if h.refresh is not None:
            continue
        # substring match on meta name="robots" only; name="googlebot" noindex is not detected
        if "noindex" in h.robots.lower():
            continue
        parsed[rel] = h
    return parsed


def sitemap_xml(relpaths) -> str:
    """Deterministic <loc>-only sitemap: sorted URLs, byte-stable output."""
    body = "\n".join(f"  <url><loc>{escape(expected_canonical(r))}</loc></url>"
                     for r in sorted(relpaths,
                                     key=lambda r: expected_canonical(r)))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + body + "\n</urlset>\n")


SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def check_site_files(root: Path, parsed: dict) -> dict:
    """Site-level checks, discovery mode only. Returns {filename: [errors]}."""
    errs = {}
    sm = root / "sitemap.xml"
    expected = {expected_canonical(r) for r in parsed}
    if not sm.exists():
        errs["sitemap.xml"] = [
            "missing — generate with: tools/verify-seo.py --write-sitemap"]
    else:
        try:
            got = {(el.text or "").strip()
                   for el in ET.parse(sm).findall(".//sm:loc", SITEMAP_NS)}
            e = []
            for url in sorted(got - expected):
                e.append(f"sitemap lists URL with no page: {url}")
            for url in sorted(expected - got):
                e.append(f"page missing from sitemap: {url} — regenerate")
            if e:
                errs["sitemap.xml"] = e
        except ET.ParseError as ex:
            errs["sitemap.xml"] = [f"unparseable sitemap: {ex}"]
    rb = root / "robots.txt"
    if not rb.exists():
        errs["robots.txt"] = ["missing"]
    else:
        e = []
        lines = rb.read_text(encoding="utf-8").splitlines()
        if f"Sitemap: {SITE}/sitemap.xml" not in lines:
            e.append(f"missing line: Sitemap: {SITE}/sitemap.xml")
        for ln in lines:
            if ln.strip().lower().startswith("disallow:"):
                e.append(f"Disallow directive present: {ln.strip()!r} "
                         "(site policy is allow-all; see spec C §4)")
        if e:
            errs["robots.txt"] = e
    lm = root / "llms.txt"
    if not lm.exists():
        errs["llms.txt"] = ["missing"]
    else:
        e = []
        text = lm.read_text(encoding="utf-8")
        for url in re.findall(r"https://spookwerk\.app/[^\s)\"'>\]]*", text):
            # bare URLs in prose: strip fragment/query, then trailing punctuation
            url = re.split(r"[#?]", url)[0].rstrip(".,;:")
            rp = href_to_relpath(url)
            if rp and not (root / rp).exists():
                e.append(f"dead link: {url}")
        if e:
            errs["llms.txt"] = e
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true",
                    help="deprecated no-op; discovery is the default")
    ap.add_argument("--write-sitemap", action="store_true",
                    help="(re)generate sitemap.xml from discovered pages, then exit")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    parsed = {}
    missing = []
    if args.files:
        for rel in args.files:
            f = root / rel
            if not f.exists():
                missing.append(rel)
                continue
            parsed[rel] = parse_file(f)
    else:
        parsed = discover(root)

    if args.write_sitemap:
        if args.files:
            print("--write-sitemap cannot be combined with FILE args")
            sys.exit(2)
        (root / "sitemap.xml").write_text(sitemap_xml(parsed), encoding="utf-8")
        print(f"sitemap.xml written ({len(parsed)} URLs)")
        sys.exit(0)

    failures = {}
    for rel in parsed:
        e = check_page(root, rel, parsed)
        if e:
            failures[rel] = e
    for rel in missing:
        failures[rel] = ["file not found"]

    # site-level checks only make sense against the full discovered set
    if not args.files:
        for name, e in check_site_files(root, parsed).items():
            failures.setdefault(name, []).extend(e)

    # sitewide JSON-LD block (full first block) must be identical on every page
    sitewide_blocks = {rel: b for rel in parsed
                       if (b := sitewide_block(parsed[rel])) is not None}
    if sitewide_blocks:
        first_rel = next(iter(sitewide_blocks))
        ref = sitewide_blocks[first_rel]
        for rel, b in sitewide_blocks.items():
            if b != ref:
                failures.setdefault(rel, []).append(
                    f"sitewide JSON-LD block differs from {first_rel}")

    if failures:
        for rel, errs in failures.items():
            print(f"FAIL {rel}")
            for e in errs:
                print(f"     - {e}")
        print(f"\n{len(failures)} page(s) failed.")
        sys.exit(1)
    print(f"OK: {len(parsed)} page(s) passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()

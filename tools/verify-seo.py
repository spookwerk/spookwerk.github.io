#!/usr/bin/env python3
"""Head-kit + per-page-type JSON-LD verifier for spookwerk.app. Stdlib only.
Usage: python3 tools/verify-seo.py [--root DIR] [--all] [FILE ...]
Pages are auto-discovered (every *.html under root, minus robots-noindex
stubs); pass explicit FILEs to scope. --all is accepted for back-compat.
Exit 0 = all checked pages pass; non-zero = failures (printed)."""
import argparse, json, re, sys
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


def org_node(h: Head):
    try:
        for n in ld_nodes(h.ld_raw):
            if isinstance(n, dict) and n.get("@id") == ORG_ID:
                return n
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true",
                    help="deprecated no-op; discovery is the default")
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
        # discovery: every *.html under root, minus robots-noindex stubs
        for f in sorted(root.rglob("*.html")):
            rel = f.relative_to(root).as_posix()
            # non-site dirs: anything here is not a deployed page
            if rel.split("/")[0] in SKIP_DIRS:
                continue
            # substring match on meta name="robots" only; name="googlebot" noindex is not detected
            h = parse_file(f)
            if "noindex" in h.robots.lower():
                continue
            parsed[rel] = h

    failures = {}
    for rel in parsed:
        e = check_page(root, rel, parsed)
        if e:
            failures[rel] = e
    for rel in missing:
        failures[rel] = ["file not found"]

    # sitewide Organization block must be identical (parsed) on every page
    orgs = {rel: n for rel in parsed if (n := org_node(parsed[rel]))}
    if orgs:
        first_rel = next(iter(orgs))
        ref = orgs[first_rel]
        for rel, n in orgs.items():
            if n != ref:
                failures.setdefault(rel, []).append(
                    f"Organization block differs from {first_rel}")

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

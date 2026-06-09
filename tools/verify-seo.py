#!/usr/bin/env python3
"""Head-kit verifier for spookwerk.app. Stdlib only.
Usage: python3 tools/verify-seo.py [--root DIR] [--all | FILE ...]
Exit 0 = all checked pages pass; non-zero = failures (printed)."""
import argparse, json, sys
from html.parser import HTMLParser
from pathlib import Path

SITE = "https://spookwerk.app"
ORG_ID = f"{SITE}/#organization"
SITE_ID = f"{SITE}/#website"

DEFAULT_PAGES = [
    "blog/index.html",
    "blog/nl/index.html",
    "blog/posts/en/why-i-built-an-apple-health-csv-exporter.html",
    "blog/posts/nl/why-i-built-an-apple-health-csv-exporter.html",
]

OG_REQUIRED = ["og:type", "og:title", "og:description", "og:url",
               "og:image", "og:site_name"]
TW_REQUIRED = ["twitter:card", "twitter:title", "twitter:description",
               "twitter:image"]


class Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = None
        self.alts = []          # (hreflang, href)
        self.og = {}            # og:*/article:* -> content
        self.tw = {}            # twitter:* -> content
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


def check_page(root: Path, relpath: str, parsed_set: dict) -> list:
    errs = []
    h = parsed_set[relpath]
    exp = expected_canonical(relpath)

    # 1. canonical
    if not h.canonical:
        errs.append("missing canonical")
    elif h.canonical != exp:
        errs.append(f"canonical {h.canonical!r} != expected {exp!r}")

    # 2. hreflang — only checked when the page declares alternates. Single-locale
    # pages legitimately have none; bilingual twins must declare them. (Twin
    # auto-detection is a known gap, deferred — see plan §14 / spec follow-ups.)
    if h.alts:
        hrefs = {hl: hr for hl, hr in h.alts}
        if "x-default" not in hrefs:
            errs.append("hreflang missing x-default")
        # every alternate target exists locally
        for hl, hr in h.alts:
            rp = href_to_relpath(hr)
            if rp is None or not (root / rp).exists():
                errs.append(f"hreflang {hl} target missing: {hr}")
        # self listed
        if exp not in hrefs.values():
            errs.append(f"hreflang does not list self ({exp})")
        # reciprocity: each non-self locale target lists this page back
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

    # 5. JSON-LD
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
        # 6. referenced assets exist
        for n in nodes:
            if isinstance(n, dict) and n.get("@id") == ORG_ID:
                logo = n.get("logo")
                rp = href_to_relpath(logo) if logo else None
                if rp and not (root / rp).exists():
                    errs.append(f"logo asset missing: {logo}")
    img = h.og.get("og:image")
    rp = href_to_relpath(img) if img else None
    if rp and not (root / rp).exists():
        errs.append(f"og:image asset missing: {img}")

    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true",
                    help="check the default page set")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    # --all (or no args) checks the curated DEFAULT_PAGES set — the pages that
    # are supposed to carry the head-kit. The set grows as sub-projects retrofit
    # more pages (E). Explicit file args override it; the two are exclusive.
    if args.files and args.all:
        ap.error("pass either --all or explicit files, not both")
    rels = list(args.files) if args.files else DEFAULT_PAGES

    parsed = {}
    missing = []
    for rel in rels:
        f = root / rel
        if not f.exists():
            missing.append(rel)
            continue
        h = Head()
        h.feed(f.read_text(encoding="utf-8"))
        parsed[rel] = h

    failures = {}
    for rel in parsed:
        e = check_page(root, rel, parsed)
        if e:
            failures[rel] = e
    for rel in missing:
        failures[rel] = ["file not found"]

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

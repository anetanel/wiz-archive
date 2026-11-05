#!/usr/bin/env python3
"""
Assemble the publishable site in ./deploy (site + PDFs) and check it against
Cloudflare Pages free-plan limits. Files are hard-linked, not copied, so this
takes almost no extra disk space.

    python3 deploy.py           # build ./deploy and print a report
"""
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
OUT = os.path.join(ROOT, "deploy")
MAX_FILE = 25 * 1024 * 1024      # Cloudflare Pages: 25 MiB per file
MAX_FILES = 20000                # Cloudflare Pages free plan

HEADERS = """/img/*
  Cache-Control: public, max-age=31536000, immutable
/pdfs/*
  Cache-Control: public, max-age=31536000, immutable
"""


def link(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main():
    issues_path = os.path.join(ROOT, "work", "issues.json")
    if not os.path.exists(issues_path) or not os.path.exists(os.path.join(SITE, "index.html")):
        sys.exit("Build the site first (run.command).")
    with open(issues_path, encoding="utf-8") as fh:
        issues = json.load(fh)

    shutil.rmtree(OUT, ignore_errors=True)
    files, too_big = 0, []

    # 1. the site, except the issue list (rewritten below); HTML pages get the
    #    site's full address for social-media previews (og:image must be absolute)
    site_url = os.environ.get("SITE_URL", "").rstrip("/") or "https://wiz-archive.pages.dev"
    for dirpath, _, names in os.walk(SITE):
        for n in names:
            if n.startswith(".") or (n == "issues.js" and dirpath.endswith("data")):
                continue
            src = os.path.join(dirpath, n)
            dst = os.path.join(OUT, os.path.relpath(src, SITE))
            if n.endswith(".html"):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src, encoding="utf-8") as fh:
                    html = fh.read().replace("__SITE_URL__", site_url)
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(html)
            else:
                link(src, dst)
            files += 1

    # 2. the PDFs, now inside the site
    for i in issues:
        src = os.path.normpath(os.path.join(SITE, i["pdf"]))
        if not os.path.exists(src):
            i.pop("pdf", None)
            continue
        if os.path.getsize(src) > MAX_FILE:
            too_big.append((i["num"], os.path.getsize(src)))
            i.pop("pdf", None)          # the site hides the PDF button
            continue
        name = "wiz-%03d.pdf" % i["num"]
        link(src, os.path.join(OUT, "pdfs", name))
        i["pdf"] = "pdfs/" + name
        files += 1

    with open(os.path.join(OUT, "data", "issues.js"), "w", encoding="utf-8") as fh:
        fh.write("WIZ_ISSUES=")
        json.dump(issues, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    with open(os.path.join(OUT, "_headers"), "w") as fh:
        fh.write(HEADERS)
    files += 2

    big_other = []
    total = 0
    for dirpath, _, names in os.walk(OUT):
        for n in names:
            p = os.path.join(dirpath, n)
            total += os.path.getsize(p)
            if os.path.getsize(p) > MAX_FILE:
                big_other.append(os.path.relpath(p, OUT))

    print(f"site address: {site_url}")
    print(f"deploy/: {files:,} files, {total / 1024**3:.2f} GB")
    ok = True
    if files > MAX_FILES:
        print(f"!! More than {MAX_FILES:,} files – over the Cloudflare Pages free limit.")
        ok = False
    if big_other:
        print("!! Files over 25 MB:", ", ".join(big_other))
        ok = False
    for num, size in too_big:
        print(f"  note: wiz-{num:03d}.pdf is {size / 1024**2:.0f} MB (over 25 MB) – left out, "
              f"its PDF button is hidden.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

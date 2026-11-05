#!/usr/bin/env python3
"""
WIZ archive builder.

Takes the scanned PDFs (wiz-XXX.pdf), splits the double-page spreads into
single magazine pages, runs Hebrew+English OCR on each page, and builds the
data files for the local website in ./site.

Usage:
    python3 build.py                 # process everything in ./pdfs
    python3 build.py --pdfs /path    # PDFs somewhere else
    python3 build.py --only 1 10 50  # only these issues
    python3 build.py --jobs 6        # number of parallel OCR processes

Safe to stop and re-run: finished pages are cached in ./work and skipped.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "work")
SITE = os.path.join(ROOT, "site")
TOC_DIR = os.path.join(ROOT, "toc")

PAGE_MAX_H = 1500        # height of page images shown in the reader
PAGE_QUALITY = 78
THUMB_H = 260
OCR_LANGS = "heb+eng"

HEB_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
              "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
MONTH_ALIASES = {"מרס": "מרץ", "מארס": "מרץ"}

BIDI_JUNK = re.compile("[‎‏‪-‮⁦-⁩﻿]")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- layout ---

def extract_raw(pdf, issue):
    """Extract the embedded scan image of every PDF page (lossless)."""
    out = os.path.join(WORK, "raw", issue)
    done = os.path.join(out, ".done")
    if os.path.exists(done):
        return out
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    subprocess.run(["pdfimages", "-j", pdf, os.path.join(out, "img")], check=True)
    # Sanity check: one image per PDF page. Otherwise fall back to rendering.
    n_pages = int(re.search(r"Pages:\s+(\d+)",
                            subprocess.run(["pdfinfo", pdf], capture_output=True,
                                           text=True).stdout).group(1))
    imgs = sorted(f for f in os.listdir(out) if f.startswith("img"))
    if len(imgs) != n_pages:
        log(f"  [{issue}] {len(imgs)} images for {n_pages} pages – rendering pages instead")
        shutil.rmtree(out)
        os.makedirs(out)
        subprocess.run(["pdftoppm", "-r", "150", "-jpeg", "-jpegopt", "quality=92",
                        pdf, os.path.join(out, "img")], check=True)
    open(done, "w").close()
    return out


def pdf_page_sizes(pdf):
    """Size of the scan on every PDF page, read from the PDF without extracting
    anything. None if the PDF is not one image per page."""
    out = subprocess.run(["pdfimages", "-list", pdf], capture_output=True, text=True).stdout
    sizes = {}
    for line in out.splitlines()[2:]:
        c = line.split()
        if len(c) < 5 or c[2] not in ("image", "smask", "stencil"):
            continue
        if c[2] != "image":
            continue
        page = int(c[0])
        if page in sizes:
            return None
        sizes[page] = (int(c[3]), int(c[4]))
    n_pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(
        ["pdfinfo", pdf], capture_output=True, text=True).stdout).group(1))
    if sorted(sizes) != list(range(1, n_pages + 1)):
        return None
    return [sizes[i] for i in range(1, n_pages + 1)]


def raw_sizes(raw_dir):
    sizes = []
    for f in sorted(f for f in os.listdir(raw_dir) if f.startswith("img")):
        with Image.open(os.path.join(raw_dir, f)) as im:
            sizes.append(im.size)
    return sizes


def plan_pages(sizes):
    """Map PDF pages to magazine pages.

    Scans are: single cover, then two-page spreads (right half = even page,
    Hebrew order), a single back cover. A much smaller trailing image is a
    scanner credit page and is skipped. A portrait page in the middle that is
    much taller than the spreads is a centrefold poster scanned sideways: it
    is shown whole and takes up two page numbers (the second one is skipped).
    """
    heights = sorted(h for _, h in sizes)
    median_h = heights[len(heights) // 2]
    pages, n = [], 1
    last = max(i for i, (w, h) in enumerate(sizes) if h >= 0.6 * median_h)
    for idx, (w, h) in enumerate(sizes):
        if h < 0.6 * median_h:
            continue  # credit / insert page that isn't part of the magazine
        if w / h > 1.25:
            pages.append({"n": n, "pdf": idx + 1, "half": "R"})
            pages.append({"n": n + 1, "pdf": idx + 1, "half": "L"})
            n += 2
        elif 0 < idx < last and h > 1.3 * median_h:
            pages.append({"n": n, "pdf": idx + 1, "half": None, "span": 2})
            n += 2
        else:
            pages.append({"n": n, "pdf": idx + 1, "half": None})
            n += 1
    return pages


def load_swaps(issue):
    """Spreads scanned with their halves reversed, from toc/NNN.json:
    "swap": [22, 24, "30-40"] – each number is the lower (even) page of a
    spread; a range covers every spread whose lower page lies in it."""
    toc = load_toc(issue) or {}
    out = set()
    for s in toc.get("swap", []):
        if isinstance(s, int):
            out.add(s)
        else:
            a, _, b = str(s).partition("-")
            out.update(range(int(a), int(b or a) + 1))
    return out


def apply_swaps(plan, swaps):
    """Return a copy of the plan with the halves of the listed spreads flipped."""
    plan = [dict(p) for p in plan]
    by_pdf = {}
    for p in plan:
        if p["half"]:
            by_pdf.setdefault(p["pdf"], []).append(p)
    for pair in by_pdf.values():
        if len(pair) != 2:
            continue
        lo = min(p["n"] for p in pair)
        want_swapped = lo in swaps
        for p in pair:
            # normal: lower page in the right half (Hebrew order)
            normal = "R" if p["n"] == lo else "L"
            p["half"] = ("L" if normal == "R" else "R") if want_swapped else normal
    return plan


def forget_page(issue, n):
    """Delete cached OCR and images of a page so it is rebuilt."""
    base = os.path.join(WORK, "ocr", issue, "%03d" % n)
    for ext in (".txt", ".tsv", ".size", ".toc.tsv", ".toc.size"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    for name in ("%03d.jpg" % n, "t%03d.jpg" % n):
        f = os.path.join(SITE, "img", issue, name)
        if os.path.exists(f):
            os.remove(f)


def make_brand(issues):
    """Favicons and the social-media preview image (og.jpg) from assets/logo.png
    and the issue covers."""
    from PIL import ImageDraw
    logo_path = os.path.join(ROOT, "assets", "logo.png")
    if not os.path.exists(logo_path):
        return
    logo = Image.open(logo_path).convert("RGBA")
    dark = (29, 23, 51, 255)

    def icon(size, radius=0.22):
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(im).rounded_rectangle((0, 0, size - 1, size - 1), round(size * radius), fill=dark)
        lw = round(size * 0.9)
        l = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
        im.alpha_composite(l, ((size - l.width) // 2, (size - l.height) // 2))
        return im

    icon(256).save(os.path.join(SITE, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    icon(32).save(os.path.join(SITE, "favicon-32.png"))
    icon(192).save(os.path.join(SITE, "icon-192.png"))
    icon(512).save(os.path.join(SITE, "icon-512.png"))
    icon(180, 0).convert("RGB").save(os.path.join(SITE, "apple-touch-icon.png"))
    logo.save(os.path.join(SITE, "logo.png"))
    hh = 92                                     # header logo, 2x for sharp screens
    logo.resize((round(logo.width * hh / logo.height), hh), Image.LANCZOS).save(
        os.path.join(SITE, "logo-header.png"), optimize=True)

    # 1200x630 preview: a fan of covers from across the years, logo on top
    W, H = 1200, 630
    og = Image.new("RGB", (W, H), dark[:3])
    covers = [i for i in issues if os.path.exists(os.path.join(SITE, "img", i["id"], "001.jpg"))]
    if covers:
        k = min(7, len(covers))
        pick = [covers[round(j * (len(covers) - 1) / max(1, k - 1))] for j in range(k)]
        ch = 420
        step = (W - 80 - round(ch * 0.77)) / max(1, k - 1)
        for j, iss in enumerate(pick):
            c = Image.open(os.path.join(SITE, "img", iss["id"], "001.jpg")).convert("RGB")
            c = c.resize((round(c.width * ch / c.height), ch), Image.LANCZOS)
            shadow = Image.new("RGBA", (c.width + 24, c.height + 24), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rectangle((12, 12, c.width + 12, c.height + 12), fill=(0, 0, 0, 120))
            angle = (j - (k - 1) / 2) * -3
            card = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
            card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
            card.paste(c, (6, 6))
            card = card.rotate(angle, expand=True, resample=Image.BICUBIC)
            x = round(40 + step * j)
            y = H - ch + 10 - abs(j - (k - 1) / 2) * 12
            og.paste(card, (x, round(y)), card)
    band = Image.new("RGBA", (W, 230), dark[:3] + (235,))
    og.paste(band, (0, 0), band)
    lw = 430
    l = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
    og.paste(l, ((W - lw) // 2, 8), l)
    og.save(os.path.join(SITE, "og.jpg"), quality=86)


def crop(raw_dir, p):
    files = sorted(f for f in os.listdir(raw_dir) if f.startswith("img"))
    im = Image.open(os.path.join(raw_dir, files[p["pdf"] - 1])).convert("RGB")
    w, h = im.size
    if p["half"] == "R":
        im = im.crop((w // 2, 0, w, h))
    elif p["half"] == "L":
        im = im.crop((0, 0, w // 2, h))
    return im


# ------------------------------------------------------------------- OCR ---

def ocr_page(args):
    raw_dir, p, issue = args
    out_dir = os.path.join(WORK, "ocr", issue)
    base = os.path.join(out_dir, "%03d" % p["n"])
    img_dir = os.path.join(SITE, "img", issue)
    big = os.path.join(img_dir, "%03d.jpg" % p["n"])
    thumb = os.path.join(img_dir, "t%03d.jpg" % p["n"])
    need_ocr = not (os.path.exists(base + ".txt") and os.path.exists(base + ".tsv"))
    need_toc = p["n"] == 3 and not os.path.exists(base + ".toc.tsv")
    need_img = not (os.path.exists(big) and os.path.exists(thumb))
    if not need_ocr and not need_img and not need_toc:
        return issue, p["n"], "cached"
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)
    im = crop(raw_dir, p)
    if need_img:
        r = im.copy()
        if r.height > PAGE_MAX_H:
            r = r.resize((round(r.width * PAGE_MAX_H / r.height), PAGE_MAX_H), Image.LANCZOS)
        r.save(big, quality=PAGE_QUALITY, optimize=True, progressive=True)
        r.thumbnail((THUMB_H * 2, THUMB_H), Image.LANCZOS)
        r.save(thumb, quality=80, optimize=True)
    if need_toc:
        ocr_toc_page(im, base)
    if need_ocr:
        # Low-res scans: upscale small pages so tesseract sees bigger glyphs.
        scale = 1.0
        if im.height < 1600:
            scale = 1600 / im.height
            im = im.resize((round(im.width * scale), 1600), Image.LANCZOS)
        tmp = base + ".png"
        im.save(tmp)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        subprocess.run(["tesseract", tmp, base, "-l", OCR_LANGS, "--psm", "3", "txt", "tsv"],
                       check=True, capture_output=True, env=env)
        os.remove(tmp)
        with open(base + ".size", "w") as fh:
            fh.write("%d %d" % im.size)
    return issue, p["n"], "done"


def all_done(issue, pages):
    """True when OCR and page images exist for every page of the issue."""
    for p in pages:
        base = os.path.join(WORK, "ocr", issue, "%03d" % p["n"])
        need = [base + ".txt", base + ".tsv", base + ".size",
                os.path.join(SITE, "img", issue, "%03d.jpg" % p["n"]),
                os.path.join(SITE, "img", issue, "t%03d.jpg" % p["n"])]
        if p["n"] == 3:
            need.append(base + ".toc.tsv")
        if not all(os.path.exists(f) for f in need):
            return False
    return True


def ocr_toc_page(im, base):
    """Extra pass for the table-of-contents page: 2x upscale + sparse-text
    mode, which catches the small page numbers sitting in their own column."""
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    tmp = base + ".toc.png"
    im.save(tmp)
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    subprocess.run(["tesseract", tmp, base + ".toc", "-l", OCR_LANGS, "--psm", "12", "tsv"],
                   check=True, capture_output=True, env=env)
    os.remove(tmp)
    with open(base + ".toc.size", "w") as fh:
        fh.write("%d %d" % im.size)


def read_text(issue, n):
    with open(os.path.join(WORK, "ocr", issue, "%03d.txt" % n), encoding="utf-8") as fh:
        t = BIDI_JUNK.sub("", fh.read())
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def read_words(issue, n, kind=""):
    """Word boxes (0..1000 coordinates) for highlighting search hits."""
    base = os.path.join(WORK, "ocr", issue, "%03d" % n) + kind
    with open(base + ".size") as fh:
        W, H = map(int, fh.read().split())
    words, lines = [], {}
    with open(base + ".tsv", encoding="utf-8") as fh:
        next(fh)
        for row in fh:
            c = row.rstrip("\n").split("\t")
            if len(c) < 12 or c[0] != "5":
                continue
            txt = BIDI_JUNK.sub("", c[11]).strip()
            if not txt or float(c[10]) < 20:
                continue
            x, y, w, h = map(int, c[6:10])
            words.append([round(x * 1000 / W), round(y * 1000 / H),
                          max(1, round(w * 1000 / W)), max(1, round(h * 1000 / H)), txt])
            # 0..1000 coordinates, words kept in tesseract's (logical) order
            lines.setdefault((c[2], c[3], c[4]), []).append(words[-1])
    return words, list(lines.values())


# ------------------------------------------------------------ metadata -----

def find_date(texts):
    """Look for 'month year' on the first few pages (TOC page has it)."""
    names = {m: m for m in HEB_MONTHS}
    names.update(MONTH_ALIASES)
    pat = re.compile(r"([א-ת]{3,})\s*[,.\-]?\s*((?:19|20)\d\d)\b")
    for n in (3, 1, 2):
        for m in pat.finditer(texts.get(n, "")):
            word, year = m.group(1), int(m.group(2))
            if not 1989 <= year <= 2004:
                continue
            for name, mon in names.items():
                # OCR often drops the first letter ("צמבר" for "דצמבר")
                if word == name or (len(word) >= 3 and name.endswith(word) and len(name) - len(word) <= 1):
                    return year, HEB_MONTHS.index(mon) + 1
    return None


def fill_dates(issues):
    """Issues without a printed date get one estimated from their neighbours
    (the magazine was monthly)."""
    known = [(i["num"], i["year"] * 12 + i["month"] - 1) for i in issues if i.get("year")]
    for i in issues:
        if i.get("year") or not known:
            continue
        ref = min(known, key=lambda k: abs(k[0] - i["num"]))
        idx = ref[1] + (i["num"] - ref[0])
        i["year"], i["month"], i["date_estimated"] = idx // 12, idx % 12 + 1, True


NUM_TOKEN = re.compile(r"^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?$")
JUNK = re.compile(r"^[\s|=■□▪●•★*·.,:;_\-–—~'\"`\[\](){}<>]+|[\s|=■□▪●•★*·.,:;_\-–—~'\"`\[\](){}<>]+$")


LATIN = re.compile(r"^[\"'“(]*[A-Za-z0-9][A-Za-z0-9.,:'’\-&!?)\"”]*$")
EDGE_JUNK = re.compile(r"^([^\w֐-׿\"]+|[8e₪©0])$")


def _title(words):
    # Order words by position: Hebrew lines read right-to-left with English
    # runs inside them left-to-right; pure English lines left-to-right.
    toks = [re.sub(r"^:(\w+)$", r"\1:", w[4]) for w in sorted(words, key=lambda w: -w[0])]
    if not any(re.search("[֐-׿]", t) for t in toks):
        out = toks[::-1]
    else:
        out, run = [], []
        for t in toks + [None]:
            if t is not None and LATIN.match(t):
                run.append(t)
                continue
            out.extend(reversed(run))
            run = []
            if t is not None:
                out.append(t)
    while out and EDGE_JUNK.match(out[0]):
        out.pop(0)
    while out and EDGE_JUNK.match(out[-1]):
        out.pop()
    return JUNK.sub("", " ".join(out))


def toc_links(line_sets, toc, max_page):
    """Clickable rows on the scanned TOC page: for each TOC entry, the box
    (0..1000 coords) around its page number and title on the scan."""
    cands = []
    for lines in line_sets:
        for e in auto_toc(lines, max_page, with_links=True):
            if not any(c["page"] == e["page"] for c in cands):
                cands.append(e)
    titled = [c["box"][2] - c["box"][0] for c in cands if c["title"]]
    width = sorted(titled)[len(titled) // 2] if titled else 250
    found = {}
    for c in cands:
        x0, y0, x1, y1 = c["box"]
        if not c["title"]:          # number found but not its title
            x1 = min(1000, x0 + width)
        found.setdefault(c["page"], [x0, y0, x1, y1])

    # Rows whose number OCR missed: place them between known neighbours of
    # the same column, assuming evenly spaced rows.
    pages = []
    for e in toc:
        if e["page"] not in pages:
            pages.append(e["page"])
    titles = {}
    for e in toc:
        titles.setdefault(e["page"], []).append(e.get("title", ""))
    all_lines = [ws for lines in line_sets for ws in lines]

    def toks(s):
        return [t for t in (re.sub(r"[^\w֐-׿]", "", x).lower() for x in s.split()) if len(t) >= 2]

    def tok_match(a, b):
        return a == b or (len(a) >= 3 and len(b) >= 3 and (a in b or b in a))

    def title_in_band(p, box):
        """Does a word of the entry's title appear inside this box?"""
        want = [t for s in titles.get(p, []) for t in toks(s)]
        x0, y0, x1, y1 = box
        for ws in all_lines:
            for v in ws:
                if v[1] + v[3] >= y0 - 4 and v[1] <= y1 + 4 and v[0] + v[2] >= x0 and v[0] <= x1:
                    if any(tok_match(t, w) for t in want for w in toks(v[4])):
                        return True
        return False

    def interpolate():
        for i, p in enumerate(pages):
            if p in found:
                continue
            prev = next((j for j in range(i - 1, -1, -1) if pages[j] in found), None)
            nxt = next((j for j in range(i + 1, len(pages)) if pages[j] in found), None)
            if prev is None or nxt is None:
                continue
            a, b = found[pages[prev]], found[pages[nxt]]
            if abs(a[0] - b[0]) > 30 or b[1] <= a[1]:
                continue
            step = (b[1] - a[1]) / (nxt - prev)
            if 8 <= step <= 60:
                yy = a[1] + step * (i - prev)
                found[p] = [min(a[0], b[0]), round(yy), max(a[2], b[2]), round(yy + (a[3] - a[1]))]

    interpolate()

    # Still missing: look for the line that carries the entry's title.
    if found:
        ys = [b[1] for b in found.values()]
        top, bottom = min(ys) - 150, max(ys) + 150
    else:
        top, bottom = 0, 1000
    for p in pages:
        if p in found:
            continue
        want = [t for s in titles.get(p, []) for t in toks(s)]
        if not want:
            continue
        best = None
        for ws in all_lines:
            lt = [t for v in ws for t in toks(v[4])]
            if not lt or len(lt) > len(want) + 4:
                continue
            y0 = min(v[1] for v in ws)
            if not top <= y0 <= bottom:
                continue
            score = sum(any(tok_match(t, w) for w in lt) for t in want) / len(want)
            if score >= 0.5 and (not best or score > best[0] or (score == best[0] and len(lt) < best[1])):
                box = [min(v[0] for v in ws) - 40, y0, max(v[0] + v[2] for v in ws),
                       max(v[1] + v[3] for v in ws)]
                best = (score, len(lt), box)
        if best:
            bx = best[2]
            # snap to the column this row belongs to (same right edge)
            col = [b for b in found.values() if abs(b[2] - bx[2]) <= 40]
            if col:
                x0s = sorted(b[0] for b in col)
                hs = sorted(b[3] - b[1] for b in col)
                h = hs[len(hs) // 2]
                cy = (bx[1] + bx[3]) / 2
                bx = [min(bx[0] + 40, x0s[len(x0s) // 2]), round(cy - h / 2),
                      max(b[2] for b in col), round(cy + h / 2)]
            found[p] = bx

    interpolate()

    # Last resort: continue an evenly spaced column up/down, but only where
    # the entry's title is actually visible at that spot.
    for i, p in enumerate(pages):
        if p in found:
            continue
        prev = next((j for j in range(i - 1, -1, -1) if pages[j] in found), None)
        nxt = next((j for j in range(i + 1, len(pages)) if pages[j] in found), None)
        box = None
        if prev is not None and prev - 1 >= 0 and pages[prev - 1] in found:
            a, b = found[pages[prev - 1]], found[pages[prev]]
            step, k = b[1] - a[1], i - prev
            if abs(a[0] - b[0]) <= 30 and 8 <= step <= 60 and k <= 3:
                box = [b[0], b[1] + step * k, b[2], b[3] + step * k]
        if box is None and nxt is not None and nxt + 1 < len(pages) and pages[nxt + 1] in found:
            a, b = found[pages[nxt]], found[pages[nxt + 1]]
            step, k = b[1] - a[1], nxt - i
            if abs(a[0] - b[0]) <= 30 and 8 <= step <= 60 and k <= 3 and a[1] - step * k > 0:
                box = [a[0], a[1] - step * k, a[2], a[3] - step * k]
        if box and title_in_band(p, box):
            found[p] = box

    links = []
    for p in sorted(found):
        if p not in pages:
            continue
        x0, y0, x1, y1 = found[p]
        pad = max(2, round((y1 - y0) * 0.45))
        links.append({"page": p, "box": [x0 - 4, y0 - pad, x1 - x0 + 8, y1 - y0 + 2 * pad]})
    return links


def auto_toc(lines, max_page, with_links=False):
    """Best-effort table of contents from the OCR of page 3.

    TOC page numbers sit in a vertical column, so: find number tokens that
    line up in a column (>=4 of them), then attach to each the text line at
    the same height next to it. Curated files in ./toc override this."""
    nums = []
    for li, ws in enumerate(lines):
        for w in ws:
            m = NUM_TOKEN.match(w[4].strip(".:|,"))
            if m and 2 <= int(m.group(1)) <= max_page and (not m.group(2) or int(m.group(2)) <= max_page):
                nums.append({"li": li, "w": w, "p": int(m.group(1)),
                             "end": int(m.group(2)) if m.group(2) else None})
    # cluster by horizontal position
    nums.sort(key=lambda n: n["w"][0] + n["w"][2] / 2)
    cols, cur = [], []
    for n in nums:
        cx = n["w"][0] + n["w"][2] / 2
        if cur and cx - (cur[0]["w"][0] + cur[0]["w"][2] / 2) > 22:
            cols.append(cur)
            cur = []
        cur.append(n)
    if cur:
        cols.append(cur)
    picked = []
    for c in cols:
        if len(c) < 4:
            continue
        # Page numbers grow down a TOC column; keep the longest increasing run
        # (drops stray numbers from the editorial text next to it).
        c = sorted(c, key=lambda n: n["w"][1])
        best = [[n] for n in c]
        for i in range(len(c)):
            for j in range(i):
                if c[j]["p"] < c[i]["p"] and len(best[j]) + 1 > len(best[i]):
                    best[i] = best[j] + [c[i]]
        seq = max(best, key=len)
        if len(seq) >= 4:
            picked.extend(seq)
    picked_ids = {id(n["w"]) for n in picked}

    entries = []
    for n in picked:
        x, y, w, h, _ = n["w"]
        cy = y + h / 2
        best, best_gap = None, 380
        for li, ws in enumerate(lines):
            # Hebrew TOC: the title is to the right of its page number.
            rest = [v for v in ws if id(v) not in picked_ids and v[0] >= x + w - 3]
            if not rest or len(re.sub(r"\W", "", "".join(v[4] for v in rest))) < 3:
                continue
            ly0 = min(v[1] for v in rest)
            ly1 = max(v[1] + v[3] for v in rest)
            if not (ly0 - h * 0.6 <= cy <= ly1 + h * 0.6):
                continue
            gap = min(v[0] for v in rest) - (x + w)
            if gap < best_gap:
                best, best_gap = rest, gap
        e = {"page": n["p"], "title": "", "box": [x, y, x + w, y + h]}
        if n["end"]:
            e["page_end"] = n["end"]
        if best:
            e["title"] = _title(best)
            # horizontal extent from the title, vertical from the number only
            # (title lines sometimes merge with the next row)
            e["box"] = [min(x, *(v[0] for v in best)), y,
                        max(x + w, *(v[0] + v[2] for v in best)), y + h]
        entries.append(e)
    if with_links:
        return entries
    entries = [e for e in entries if len(re.sub(r"\W", "", e["title"])) >= 3]
    for e in entries:
        e.pop("box", None)
    seen, out = set(), []
    for e in sorted(entries, key=lambda e: e["page"]):
        k = (e["page"], e["title"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def load_toc(issue):
    path = os.path.join(TOC_DIR, issue + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


# ----------------------------------------------------------------- main ----

def js_dump(path, varname, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(varname + "=")
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdfs", default=os.path.join(ROOT, "pdfs"))
    ap.add_argument("--only", nargs="*", type=int)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    for tool in ("pdfimages", "pdfinfo", "tesseract"):
        if not shutil.which(tool):
            sys.exit(f"Missing tool: {tool}. Run setup first (see README).")

    pdfs = {}
    for f in glob.glob(os.path.join(args.pdfs, "*.pdf")) + glob.glob(os.path.join(args.pdfs, "*.PDF")):
        m = re.search(r"(\d+)\D*$", os.path.splitext(os.path.basename(f))[0])
        if m:
            pdfs[int(m.group(1))] = f
    if args.only:
        pdfs = {k: v for k, v in pdfs.items() if k in args.only}
    if not pdfs:
        sys.exit(f"No PDFs found in {args.pdfs}")
    log(f"Found {len(pdfs)} issues. OCR with {args.jobs} parallel jobs.")

    os.makedirs(os.path.join(SITE, "data", "words"), exist_ok=True)
    os.makedirs(os.path.join(SITE, "data", "text"), exist_ok=True)
    plans = {}
    for num in sorted(pdfs):
        issue = "%03d" % num
        plan_path = os.path.join(WORK, "plan", issue + ".json")
        swaps = load_swaps(issue)
        sizes = pdf_page_sizes(pdfs[num])
        raw = None
        if sizes is None:
            raw = extract_raw(pdfs[num], issue)
            sizes = raw_sizes(raw)
        new = apply_swaps(plan_pages(sizes), swaps)
        old = json.load(open(plan_path)) if os.path.exists(plan_path) else []
        key = lambda p: (p["pdf"], p["half"], p.get("span", 1))
        old_by_n = {p["n"]: key(p) for p in old}
        new_by_n = {p["n"]: key(p) for p in new}
        changed = sorted(n for n in set(old_by_n) | set(new_by_n) if old_by_n.get(n) != new_by_n.get(n))
        if old and changed:
            log(f"  [{issue}] page layout changed for pages {changed} – redoing them")
        for n in changed:
            forget_page(issue, n)
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, "w") as fh:
            json.dump(new, fh)
        if raw is None and not all_done(issue, new):
            raw = extract_raw(pdfs[num], issue)
        plans[num] = (raw, new)

    jobs = [(raw, p, "%03d" % num) for num, (raw, pages) in plans.items() for p in pages]
    t0, done = time.time(), 0
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(ocr_page, j) for j in jobs]
        for fut in as_completed(futs):
            issue, n, status = fut.result()
            done += 1
            if status != "cached" and (done % 10 == 0 or done == len(jobs)):
                el = time.time() - t0
                eta = el / done * (len(jobs) - done)
                log(f"  {done}/{len(jobs)} pages  (~{eta/60:.0f} min left)")
    log("OCR finished. Building site data...")

    # Merge with issues that were built earlier (so --only doesn't drop them).
    meta_path = os.path.join(WORK, "issues.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            meta = {i["num"]: i for i in json.load(fh)}

    os.makedirs(os.path.join(WORK, "text"), exist_ok=True)
    toc_pages = os.path.join(ROOT, "toc_pages")
    os.makedirs(toc_pages, exist_ok=True)
    t0 = time.time()
    for done, (num, (raw, pages)) in enumerate(plans.items(), 1):
        issue = "%03d" % num
        texts, words, lines3, line_sets = {}, {}, [], []
        for p in pages:
            texts[p["n"]] = read_text(issue, p["n"])
            w, lines = read_words(issue, p["n"])
            words[p["n"]] = w
            if p["n"] == 3:
                lines3 = lines
                if os.path.exists(os.path.join(WORK, "ocr", issue, "003.toc.tsv")):
                    lines3 = read_words(issue, 3, ".toc")[1]
                line_sets = [lines3, lines]
        js_dump(os.path.join(SITE, "data", "words", issue + ".js"),
                "WIZ_WORDS[%s]" % json.dumps(issue), words)
        with open(os.path.join(WORK, "text", issue + ".json"), "w", encoding="utf-8") as fh:
            json.dump(texts, fh, ensure_ascii=False)
        js_dump(os.path.join(SITE, "data", "text", issue + ".js"),
                "WIZ_ITEXT[%s]" % json.dumps(issue), texts)
        info = {"num": num, "id": issue,
                "pdf": os.path.relpath(pdfs[num], SITE),
                "pages": [[p["n"], p["pdf"]] for p in pages]}
        d = find_date(texts)
        if d:
            info["year"], info["month"] = d
        toc = load_toc(issue)
        if toc:
            info.update({k: v for k, v in toc.items() if k in ("year", "month", "title", "notes")})
            info["date_estimated"] = toc.get("date_estimated", False) if "year" in toc else False
            info["toc"], info["toc_source"] = toc.get("entries", []), "curated"
        else:
            info["toc"], info["toc_source"] = auto_toc(lines3, len(pages)), "auto"
        info["toc_links"] = toc_links(line_sets, info["toc"], len(pages)) if line_sets else []
        meta[num] = info
        src = os.path.join(SITE, "img", issue, "003.jpg")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(toc_pages, f"wiz-{issue}-toc.jpg"))
        if done % 10 == 0 or done == len(plans):
            el = time.time() - t0
            eta = el / done * (len(plans) - done)
            log(f"  {done}/{len(plans)} issues  (~{eta:.0f} s left)")

    issues = [meta[k] for k in sorted(meta)]
    for i in issues:
        if i.get("date_estimated") and not (load_toc(i["id"]) or {}).get("year"):
            i.pop("year", None); i.pop("month", None); i.pop("date_estimated", None)
    log("Writing issue list, icons and search text...")
    fill_dates(issues)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(issues, fh, ensure_ascii=False, indent=1)
    js_dump(os.path.join(SITE, "data", "issues.js"), "WIZ_ISSUES", issues)
    make_brand(issues)

    # One full-text file for the search (all issues built so far).
    alltext = {}
    for i in issues:
        p = os.path.join(WORK, "text", i["id"] + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                alltext[i["id"]] = json.load(fh)
    js_dump(os.path.join(SITE, "data", "text.js"), "WIZ_TEXT", alltext)
    n_pages = sum(len(v) for v in alltext.values())
    log(f"Done: {len(issues)} issues, {n_pages} pages. Open site/index.html")


if __name__ == "__main__":
    main()

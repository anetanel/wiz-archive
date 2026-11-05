#!/usr/bin/env python3
"""
Fix Hebrew words that the heb+eng OCR read as Latin gibberish ("oy" for עם,
"ON" for אם, "WAN" for אפשר ...).

For every Latin word inside a mostly-Hebrew line, the word's box is cut from
the page image and read again with Hebrew-only tesseract. The Hebrew reading
replaces the Latin one when it is believable:
  - a word that occurs often elsewhere in the archive, or a confident rare one;
  - real English (dictionary words, words seen in English-only lines) is kept
    unless the original read was unsure and the Hebrew one is a common word.
Result: ocrfix/NNN.json ({"page": [[block, par, line, word, "old", "new"]]}),
which build.py applies on top of the cached OCR. Run build.py afterwards.

Usage:
    .venv/bin/python tools/ocrfix.py              # all issues (~15 min on 12 cores)
    .venv/bin/python tools/ocrfix.py --only 2 88
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import subprocess
import sys
from multiprocessing import Pool

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from build import BIDI_JUNK, FIX_DIR, SITE, WORK  # noqa: E402

HEB = re.compile("[א-ת]")
LAT = re.compile("[A-Za-z]")
HEB_WORD = re.compile("[א-ת]+([\"'׳״][א-ת]+)?")
PUNCT = ".,:;!?()\"'-–׳״[]{}<>/*“”‘’«»"
WORDLIST = "/usr/share/dict/words"


def tsv_lines(path):
    lines = {}
    with open(path, encoding="utf-8") as fh:
        next(fh)
        for row in fh:
            c = row.rstrip("\n").split("\t")
            if len(c) < 12 or c[0] != "5":
                continue
            txt = BIDI_JUNK.sub("", c[11]).strip()
            if txt:
                lines.setdefault(tuple(c[2:5]), []).append((tuple(map(int, c[2:6])), txt,
                                                            float(c[10]), tuple(map(int, c[6:10]))))
    return lines.values()


def lexicon(tsvs):
    """Word counts: Hebrew words anywhere, Latin words in lines without Hebrew."""
    heb, eng = collections.Counter(), collections.Counter()
    for path in tsvs:
        for ws in tsv_lines(path):
            hebrew_line = any(HEB.search(w[1]) for w in ws)
            for w in ws:
                t = w[1].strip(PUNCT)
                if HEB_WORD.fullmatch(t):
                    heb[t] += 1
                elif not hebrew_line and t.isascii() and t.isalpha():
                    eng[t.lower()] += 1
    return heb, eng


def candidates(path):
    """Latin words in lines that are mostly Hebrew."""
    for ws in tsv_lines(path):
        heb = sum(1 for w in ws if HEB.search(w[1]))
        lat = sum(1 for w in ws if LAT.search(w[1]) and not HEB.search(w[1]))
        if heb >= 2 and heb >= lat:
            for w in ws:
                if LAT.search(w[1]) and not HEB.search(w[1]):
                    yield w


def reread(job):
    """Hebrew-only OCR of one word box; returns (text, confidence)."""
    issue, n, (x, y, w, h) = job
    with open(os.path.join(WORK, "ocr", issue, "%03d.size" % n)) as fh:
        H = int(fh.read().split()[1])
    im = Image.open(os.path.join(SITE, "img", issue, "%03d.jpg" % n)).convert("L")
    s = im.height / H        # OCR ran on a copy scaled to H pixels high
    pad = int(h * s * 0.3) + 2
    im = im.crop((max(0, int(x * s) - pad), max(0, int(y * s) - pad),
                  int((x + w) * s) + pad, int((y + h) * s) + pad))
    im = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = subprocess.run(["tesseract", "stdin", "-", "-l", "heb", "--psm", "8", "tsv"],
                         input=buf.getvalue(), capture_output=True,
                         env=dict(os.environ, OMP_THREAD_LIMIT="1")).stdout.decode()
    rows = [r.split("\t") for r in out.split("\n")[1:]]
    rows = [r for r in rows if len(r) >= 12 and r[0] == "5" and r[11].strip()]
    return " ".join(r[11] for r in rows), min((float(r[10]) for r in rows), default=0)


def decide(old, conf, new, hconf, heb, eng, english):
    """The replacement for `old`, or None to keep it."""
    core = new.strip(PUNCT)
    if not HEB_WORD.fullmatch(core) or len(core) < 2:
        return None
    t = old.strip(PUNCT)
    real = t.isalpha() and (len(t) >= 3 and t.lower() in english or eng[t.lower()] >= 3)
    freq = heb[core]
    if real and len(core) < len(t):
        ok = False      # GET -> זה: a real word read as a shorter Hebrew one
    elif real and conf >= 80:
        ok = hconf >= 55 and freq >= 20
    elif len(core) == 2:
        ok = freq >= 300
    else:
        ok = freq >= 200 or (hconf >= 40 and freq >= 5) or \
             (len(core) >= 3 and hconf >= 75 and not (real and conf >= 60) and conf < 80)
    if not ok:
        return None
    # psm 8 invents trailing dots; keep only punctuation the first read also saw
    seen = set(old) & set(PUNCT)
    lead = "".join(ch for ch in new[:len(new) - len(new.lstrip(PUNCT))] if ch in seen)
    trail = "".join(ch for ch in new[len(new.rstrip(PUNCT)):] if ch in seen)
    return lead + core + trail


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", type=int, help="issue numbers")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    tsvs = sorted(glob.glob(os.path.join(WORK, "ocr", "*", "[0-9][0-9][0-9].tsv")))
    heb, eng = lexicon(tsvs)
    english = set()
    if os.path.exists(WORDLIST):
        with open(WORDLIST, encoding="utf-8", errors="ignore") as fh:
            english = {w.strip().lower() for w in fh if w.strip().isalpha()}
    else:
        print("note: %s not found, English words are recognised by the archive only" % WORDLIST)

    cands = []
    for path in tsvs:
        issue, n = os.path.basename(os.path.dirname(path)), int(os.path.basename(path)[:3])
        if args.only and int(issue) not in args.only:
            continue
        cands += [(issue, n, w) for w in candidates(path)]
    print("%d Latin words in Hebrew lines, reading them again..." % len(cands), flush=True)

    fixes = collections.defaultdict(lambda: collections.defaultdict(list))
    with Pool(args.jobs) as pool:
        reads = pool.imap(reread, [(i, n, w[3]) for i, n, w in cands], chunksize=64)
        for k, ((issue, n, (key, old, conf, _)), (new, hconf)) in enumerate(zip(cands, reads), 1):
            fixed = decide(old, conf, new, hconf, heb, eng, english)
            if fixed:
                fixes[issue][n].append(list(key) + [old, fixed])
            if k % 5000 == 0:
                print("  %d/%d" % (k, len(cands)), flush=True)

    os.makedirs(FIX_DIR, exist_ok=True)
    issues = sorted({c[0] for c in cands})
    for issue in issues:
        path = os.path.join(FIX_DIR, issue + ".json")
        if fixes[issue]:
            pages = {str(n): v for n, v in sorted(fixes[issue].items())}
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{\n" + ",\n".join(
                    "%s: [\n  %s\n]" % (json.dumps(n), ",\n  ".join(json.dumps(f, ensure_ascii=False) for f in v))
                    for n, v in pages.items()) + "\n}\n")
        elif os.path.exists(path):
            os.remove(path)
    total = sum(len(v) for p in fixes.values() for v in p.values())
    print("%d words fixed in %d issues -> %s" % (total, len(fixes), os.path.relpath(FIX_DIR, ROOT)))


if __name__ == "__main__":
    main()

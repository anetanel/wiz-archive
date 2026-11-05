# WIZ Archive

Searchable archive of the Israeli computer-games magazine **WIZ** (126 issues, 1991–2001),
built from scanned PDFs. Hebrew, RTL. Runs locally (open `site/index.html`, no server)
and is published to **Cloudflare Pages** (static site + PDFs) with a public **REST API**
(Pages Function + **D1** SQLite).

The owner works on macOS; the repo (code + local data) lives at `~/repos/wiz-archive`.

## Pipeline

1. `run.command` – installs Homebrew deps (tesseract + `tesseract-lang`, poppler, python),
   makes `.venv` with Pillow, runs `build.py`, opens the site.
2. `build.py` – for every `pdfs/wiz-NNN.pdf`:
   - plans magazine pages from the scan sizes (`pdfimages -list`): page 1 single cover,
     then two-page spreads (right half = lower/even page, Hebrew order), single back cover;
     a much smaller trailing image is a scanner credit page and is skipped; a tall portrait
     page in the middle is a sideways **centrefold poster** that takes two page numbers
     (issue 3: page 20, then 22). `"swap": [22, "30-40"]` in `toc/NNN.json` flips the halves
     of mis-scanned spreads.
   - extracts scans to `work/raw` only when something must be (re)built, splits spreads,
     OCRs each page (`tesseract -l heb+eng`, 2nd sparse pass on the TOC page 3),
     caches OCR in `work/ocr`, writes page images to `site/img/NNN/`.
     If a page's layout changes, only that page is redone.
   - writes `site/data/issues.js` (metadata + TOC), `site/data/text.js` (all text, for
     client-side search), `site/data/text/NNN.js`, `site/data/words/NNN.js` (word boxes
     for highlighting hits on the scan), clickable TOC rows (`toc_links`),
     `review/wiz-NNN.jpg` (spread-footer sheets for spotting reversed spreads),
     favicons + `og.jpg` from `assets/logo.png`.
3. `deploy.command` (project name arg, default `wiz-archive`) – installs wrangler
   **4.156.2** into `.tools/`, runs `deploy.py` (assembles `deploy/` = site + PDFs as
   hard links, rewrites `__SITE_URL__` in HTML for Open Graph, checks the Pages free limits:
   ≤20,000 files, ≤25 MiB/file), logs in with `wrangler login --browser=false`
   (user pastes the URL into the browser where they are signed in), creates the D1 DB
   and `wrangler.toml` on first run, syncs API data, then `wrangler pages deploy deploy`.

## Table of contents data

- `toc/NNN.json` – hand-transcribed from each issue's TOC page: `{year, month,
  date_estimated, notes?, swap?, entries: [{page, page_end?, section?, title}]}`.
  These are curated data – edit carefully, keep `swap`/`notes`.
- `tools/tocsrc/b1..b5.txt` + `convert.py` – the plain-text source most toc files were
  generated from (`#NNN`, `@date YYYY-MM[~]`, `@note`, `@swap`, `@keep`,
  `page|section|title`). Issues 050, 080, 100, 126 exist only as JSON. If you regenerate,
  check you don't drop hand edits made directly in JSON.
- Dates of issues 1–17 are estimates (`date_estimated`); from 18 on they are printed.

## REST API (`functions/api/[[path]].js`, docs page `site/developers.html` → `/developers`)

`/api` (browser → redirect to docs, else JSON index), `/api/issues`, `/api/issues/{n}`,
`/api/issues/{n}/pages/{p}` (full OCR text), `/api/search?q=` (FTS5, all words,
"exact phrase", Hebrew prefix letters ו ה ב כ ל מ ש stripped at index time, `from/to/
sort/limit/offset`), `/api/articles`, `/api/sections`. CORS `*`, no key.
Normalisation `norm()` / tokenisation must stay identical in `build_api.py` and the JS.

### D1 write budget – important
Free plan: **100,000 rows written per day** (resets 00:00 UTC). A full import is ~30,000.
`build_api.py` writes `api/schema.sql`, `api/data.sql`, per-issue `api/issues/NNN.json`.
`api_sync.py` compares them with `api/imported/` (what the live DB holds) and produces
`api/update.sql` touching only changed issues (contentless FTS5 rows are removed with the
`'delete'` command, which needs the old tokens – that is why `api/imported/` stores them).
`deploy.command` asks before any full import. **Never delete `api/imported/` or
`api/imported-schema.sha256`** – without them the next deploy needs a full import.
Changing the schema (or FTS options) forces a full import: plan it for a fresh day.
Test API changes locally against `node:sqlite` with `api/wiz.sql` before deploying.

## Frontend (`site/`)

Static SPA, hash routes: `#/` covers by year, `#/issue/NNN`, `#/read/NNN/P?q=` (reader:
single/double page, search-hit highlight boxes, TOC/OCR side panel, clickable rows on the
scanned TOC page, keyboard arrows, swipe on phones — swipe right = next page),
`#/search?q=`, `#/articles`, `#/random`. Must keep working from `file://` (scripts are
loaded as `.js` globals, no fetch of JSON). Mobile layout under `@media (max-width:700px)`.

## Conventions
- UI text is Hebrew; code/comments English.
- Don't commit scans, page images or generated data (see `.gitignore`).
- Verify UI changes with Playwright screenshots (desktop + `devices['iPhone 13']`).
- The owner should be told plainly about copyright: the scans and full OCR text are
  commercial content; publishing is their call.

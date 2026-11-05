#!/bin/bash
# WIZ archive: installs what's needed (first time only), runs OCR on every
# PDF in ./pdfs and opens the site. Safe to run again: finished pages are skipped.
set -e
cd "$(dirname "$0")"

echo "=== ארכיון WIZ ==="

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)"
  done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "מתקין Homebrew (ידרוש סיסמת מחשב)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)"
  done
fi

# 2. OCR tools (tesseract + Hebrew language pack, poppler for PDF images)
for pkg in tesseract tesseract-lang poppler python@3.12; do
  brew list "$pkg" >/dev/null 2>&1 || { echo "מתקין $pkg..."; brew install "$pkg"; }
done

# 3. Python environment with Pillow
PY="$(brew --prefix python@3.12)/bin/python3.12"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip pillow
fi

# 4. Build
for f in site/index.html site/app.js site/style.css build.py; do
  if [ ! -f "$f" ]; then
    echo "חסר הקובץ $f בתיקייה $(pwd)"
    echo "חלצו מחדש את WIZ-Archive.zip (או את site-files.zip לתוך התיקייה site) והריצו שוב."
    read -p "Enter לסיום"; exit 1
  fi
done
if ! ls pdfs/*.pdf >/dev/null 2>&1; then
  echo "לא נמצאו קבצי PDF בתיקייה pdfs. העתיקו לשם את wiz-001.pdf ... wiz-126.pdf והריצו שוב."
  read -p "Enter לסיום"; exit 1
fi
CORES=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
.venv/bin/python build.py --jobs "$CORES" "$@"

# 5. Open the site
open site/index.html
echo "סיום. האתר נפתח בדפדפן (site/index.html)."

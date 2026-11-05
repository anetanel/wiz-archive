#!/bin/bash
# WIZ archive: installs what's needed (first time only), runs OCR on every
# PDF in ./pdfs and opens the site. Safe to run again: finished pages are skipped.
# Works on macOS (Homebrew) and Linux (Debian/Ubuntu/Mint: prints the apt line).
set -e
cd "$(dirname "$0")"

echo "=== ארכיון WIZ ==="

case "$(uname -s)" in
  Darwin)
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
    PY="$(brew --prefix python@3.12)/bin/python3.12"
    CORES=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
    OPEN=open
    ;;
  Linux)
    # 1-2. System packages are not installed from here (needs sudo): list what's missing
    MISSING=()
    if ! command -v tesseract >/dev/null 2>&1; then
      MISSING+=(tesseract-ocr tesseract-ocr-heb tesseract-ocr-eng)
    else
      LANGS="$(tesseract --list-langs 2>&1)"
      grep -qx heb <<< "$LANGS" || MISSING+=(tesseract-ocr-heb)
      grep -qx eng <<< "$LANGS" || MISSING+=(tesseract-ocr-eng)
    fi
    for t in pdfimages pdfinfo pdftoppm; do
      command -v "$t" >/dev/null 2>&1 || { MISSING+=(poppler-utils); break; }
    done
    # python3 -m venv needs ensurepip, which Debian ships separately (python3-venv)
    if ! command -v python3 >/dev/null 2>&1; then
      MISSING+=(python3 python3-venv)
    elif ! python3 -c "import ensurepip, venv" >/dev/null 2>&1; then
      MISSING+=(python3-venv)
    fi
    if [ ${#MISSING[@]} -gt 0 ]; then
      echo "חסרות תוכנות. התקינו אותן עם הפקודה הבאה והריצו שוב:"
      echo
      echo "  sudo apt install ${MISSING[*]}"
      echo
      read -rp "Enter לסיום"; exit 1
    fi
    PY=python3
    CORES=$(nproc 2>/dev/null || echo 4)
    OPEN=xdg-open
    ;;
  *)
    echo "מערכת הפעלה לא נתמכת: $(uname -s)"; exit 1 ;;
esac

# 3. Python environment with Pillow (recreated if it was copied from another machine)
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import PIL" >/dev/null 2>&1; then
  rm -rf .venv
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip pillow
fi

# 4. Build
for f in site/index.html site/app.js site/style.css build.py; do
  if [ ! -f "$f" ]; then
    echo "חסר הקובץ $f בתיקייה $(pwd)"
    echo "חלצו מחדש את WIZ-Archive.zip (או את site-files.zip לתוך התיקייה site) והריצו שוב."
    read -rp "Enter לסיום"; exit 1
  fi
done
if ! ls pdfs/*.pdf >/dev/null 2>&1; then
  echo "לא נמצאו קבצי PDF בתיקייה pdfs. העתיקו לשם את wiz-001.pdf ... wiz-126.pdf והריצו שוב."
  read -rp "Enter לסיום"; exit 1
fi
.venv/bin/python build.py --jobs "$CORES" "$@"

# 5. Open the site
"$OPEN" site/index.html >/dev/null 2>&1 || true
echo "סיום. האתר נפתח בדפדפן (site/index.html)."

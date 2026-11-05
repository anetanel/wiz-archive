#!/bin/bash
# Publish the WIZ archive (site + PDFs) to Cloudflare Pages.
# Usage: deploy.sh [project-name]     (default: wiz-archive)
# Works on macOS (Homebrew) and Linux (Debian/Ubuntu/Mint: prints the apt line).
set -e
cd "$(dirname "$0")"
PROJECT="${1:-wiz-archive}"
WRANGLER_VERSION="4.156.2"

case "$(uname -s)" in
  Darwin)
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      [ -x "$b" ] && eval "$("$b" shellenv)"
    done
    command -v node >/dev/null 2>&1 || { echo "מתקין Node.js..."; brew install node; }
    ;;
  Linux)
    # no sudo from here: print the apt line for whatever is missing
    MISSING=()
    command -v node >/dev/null 2>&1 || MISSING+=(nodejs)
    command -v npm >/dev/null 2>&1 || MISSING+=(npm)
    if [ ${#MISSING[@]} -gt 0 ]; then
      echo "חסרות תוכנות. התקינו אותן עם הפקודה הבאה והריצו שוב:"
      echo
      echo "  sudo apt install ${MISSING[*]}"
      echo
      read -rp "Enter לסיום"; exit 1
    fi
    ;;
  *)
    echo "מערכת הפעלה לא נתמכת: $(uname -s)"; exit 1 ;;
esac

# wrangler installed once into ./.tools (fixed version, no re-download each run)
WRANGLER="./.tools/node_modules/.bin/wrangler"
if [ ! -x "$WRANGLER" ] || ! "$WRANGLER" --version 2>/dev/null | grep -q "$WRANGLER_VERSION"; then
  echo "מתקין wrangler $WRANGLER_VERSION..."
  npm install --silent --no-fund --no-audit --prefix ./.tools "wrangler@$WRANGLER_VERSION"
fi

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== מרכיב את התיקייה deploy ==="
# the public address (for link previews); set SITE_URL for a custom domain
SITE_URL="${SITE_URL:-https://$PROJECT.pages.dev}" "$PY" deploy.py

if [ -z "$CLOUDFLARE_API_TOKEN" ] && ! "$WRANGLER" whoami 2>/dev/null | grep -qi "associated with the email"; then
  echo
  echo "=== התחברות ל-Cloudflare ==="
  echo "בעוד רגע יודפס קישור ארוך (https://dash.cloudflare.com/oauth2/...)."
  echo "העתיקו אותו והדביקו בדפדפן שבו אתם מחוברים ל-Cloudflare, ואשרו."
  echo "אל תסגרו את החלון הזה עד שתופיע הודעת הצלחה."
  echo
  "$WRANGLER" login --browser=false
fi

# Create the project the first time (ignore the error if it already exists).
"$WRANGLER" pages project create "$PROJECT" --production-branch=main >/dev/null 2>&1 || true

# --- REST API: D1 database ---------------------------------------------------
DB_NAME="$PROJECT-db"
echo "=== מכין את מסד הנתונים של ה-API ==="
"$PY" api_sync.py legacy
"$PY" build_api.py
if [ ! -f wrangler.toml ] || ! grep -q "database_name = \"$DB_NAME\"" wrangler.toml; then
  "$WRANGLER" d1 create "$DB_NAME" >/dev/null 2>&1 || true
  DB_ID=$("$WRANGLER" d1 list --json | "$PY" -c "import json,sys; print(next(d['uuid'] for d in json.load(sys.stdin) if d['name']=='$DB_NAME'))")
  cat > wrangler.toml <<EOF
name = "$PROJECT"
pages_build_output_dir = "deploy"
compatibility_date = "2026-10-01"

[[d1_databases]]
binding = "DB"
database_name = "$DB_NAME"
database_id = "$DB_ID"
EOF
  rm -rf api/imported.sha256 api/imported api/imported-schema.sha256
  echo "נוצר מסד נתונים $DB_NAME ($DB_ID)"
fi
# D1 free plan: 100,000 written rows a day. Only issues that changed are sent.
read -r MODE ROWS NISS <<< "$("$PY" api_sync.py plan)"
case "$MODE" in
  none)
    echo "נתוני ה-API לא השתנו – מדלג." ;;
  update)
    echo "מעדכן ב-API $NISS גליונות שהשתנו (כ-$ROWS שורות)..."
    "$WRANGLER" d1 execute "$DB_NAME" --remote --yes --file api/update.sql
    "$PY" api_sync.py commit update ;;
  full)
    echo
    echo "!! נדרש ייבוא מלא של נתוני ה-API: כ-$ROWS כתיבות."
    echo "   בתוכנית החינמית מותרות 100,000 כתיבות ביום (מתאפס ב-02:00 שעון ישראל)."
    read -rp "   לייבא עכשיו? (y = כן, Enter = לדלג ולהעלות רק את האתר) " ANSWER
    if [ "$ANSWER" = "y" ] || [ "$ANSWER" = "Y" ]; then
      "$WRANGLER" d1 execute "$DB_NAME" --remote --yes --command "$(cat api/schema.sql)"
      "$WRANGLER" d1 execute "$DB_NAME" --remote --yes --file api/data.sql
      "$PY" api_sync.py commit full
    else
      echo "   דילגתי. ה-API ימשיך להחזיר את הנתונים הקודמים."
    fi ;;
esac

echo "=== מעלה (בפעם הראשונה זה ייקח זמן – כמה GB) ==="
"$WRANGLER" pages deploy deploy --project-name="$PROJECT" --branch=main --commit-dirty=true

echo "סיום. הכתובת מופיעה למעלה (https://$PROJECT.pages.dev או דומה)."
read -rp "Enter לסיום"

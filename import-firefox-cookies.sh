#!/usr/bin/env bash
#
# import-firefox-cookies.sh — merge cookies (and optionally site permissions)
# from a real Firefox profile into the stealth browser MCP's persistent profile.
#
# Usage:
#   ./import-firefox-cookies.sh <profile-dir | profile.tar.gz | profile.zip>
#
# Environment overrides:
#   STEALTHFOX_PROFILE_HOST   Target Firefox profile dir (default: ./data/profile)
#   COMPOSE_FILE              Docker Compose file path (default: ../docker-compose.yaml)
#   STEALTHFOX_SERVICE_NAME   Compose service name (default: playwright-invisible-mcp)
#
# Notes:
#   * Firefox locks a live profile. Export a *closed* profile (or a copy).
#   * Saved passwords (logins.json/key4.db) do NOT transfer across operating
#     systems — on Windows/macOS the key is sealed by DPAPI/Keychain and cannot
#     be unwrapped on this Linux host.
#   * This stops the MCP container briefly; the browser profile is backed up
#     first as cookies.sqlite.bak-<timestamp>.
#
set -euo pipefail

SRC="${1:-}"
if [ -z "$SRC" ]; then
  echo "usage: $0 <firefox-profile-dir | profile.tar.gz | profile.zip>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_HOST="${STEALTHFOX_PROFILE_HOST:-${SCRIPT_DIR}/data/profile}"
COMPOSE="${COMPOSE_FILE:-${SCRIPT_DIR}/../docker-compose.yaml}"
SVC="${STEALTHFOX_SERVICE_NAME:-playwright-invisible-mcp}"
TMP="${TMPDIR:-/tmp}/stealthfox-import"

mkdir -p "$TMP"
WORK="$(mktemp -d "$TMP/fximport.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# --- resolve the source profile directory ---------------------------------
SRC_PROFILE=""
case "$SRC" in
  *.tar.gz|*.tgz) tar -xzf "$SRC" -C "$WORK" ;;
  *.tar)          tar -xf  "$SRC" -C "$WORK" ;;
  *.zip)          command -v unzip >/dev/null || { echo "unzip not installed" >&2; exit 1; }; unzip -q "$SRC" -d "$WORK" ;;
esac
if [ -f "$SRC/cookies.sqlite" ]; then
  SRC_PROFILE="$SRC"
else
  found="$(find "$WORK" -name cookies.sqlite -print -quit 2>/dev/null || true)"
  [ -n "$found" ] && SRC_PROFILE="$(dirname "$found")"
fi
[ -n "$SRC_PROFILE" ] && [ -f "$SRC_PROFILE/cookies.sqlite" ] || {
  echo "❌ no cookies.sqlite found in '$SRC'" >&2; exit 1; }

echo "→ source profile: $SRC_PROFILE"
echo "→ cookies rows : $(sudo sqlite3 "$SRC_PROFILE/cookies.sqlite" 'SELECT COUNT(*) FROM moz_cookies;' 2>/dev/null || echo '?')"

# --- stop the browser so the profile is not locked / mid-WAL ----------------
echo "→ stopping $SVC ..."
docker compose -f "$COMPOSE" stop "$SVC" >/dev/null

if [ ! -f "$PROFILE_HOST/cookies.sqlite" ]; then
  echo "❌ target profile not found at $PROFILE_HOST/cookies.sqlite" >&2
  docker compose -f "$COMPOSE" start "$SVC" >/dev/null
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
sudo cp -a "$PROFILE_HOST/cookies.sqlite" "$PROFILE_HOST/cookies.sqlite.bak-$TS"
echo "→ backup: cookies.sqlite.bak-$TS"

# --- copy source DB somewhere the sqlite process can read -------------------
cp -f "$SRC_PROFILE/cookies.sqlite" "$TMP/src-cookies.sqlite"

# --- schema-tolerant merge --------------------------------------------------
target_cols="$(sudo sqlite3 "$PROFILE_HOST/cookies.sqlite" 'PRAGMA table_info(moz_cookies);' | awk -F'|' '{print $2}' | sort)"
source_cols="$(sqlite3 "$TMP/src-cookies.sqlite" 'PRAGMA table_info(moz_cookies);' | awk -F'|' '{print $2}' | sort)"
common_cols="$(comm -12 <(printf '%s\n' "$target_cols") <(printf '%s\n' "$source_cols") | paste -sd, -)"
[ -n "$common_cols" ] || { echo "❌ no common moz_cookies columns; schema mismatch" >&2; docker compose -f "$COMPOSE" start "$SVC" >/dev/null; exit 1; }

echo "→ merging on columns: $common_cols"
sudo sqlite3 "$PROFILE_HOST/cookies.sqlite" <<SQL
PRAGMA journal_mode=WAL;
ATTACH '$TMP/src-cookies.sqlite' AS src;
INSERT OR REPLACE INTO moz_cookies ($common_cols)
  SELECT $common_cols FROM src.moz_cookies;
DETACH src;
SQL

# --- optionally merge site permissions (cookie/geo/notification allowances) --
if [ -f "$SRC_PROFILE/permissions.sqlite" ]; then
  cp -f "$SRC_PROFILE/permissions.sqlite" "$TMP/src-permissions.sqlite"
  tcols="$(sudo sqlite3 "$PROFILE_HOST/permissions.sqlite" 'PRAGMA table_info(moz_permissions);' 2>/dev/null | awk -F'|' '{print $2}' | sort)"
  scols="$(sqlite3 "$TMP/src-permissions.sqlite" 'PRAGMA table_info(moz_permissions);' 2>/dev/null | awk -F'|' '{print $2}' | sort)"
  ccols="$(comm -12 <(printf '%s\n' "$tcols") <(printf '%s\n' "$scols") | paste -sd, -)"
  if [ -n "$ccols" ]; then
    sudo cp -a "$PROFILE_HOST/permissions.sqlite" "$PROFILE_HOST/permissions.sqlite.bak-$TS" 2>/dev/null || true
    sudo sqlite3 "$PROFILE_HOST/permissions.sqlite" <<SQL
PRAGMA journal_mode=WAL;
ATTACH '$TMP/src-permissions.sqlite' AS src;
INSERT OR REPLACE INTO moz_permissions ($ccols) SELECT $ccols FROM src.moz_permissions;
DETACH src;
SQL
    echo "→ merged site permissions too"
  fi
fi

# --- clear any stale Firefox lock so the engine can open the profile --------
sudo rm -f "$PROFILE_HOST/lock" 2>/dev/null || true

echo "→ cookies rows now: $(sudo sqlite3 "$PROFILE_HOST/cookies.sqlite" 'SELECT COUNT(*) FROM moz_cookies;')"

echo "→ starting $SVC ..."
docker compose -f "$COMPOSE" start "$SVC" >/dev/null
sleep 4
docker ps --filter name="$SVC" --format '→ {{.Names}} {{.Status}}'

echo
echo "✅ Done. Open the browser and it will carry your signed-in sessions."
echo "   If a site still shows logged out, its session cookie had already expired."

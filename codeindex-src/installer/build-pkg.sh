#!/bin/bash
# CodeIndex macOS .pkg Builder
# Builds the CLI, web UI, and creates a native macOS installer.
set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CODEINDEX_DIR="$ROOT_DIR/codeindex"
WEB_DIR="$ROOT_DIR/codeindex-web"
INSTALLER_DIR="$ROOT_DIR/installer"
BUILD_DIR="$ROOT_DIR/build"
TMP="$ROOT_DIR/.build-tmp"

# ── Step 1: Version ──────────────────────────────────────────────

VERSION_FILE="$ROOT_DIR/VERSION"

if [ -f "$VERSION_FILE" ]; then
  OLD_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
else
  OLD_VERSION=$(node -e "console.log(require('$CODEINDEX_DIR/package.json').version)")
fi

# Bump patch unless --no-bump is passed
if [ "$1" != "--no-bump" ]; then
  IFS='.' read -r MAJOR MINOR PATCH <<< "$OLD_VERSION"
  PATCH=$((PATCH + 1))
  VERSION="$MAJOR.$MINOR.$PATCH"

  echo "$VERSION" > "$VERSION_FILE"

  # Sync to package.json
  node -e "
    const fs = require('fs');
    const pkg = JSON.parse(fs.readFileSync('$CODEINDEX_DIR/package.json', 'utf-8'));
    pkg.version = '$VERSION';
    fs.writeFileSync('$CODEINDEX_DIR/package.json', JSON.stringify(pkg, null, 2) + '\n');
  "

  # Update CLI version string
  sed -i '' "s/\.version('[^']*')/\.version('$VERSION')/" "$CODEINDEX_DIR/src/cli/index.ts"
else
  VERSION="$OLD_VERSION"
fi

echo ""
echo "  CodeIndex Installer Build"
echo "  ========================="
echo "  Version: $VERSION"
echo ""

# ── Step 2: Build codeindex CLI ──────────────────────────────────

echo "  [1/5] Building codeindex CLI..."
cd "$CODEINDEX_DIR"
npm ci --silent 2>/dev/null || npm install --silent 2>/dev/null
npm run build --silent

rm -rf "$TMP"
mkdir -p "$TMP"

TGZ_FILE=$(npm pack --pack-destination "$TMP" 2>&1 | tail -1)
TGZ_PATH="$TMP/$TGZ_FILE"

if [ ! -f "$TGZ_PATH" ]; then
  echo "  ERROR: npm pack failed"
  exit 1
fi
echo "         codeindex.tgz ($(du -sh "$TGZ_PATH" | cut -f1 | xargs))"

# ── Step 3: Build web UI ─────────────────────────────────────────

echo "  [2/5] Building web UI..."
cd "$WEB_DIR"
npm ci --silent 2>/dev/null || npm install --silent 2>/dev/null
npx vite build 2>/dev/null

if [ ! -d "$WEB_DIR/dist" ]; then
  echo "  ERROR: web UI build failed (dist/ not found)"
  exit 1
fi
echo "         web-dist/ ($(du -sh "$WEB_DIR/dist" | cut -f1 | xargs))"

# ── Step 4: Stage payload ────────────────────────────────────────

echo "  [3/5] Staging payload..."
PKG_STAGING="$TMP/pkg-staging"
mkdir -p "$PKG_STAGING"

cp "$TGZ_PATH" "$PKG_STAGING/codeindex.tgz"
cp -R "$WEB_DIR/dist" "$PKG_STAGING/web-dist"

# ── Step 5: Build component package ──────────────────────────────

echo "  [4/5] Building component package..."
pkgbuild \
  --root "$PKG_STAGING" \
  --identifier com.codeindex.pkg \
  --version "$VERSION" \
  --scripts "$INSTALLER_DIR/scripts" \
  --install-location /tmp/codeindex-install \
  "$TMP/codeindex.pkg"

# ── Step 6: Build product archive ────────────────────────────────

echo "  [5/5] Building installer .pkg..."
mkdir -p "$BUILD_DIR"

# Replace version placeholders in distribution.xml and resources
sed "s/__VERSION__/$VERSION/g" \
  "$INSTALLER_DIR/distribution.xml" > "$TMP/distribution.xml"

mkdir -p "$TMP/resources"
for f in "$INSTALLER_DIR/resources"/*; do
  sed "s/__VERSION__/$VERSION/g" "$f" > "$TMP/resources/$(basename "$f")"
done

productbuild \
  --distribution "$TMP/distribution.xml" \
  --resources "$TMP/resources" \
  --package-path "$TMP" \
  "$BUILD_DIR/CodeIndex-${VERSION}.pkg"

# Remove old .pkg files
find "$BUILD_DIR" -name "CodeIndex-*.pkg" ! -name "CodeIndex-${VERSION}.pkg" -delete 2>/dev/null || true

# ── Cleanup ──────────────────────────────────────────────────────

rm -rf "$TMP"
cd "$ROOT_DIR"

PKG_SIZE=$(du -sh "$BUILD_DIR/CodeIndex-${VERSION}.pkg" | cut -f1 | xargs)

echo ""
echo "  Done!"
echo "  ─────────────────────────────────────"
echo "  build/CodeIndex-${VERSION}.pkg ($PKG_SIZE)"
echo ""
echo "  Install:"
echo "    open build/CodeIndex-${VERSION}.pkg"
echo ""

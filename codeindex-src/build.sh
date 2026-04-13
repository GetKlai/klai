#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEINDEX_DIR="$ROOT_DIR/codeindex"
BUILD_DIR="$ROOT_DIR/build"

# ── Step 1: Bump patch version ────────────────────────────────────

VERSION_FILE="$ROOT_DIR/VERSION"

if [ -f "$VERSION_FILE" ]; then
  OLD_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
else
  OLD_VERSION=$(node -e "console.log(require('$CODEINDEX_DIR/package.json').version)")
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$OLD_VERSION"
PATCH=$((PATCH + 1))
VERSION="$MAJOR.$MINOR.$PATCH"

echo ""
echo "  CodeIndex Build"
echo "  ==============="
echo "  Version: $OLD_VERSION → $VERSION"
echo ""

# Write new version to VERSION file (source of truth)
echo "$VERSION" > "$VERSION_FILE"

# Sync to package.json
node -e "
  const fs = require('fs');
  const pkg = JSON.parse(fs.readFileSync('$CODEINDEX_DIR/package.json', 'utf-8'));
  pkg.version = '$VERSION';
  fs.writeFileSync('$CODEINDEX_DIR/package.json', JSON.stringify(pkg, null, 2) + '\n');
"

# Update hardcoded version in CLI
sed -i '' "s/\.version('[^']*')/\.version('$VERSION')/" "$CODEINDEX_DIR/src/cli/index.ts"

echo "  [1/3] Version bumped"

# ── Step 2: Build & pack ──────────────────────────────────────────

echo "  [2/3] Building npm package..."
cd "$CODEINDEX_DIR"
npm run build

TMP="$ROOT_DIR/.build-tmp"
rm -rf "$TMP"
mkdir -p "$TMP"

TGZ_FILE=$(npm pack --pack-destination "$TMP" 2>&1 | tail -1)
TGZ_PATH="$TMP/$TGZ_FILE"

if [ ! -f "$TGZ_PATH" ]; then
  echo "  ERROR: npm pack failed"
  exit 1
fi

echo "         → codeindex.tgz ($(du -sh "$TGZ_PATH" | cut -f1 | xargs))"

# ── Step 3: Create zip ────────────────────────────────────────────

echo "  [3/3] Creating distribution zip..."

# Clean build dir, keep old builds around for reference
mkdir -p "$BUILD_DIR"

# Copy tgz + installer script to build/ (for local testing)
cp "$TGZ_PATH" "$BUILD_DIR/codeindex.tgz"
cp "$ROOT_DIR/codeindex.sh" "$BUILD_DIR/codeindex.sh"
chmod +x "$BUILD_DIR/codeindex.sh"

# Create zip
ZIP_STAGING="$TMP/CodeIndex-${VERSION}"
mkdir -p "$ZIP_STAGING"
cp "$TGZ_PATH" "$ZIP_STAGING/codeindex.tgz"
cp "$ROOT_DIR/codeindex.sh" "$ZIP_STAGING/codeindex.sh"
chmod +x "$ZIP_STAGING/codeindex.sh"

ZIP_NAME="CodeIndex-${VERSION}.zip"
cd "$TMP"
zip -qr "$BUILD_DIR/$ZIP_NAME" "CodeIndex-${VERSION}/"
cd "$ROOT_DIR"

# Remove old zips from build/
find "$BUILD_DIR" -name "CodeIndex-*.zip" ! -name "$ZIP_NAME" -delete 2>/dev/null || true

# Clean up temp
rm -rf "$TMP"

# Clean up any old artifacts from root
rm -f "$ROOT_DIR"/CodeIndex-*.zip "$ROOT_DIR"/CodeIndex-*.pkg "$ROOT_DIR"/codeindex.tgz 2>/dev/null || true

echo ""
echo "  Done!"
echo "  ─────────────────────────────────────"
echo "  build/$ZIP_NAME ($(du -sh "$BUILD_DIR/$ZIP_NAME" | cut -f1 | xargs))"
echo "  build/codeindex.sh + codeindex.tgz"
echo ""
echo "  Install locally:"
echo "    cd build && ./codeindex.sh"
echo ""
echo "  Share the zip. Your friend unzips and runs:"
echo "    ./codeindex.sh"
echo ""

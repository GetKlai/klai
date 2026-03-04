#!/bin/bash
# update-moai.sh — Update MoAI-ADK agents naar een nieuwe versie
#
# Gebruik: ./scripts/update-moai.sh
#
# MoAI-ADK wordt gedistribueerd via https://github.com/moai-adk/moai-adk
# Na een update: controleer of agents/klai/ of rules/klai/ aanpassingen nodig hebben.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR=$(mktemp -d)

MOAI_REPO="https://github.com/moai-adk/moai-adk.git"

echo "MoAI-ADK updaten..."
echo "Huidige versie: $(cat "$ROOT_DIR/VERSION")"
echo ""

# Clone upstream MoAI
echo "Upstream ophalen..."
git clone --depth 1 "$MOAI_REPO" "$TMP_DIR" 2>/dev/null || {
    echo "Fout: kon MoAI-ADK repo niet ophalen."
    echo "Controleer of je internettoegang hebt en de repo bereikbaar is."
    rm -rf "$TMP_DIR"
    exit 1
}

# Toon diff
echo ""
echo "Wijzigingen ten opzichte van huidige agents/moai/:"
diff -rq "$ROOT_DIR/agents/moai/" "$TMP_DIR/.claude/agents/moai/" 2>/dev/null || true
echo ""

read -p "Doorgaan met update? (j/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Jj]$ ]]; then
    echo "Update geannuleerd."
    rm -rf "$TMP_DIR"
    exit 0
fi

# Vervang agents/moai volledig
rm -rf "$ROOT_DIR/agents/moai"
cp -r "$TMP_DIR/.claude/agents/moai" "$ROOT_DIR/agents/moai/"

# Vervang rules/moai als aanwezig
if [ -d "$TMP_DIR/.claude/rules/moai" ]; then
    rm -rf "$ROOT_DIR/rules/moai"
    cp -r "$TMP_DIR/.claude/rules/moai" "$ROOT_DIR/rules/moai/"
fi

# Vervang skills als aanwezig
if [ -d "$TMP_DIR/.claude/skills" ]; then
    rm -rf "$ROOT_DIR/skills"
    cp -r "$TMP_DIR/.claude/skills" "$ROOT_DIR/skills/"
fi

rm -rf "$TMP_DIR"

echo "MoAI agents bijgewerkt."
echo "Controleer rules/klai/ en agents/klai/ op eventuele aanpassingen."
echo "Vergeet niet VERSION bij te werken en te committen."

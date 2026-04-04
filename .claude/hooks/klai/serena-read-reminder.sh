#!/usr/bin/env bash
# PreToolUse hook: remind to use Serena when reading/searching code files
#
# Fires when Read or Grep is called on code files (.py .ts .tsx .js .jsx).
# Does NOT block — injects a systemMessage reminder to use Serena first.
# NOT triggered for: .md, .css, .yaml, .json, .env (Read is correct for those)

set -euo pipefail

CODE_EXT_PATTERN='\.(py|ts|tsx|js|jsx)$'
CODE_GLOB_PATTERN='\*\.(py|ts|tsx|js|jsx)'

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_name', ''))
" 2>/dev/null || echo "")

is_code_file() {
    local path="$1"
    echo "$path" | grep -qE "$CODE_EXT_PATTERN"
}

MATCH=0

case "$TOOL_NAME" in
    Read)
        FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null || echo "")
        is_code_file "$FILE_PATH" && MATCH=1
        ;;
    Grep)
        # Check glob param (e.g. "*.py", "**/*.ts"), type param ("py", "ts"), or path ending in code ext
        GREP_INFO=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ti = d.get('tool_input', {})
parts = [ti.get('glob', ''), ti.get('path', ''), ti.get('type', '')]
print(' '.join(p for p in parts if p))
" 2>/dev/null || echo "")
        echo "$GREP_INFO" | grep -qE "$CODE_EXT_PATTERN|$CODE_GLOB_PATTERN|\b(py|ts|js)\b" && MATCH=1
        ;;
esac

if [ "$MATCH" -eq 1 ]; then
    echo '{"systemMessage": "SERENA REMINDER: You are reading/searching a code file. Consider using Serena first:\n- get_symbols_overview → see all classes/functions without reading the full file\n- find_symbol (name_path_pattern=...) → locate a specific symbol\n- find_referencing_symbols (name_path_pattern=...) → find callers\n- search_for_pattern (substring_pattern=...) → semantic search, always scope with relative_path or paths_include_glob\n- replace_symbol_body → replace a whole function atomically\nOnly use Read/Grep when you already know the exact lines or need raw text search."}'
fi

exit 0

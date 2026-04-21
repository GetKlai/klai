@echo off
echo Starting Playwright MCP server on port 8931...
echo Brave will open — keep this window open while using Claude Code.
echo.
npx @playwright/mcp@0.0.70 --port 8931 --user-data-dir "%USERPROFILE%\.claude\mcp-brave-profile" --executable-path "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

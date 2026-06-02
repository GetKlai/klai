# Klai Shield Extension

Platform-admin-only test extension for Klai Shield.

## Load locally

1. Open `chrome://extensions`.
2. Enable developer mode.
3. Choose **Load unpacked** and select this `klai-shield-extension` folder.
4. Open the Klai Shield side panel.
5. Set API base, for example `http://localhost:8000` or `https://my.getklai.com`.
6. Paste a Shield token created via `POST /api/app/shield/tokens`.

The extension checks prompts in supported browser LLMs and can insert Klai
knowledge context into the active composer.

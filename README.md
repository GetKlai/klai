# Klai

Open-source AI platform — self-hostable, multi-tenant, production-ready.

Klai bundles the tools your team needs to work with AI: a chat interface, a knowledge base, transcription, and document research — all in one platform you control.

## What's included

| Package | Description |
|---------|-------------|
| [`klai-portal/`](klai-portal/) | Web portal — tenant management, knowledge base, transcription, research |
| [`docs/`](docs/) | Documentation site |
| [`deploy/`](deploy/) | Self-hosting stack — Docker Compose, Caddy, Zitadel, LiteLLM, LibreChat |

## Self-hosting

See [deploy/README.md](deploy/README.md) for the full self-hosting guide.

**Quick start:**
```bash
git clone https://github.com/GetKlai/klai.git
cd klai/deploy
cp config.example.env config.env
# Fill in your domain, server IP, and credentials
bash setup.sh
```

## Tech stack

- **Portal backend:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL
- **Portal frontend:** React 19, Vite, TanStack Router, Mantine, Tailwind CSS
- **Auth:** Zitadel (OIDC)
- **LLM proxy:** LiteLLM (OpenAI, Mistral, Ollama, and more)
- **Chat:** LibreChat
- **Reverse proxy:** Caddy with automatic TLS

## Contributing

Pull requests are welcome. For significant changes, please open an issue first.

See [CLAUDE.md](CLAUDE.md) for the codebase guide used by AI-assisted development.

## License

Klai's own code is [MIT](LICENSE).

Third-party dependencies have their own licenses — notably the BlockNote editor
(`@blocknote/*`) which is [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/).
MPL-2.0 is file-level copyleft: it only affects modifications to BlockNote's own
source files, not the larger application. See [NOTICES.md](NOTICES.md) for the full
third-party license inventory.

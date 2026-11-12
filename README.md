# Klai

Open-source AI platform — self-hostable, multi-tenant, production-ready.

Klai bundles the tools your team needs to work with AI: a chat interface, a knowledge base, and transcription, all in one platform you control.

## What's included

| Package | Description |
|---------|-------------|
| [`klai-portal/`](klai-portal/) | Web portal: tenant management, knowledge base, transcription |
| [`docs/`](docs/) | Architecture, runbooks and specifications as markdown (start at [`docs/architecture/README.md`](docs/architecture/README.md)) |
| [`deploy/`](deploy/) | Self-hosting stack: Docker Compose, Caddy, Zitadel, LiteLLM, LibreChat, the knowledge services |

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

- **Portal backend:** Python 3.13, FastAPI, SQLAlchemy, PostgreSQL
- **Portal frontend:** React 19, Vite, TanStack Router, Mantine, Tailwind CSS
- **Other services:** Python 3.12 and FastAPI (knowledge ingest, retrieval, connectors, Scribe, mailer, Slack), Next.js (docs app)
- **Auth:** Zitadel (OIDC), with a cookie-session backend in the portal
- **LLM gateway:** LiteLLM in front of Mistral models; embeddings, reranking and transcription are self-hosted
- **Knowledge:** Qdrant, FalkorDB (graph), PostgreSQL, Gitea
- **Chat:** answered by the portal's partner endpoint; LibreChat is the chat interface for the internal chat
- **Reverse proxy:** Caddy with automatic TLS

How these fit together is described in [`docs/architecture/platform.md`](docs/architecture/platform.md); the index of all architecture documents is [`docs/architecture/README.md`](docs/architecture/README.md).

## Contributing

Pull requests are welcome. For significant changes, please open an issue first.

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Klai production infrastructure
details are intentionally private, but product code, tests, self-hosting
templates, public build workflows, and architecture docs are open for
contribution.

See [CLAUDE.md](CLAUDE.md) for the codebase guide used by AI-assisted development.

## License

Klai's own code is [MIT](LICENSE).

Third-party dependencies have their own licenses — notably the BlockNote editor
(`@blocknote/*`) which is [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/).
MPL-2.0 is file-level copyleft: it only affects modifications to BlockNote's own
source files, not the larger application. See [NOTICES.md](NOTICES.md) for the full
third-party license inventory.

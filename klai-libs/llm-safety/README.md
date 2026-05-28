# klai-llm-safety

Shared deterministic LLM safety policy for Klai services.

This library is intentionally framework-free. FastAPI services and the LiteLLM
hook wrap it with thin local adapters for settings, telemetry, and rollout mode.

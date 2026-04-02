# LLM Selection for Knowledge Graph Construction

> Research: which Mistral model is best suited for populating the Klai knowledge graph via Graphiti?
> Date: 2026-03-31
> Decision: `mistral-small-2603` (Small 4) via LiteLLM alias `klai-large`

## Context

The knowledge-ingest service uses [Graphiti](https://github.com/getzep/graphiti) to build a knowledge graph in FalkorDB. Graphiti performs **multiple LLM calls per episode**:

1. **Entity extraction** — identify persons, organisations, concepts from text
2. **Edge/fact extraction** — determine relationships between entities
3. **Deduplication** — compare new entities with existing graph nodes
4. **Summarization** — create edge summaries

Additionally, the enrichment pipeline (`enrichment.py`) uses an LLM for contextual prefix generation and HyPE question generation per chunk.

Config: `GRAPHITI_LLM_MODEL` in knowledge-ingest settings (`config.py`).

## Model Comparison (March 2026)

| | Nemo (`2407`) | Small 3.2 (`2506`) | Small 4 (`2603`) | Large 3 (`2512`) |
|---|---|---|---|---|
| **Parameters** | 12B dense | 24B dense | 119B MoE (6.5B active) | ~675B MoE (~40B active) |
| **Context window** | 128K | 128K | 256K | 32K |
| **MMLU-Pro** | ~40-50 | ~68 | **78** | ~low 80s |
| **Reasoning mode** | No | No | Yes (configurable per request) | No |
| **Input price** | $0.02/M | $0.10/M | $0.15/M | $0.425/M |
| **Output price** | $0.04/M | $0.30/M | $0.60/M | $1.275/M |
| **Release** | July 2024 | June 2025 | March 2026 | Dec 2025 |
| **Self-host VRAM** | 1x consumer GPU | 1x consumer GPU | 2-4x H100 (240GB) | Not feasible |
| **License** | Apache 2.0 | Apache 2.0 | Apache 2.0 | Apache 2.0 |

### LiteLLM aliases

| Alias | Current mapping | Use for |
|---|---|---|
| `klai-primary` | Mistral Small (EU) | Default for most tasks |
| `klai-fast` | Mistral Nemo (EU) | Fast, lightweight tasks |
| `klai-large` | Mistral Large (EU) | Complex reasoning |

## Analysis per model

### Mistral Nemo (`open-mistral-nemo`) — Not recommended

- 12B model from July 2024 — nearly 2 years old
- Entity extraction requires nuanced understanding of what constitutes an entity, duplicate recognition, and correct relationship classification — a 12B model struggles with this
- Structured output reliability is lower with smaller models — Graphiti expects consistent JSON responses across multiple calls per episode
- Extremely cheap ($0.02/M input) but graph pollution from bad entities costs more to clean up than the savings

### Mistral Small 3.2 (`mistral-small-2506`) — Acceptable baseline

- 24B dense model, fast (~186 tokens/sec), good instruction following
- Adequate for simple entity extraction but may miss subtle relationships
- Best cost/performance ratio for bulk enrichment tasks (contextual prefix, HyPE questions)

### Mistral Small 4 (`mistral-small-2603`) — Recommended

- 119B MoE architecture (6.5B active per token) — significantly more "knowledge" for entity recognition
- Scores 78 on MMLU-Pro, close to Large 3 (low 80s) at a fraction of the cost
- Configurable reasoning mode: `reasoning_effort="none"` for speed, `"high"` for complex extraction
- [Rated 8.4/10](https://awesomeagents.ai/reviews/review-mistral-small-4/) as "strongest open-weight small model" for combined reasoning + coding + vision
- Native JSON schema mode support with high reliability
- 50% more expensive than Small 3.2 on input ($0.15 vs $0.10) but the quality improvement for graph construction justifies the cost
- **Cannot be self-hosted** on current GPU infrastructure (needs 2-4x H100)

### Mistral Large 3 (`mistral-large-2512`) — Overkill for bulk

- Strongest absolute benchmark scores (MMLU-Pro low 80s)
- ~3x more expensive than Small 4 on input, ~2x on output
- Only 32K context window (vs 256K for Small 4)
- Diminishing returns: the quality gap vs Small 4 is small, while cost gap is large
- Consider for one-off quality verification passes over high-value documents

## Decision

**Use `mistral-small-2603` (Small 4) for graph construction** via `GRAPHITI_LLM_MODEL=klai-large`.

Rationale:
1. Quality is critical for knowledge graphs — bad entities pollute the entire graph and are expensive to clean up
2. Small 4 closes most of the quality gap to Large 3 at ~3x lower cost
3. Graphiti does multiple LLM calls per episode — cost scales linearly with document count
4. The reasoning mode provides an upgrade path: start with `reasoning_effort="none"` for speed, switch to `"high"` for important documents

### Cost estimate (bulk backfill)

Assuming ~500 documents, ~3 LLM calls per episode, ~2000 tokens input + ~500 tokens output per call:

| Model | Input cost | Output cost | Total |
|---|---|---|---|
| Nemo | $0.06 | $0.03 | ~$0.09 |
| Small 3.2 | $0.30 | $0.23 | ~$0.53 |
| **Small 4** | **$0.45** | **$0.45** | **~$0.90** |
| Large 3 | $1.28 | $0.96 | ~$2.24 |

At ~$0.90 for 500 documents, Small 4 is affordable even for full backfills.

## Future considerations

- **Hybrid approach**: use Small 4 for bulk ingest, Large 3 for a verification pass on high-value docs
- **Reasoning mode tuning**: test whether `reasoning_effort="high"` meaningfully improves entity quality (at the cost of slower throughput)
- **Self-hosting**: Small 4 requires 2-4x H100 — not feasible on current gpu-01 (single GPU). Monitor if quantized versions (NVFP4, ~66GB) become viable on future hardware

## Sources

- [Mistral Small 4 Review — Awesome Agents](https://awesomeagents.ai/reviews/review-mistral-small-4/)
- [Mistral Small 4 Pricing & Benchmarks — TokenCost](https://tokencost.app/blog/mistral-small-4-pricing)
- [Introducing Mistral Small 4 — Mistral AI](https://mistral.ai/news/mistral-small-4)
- [Mistral Small 4 Docs — Mistral](https://docs.mistral.ai/models/mistral-small-4-0-26-03)
- [Mistral Large 3 Review — Medium](https://medium.com/@leucopsis/mistral-large-3-2512-review-7788c779a5e4)
- [Mistral Small 4 — HuggingFace](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603)
- [Mistral Structured Outputs — Docs](https://docs.mistral.ai/capabilities/structured_output)
- [Mistral NeMo — Mistral AI](https://mistral.ai/news/mistral-nemo)
- [Graphiti — GitHub](https://github.com/getzep/graphiti)

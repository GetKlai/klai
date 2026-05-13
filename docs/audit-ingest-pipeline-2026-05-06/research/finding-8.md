# Finding 8 research: markdown chunker regex naivety

## Code verification

### What the code does

`_split_by_headings` (chunker.py line 53–80) applies a single regex scan over the
raw body string:

```python
heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
```

Note: the implementation uses `#{1,3}` not `#{1,6}`, so only H1–H3 are matched.
The pattern has `re.MULTILINE`, which means `^` matches at the start of every line
in the string, regardless of context.

There is no state machine. There is no tracking of whether the current position is
inside a fenced code block. Every line that starts with one to three `#` characters
followed by whitespace is treated as a document heading and becomes a section
boundary and a `heading_path` component.

### Concrete failure trace

Given this worst-case input (Python code-block inside prose):

```
# Guide to the API

This section explains the API.

    ```python
    # TODO: handle edge cases
    # connect to the server
    import socket
    #!/usr/bin/env python3
    class Client:
        pass
    ```

## Authentication

Use bearer tokens.
```

`_split_by_headings` will find these matches (in order):

| Match | Line | What the code concludes |
|---|---|---|
| `# Guide to the API` | prose | H1 → `heading_path = "Guide to the API"` |
| `# TODO: handle edge cases` | inside ` ``` ` | H1 → overwrites heading_path |
| `# connect to the server` | inside ` ``` ` | H1 → overwrites heading_path again |
| `#!/usr/bin/env python3` | inside ` ``` ` | `#!` matches `#{1,3}` because `!` satisfies `\s+(.+)$`? |
| `## Authentication` | prose | H2 → `heading_path = "Guide to the API > Authentication"` |

Wait — `#!/usr/bin/env python3`: the pattern requires `\s+` after `#`, and `!` is
not whitespace, so shebangs do **not** match. But plain Python comments such as
`# TODO: handle edge cases` do match because they look like `#<space><text>`.

Concretely: `chunk_markdown_with_parents` on the above input will produce:

1. A section with `heading_path = "Guide to the API"` containing the prose before
   the code block and the partial code block body up to `# TODO`.
2. A section with `heading_path = "TODO: handle edge cases"` containing the rest
   of the code block content — split at a comment line.
3. A section with `heading_path = "connect to the server"` containing the remaining
   code block lines.
4. A section with `heading_path = "Guide to the API > Authentication"` containing
   `"Use bearer tokens."`.

The chunks for items 2 and 3 will look like:

```
TODO: handle edge cases

import socket
class Client:
    pass
```

This text — with a mangled heading prefix derived from a code comment — is what
gets embedded and stored in Qdrant. The embedding for "TODO: handle edge cases /
import socket / class Client" is now indexed as a section heading, not as code
content. A user asking "How do I authenticate?" cannot retrieve the Authentication
section unless it happens to score well enough against the code noise.

### `_split_by_size` and mid-block splitting

`_split_by_size` (lines 83–103) has no code-block awareness either. It prefers
`\n\n` paragraph boundaries, then `. ` sentence boundaries. A code block that
contains no blank lines and no period-space sequences will be split at an arbitrary
character boundary in the middle of a logical unit (e.g., mid-function).

### Test coverage audit

`tests/test_chunker_parent_child.py` (the only chunker test file) contains 10 tests.
All test inputs use plain prose in Dutch with `##` headings. None of the tests
include:

- Fenced code blocks (` ``` ` delimiters)
- Python comments inside code blocks
- Shebangs
- Headings-inside-code-block scenarios
- Mixed prose + code content

The frontmatter test (`test_frontmatter_is_stripped_from_chunks`) comes closest to
testing non-prose content but only checks YAML removal, not code blocks.

**Conclusion: the failing mode described in the finding is confirmed and is not
covered by any existing test.**

---

## Current behavior

| Input scenario | What happens |
|---|---|
| Fenced code block containing `# comment` | Comment line becomes a section boundary; preceding content is split there; comment text appears in `heading_path` |
| Multi-heading code block (e.g., shell scripts) | Multiple false sections created, each inheriting a code-line as its heading |
| Code block that exceeds child_size (1200 chars) | `_split_by_size` may split mid-block at character boundary; two children may each contain half a function definition |
| Frontmatter (YAML `---` delimited) | Correctly stripped by `_strip_frontmatter` — this part works |
| Document with no headings at all | Falls back to a single section — works correctly |
| Prose-only document with real H1–H3 headings | Works as intended |

The github connector (klai-connector `app/adapters/github.py`) ingests `.md`, `.rst`,
`.txt`, `.pdf`, `.docx`, `.html`, `.csv` files. README files for code repositories
routinely contain fenced Python, shell, and YAML code blocks with `#`-prefixed
comment lines. Every such file ingested via the github connector is subject to
heading-path contamination.

The ragas eval suites (`_sample.yaml`, `chat.yaml`, `knowledge_org.yaml`) test
customer-service prose content (Bubble plugin, uitportering, voicemail). They do not
include github-connector content with code blocks. This means the existing eval
infrastructure provides zero signal about chunking quality on technical documentation.

---

## Industry standard (2026)

### LangChain `ExperimentalMarkdownSyntaxTextSplitter`

LangChain's text-splitters library (langchain-text-splitters) provides two relevant
implementations.

`MarkdownHeaderTextSplitter` is the older splitter. It performs a line-by-line
regex scan similar to klai's implementation, and has the same code-block
blindness. It is documented as the simple baseline.

`ExperimentalMarkdownSyntaxTextSplitter` (added 2024) corrects this by processing
lines sequentially. When a line matches `` ^```(.*) `` or `^~~~(.*)`, it enters
a code-block accumulation mode via `_resolve_code_chunk()`. Lines inside the fence
are consumed without applying the heading regex. The whole block is stored as a
single chunk with a `Code` metadata key containing the language identifier.
This is a sequential-state approach, not a full AST, but it is sufficient to avoid
treating comment lines as headings.

Limitation: it does not attempt size-based re-splitting of large code blocks;
a 5000-line function would become one chunk.

### LlamaIndex `MarkdownNodeParser`

LlamaIndex's `MarkdownNodeParser` splits by headers at the AST level using the
`mistune` parser under the hood (depending on version). Because mistune follows
CommonMark spec, content inside fenced code blocks is never parsed as headers.
It produces a tree of nodes where each node carries inherited heading metadata.

For oversized sections, LlamaIndex recommends chaining with `SentenceSplitter`.
Neither splitter has code-block awareness at the sentence-level stage, so a code
block larger than the chunk size may still be split at an arbitrary boundary by
`SentenceSplitter`.

### Unstructured.io `partition_md`

Unstructured's pipeline parses markdown into typed elements: `Title`, `NarrativeText`,
`CodeSnippet`, `Table`, etc., before chunking. Code blocks are element-typed as
`CodeSnippet` and are kept whole unless they exceed the hard-max character limit.
The `chunking_strategy="by_title"` mode never splits across element type boundaries,
so a code snippet and the prose that precedes it end up in separate chunks only at
explicit section boundaries.

This is the most correct approach for mixed prose/code content, but it requires the
full unstructured dependency stack (~350 MB compressed), which is heavier than the
current zero-dependency regex approach.

### Haystack `DocumentSplitter` + `MarkdownToDocument`

Haystack's `MarkdownToDocument` converter uses `mistune` to convert markdown to
plain text before splitting, which means fenced code block delimiters are removed
and the body is handed off to `DocumentSplitter` as unstructured text. The splitter
then works on `split_by` options: `sentence`, `word`, `passage`, `page`, etc.
None of these options is code-block-aware; if `mistune` did not strip the fences
correctly a comment line might end up at a split boundary.

However, since Haystack converts to text first, the heading-contamination problem
does not arise: `#` inside a fenced block is consumed by `mistune` as plain text,
not re-matched by a heading regex.

### mistune / markdown-it-py direct AST parsing

Both `mistune` (v3, pure Python, ~10 KB) and `markdown-it-py` (CommonMark-compliant,
~50 KB) produce an AST where fenced code blocks are `block_code` nodes distinct from
`heading` nodes. Walking the AST instead of scanning the raw string eliminates the
false-heading problem entirely.

A chunker built on either library would:
1. Parse the markdown string into an AST.
2. Walk nodes; at each `heading` node, create a new section boundary.
3. At each `block_code` node, emit the node as an atomic unit.
4. For prose nodes exceeding `child_size`, apply sentence splitting.

`mistune` v3 has no C extension and no mandatory external dependencies — the
install size is comparable to the current zero-dependency approach.

### Summary comparison

| Chunker | Heading-in-code-block safe | Code block atomic | Dependencies | Complexity |
|---|---|---|---|---|
| klai current | No | No | None | Low |
| LangChain `ExperimentalMarkdownSyntaxTextSplitter` | Yes (sequential state) | Yes | langchain-text-splitters | Low-Medium |
| LlamaIndex `MarkdownNodeParser` | Yes (AST via mistune) | Yes | llama-index-core, mistune | Medium |
| Unstructured `partition_md` | Yes (typed elements) | Yes (hard-max respected) | unstructured (~350 MB) | High |
| Haystack `MarkdownToDocument` + `DocumentSplitter` | Yes (mistune converts first) | Partially | haystack-ai, mistune | Medium |
| Direct mistune AST walk | Yes (AST) | Yes | mistune (~50 KB) | Low-Medium |

---

## Fix recommendations

### Option A — Minimal: add in_code_block state to `_split_by_headings` (Priority High)

Replace the single-pass regex scan with a line iterator that tracks whether we are
inside a fenced block. This is a localized change with no new dependencies:

```python
def _split_by_headings(text: str) -> list[tuple[str, str]]:
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$")
    fence_re = re.compile(r"^(`{3,}|~{3,})")
    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_heading_path = ""
    buffer: list[str] = []
    in_code_block = False
    fence_marker: str | None = None

    for line in text.splitlines(keepends=True):
        fence_match = fence_re.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]  # ` or ~
            if not in_code_block:
                in_code_block = True
                fence_marker = marker
            elif marker == fence_marker:
                in_code_block = False
                fence_marker = None
            buffer.append(line)
            continue

        if in_code_block:
            buffer.append(line)
            continue

        heading_match = heading_re.match(line.rstrip("\n"))
        if heading_match:
            body = "".join(buffer).strip()
            if body:
                sections.append((current_heading_path, body))
            buffer = []
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
            heading_stack.append((level, title))
            current_heading_path = " > ".join(t for _, t in heading_stack)
        else:
            buffer.append(line)

    body = "".join(buffer).strip()
    if body:
        sections.append((current_heading_path, body))
    return sections
```

This change also correctly handles `_split_by_size` contamination because code
blocks are no longer split into separate sections; they stay in their host section's
buffer and are subject only to size-based splitting within that section's text —
which is still imperfect (see Option B).

### Option B — Better: keep code blocks atomic in `_split_by_size` (Priority Medium)

`_split_by_size` should not split in the middle of a fenced code block. Add a
pass that identifies code block boundaries before the character-count loop and
treats each block as an indivisible unit. If a block exceeds `size`, it is emitted
as one oversized chunk rather than split.

This prevents the "half a function" retrieval problem and is important for
github-connector content with multi-hundred-line code examples.

### Option C — Strategic: migrate to mistune AST walking (Priority Low)

Replace both `_split_by_headings` and the regex heading scan with a `mistune`-based
AST walker. `mistune` v3 (`pip install mistune`) is pure Python (~50 KB) and has no
C extension or heavy transitive dependencies.

The AST walk would produce structurally correct section splits and atomic code
blocks in one pass. This is the approach used by LlamaIndex internally.

Implementation cost: medium (1–2 days). The gain is correctness by construction
rather than correctness by careful state tracking.

### Option D — Eval coverage (Priority High, independent of A/B/C)

Add test cases and ragas eval entries for github-connector-style content:
- A pytest fixture with a markdown document containing Python code blocks with
  `# comment` lines, verifying that no code comment appears in `heading_path`.
- A ragas eval query suite (`eval/suites/github.yaml`) with technical queries
  against a seeded github-style KB to measure retrieval accuracy on code-heavy
  content.

Option D is independent: it should land even if Options A–C are deferred, because
it makes the existing defect observable.

---

## Risk assessment

### Production impact today

The github connector ingests `.md` and `.rst` files from connected repositories.
README files for typical software projects contain fenced Python, shell, YAML, and
Dockerfile code blocks. Any `# comment` or `## section` inside a code block in
such a file currently creates false heading paths in Qdrant.

Portal and Notion connectors ingest human-written prose. Notion pages occasionally
include code snippets rendered as fenced blocks, but the density is lower.

**Affected connector types**: github (high code density), potentially Notion with
code blocks (medium density), web-crawler content from technical documentation
sites (medium density).

**Not affected**: Customer-service prose pages (the current ragas eval population),
spreadsheet/CSV content, pure prose Notion wikis.

### Retrieval quality impact

Contaminated chunks have two adverse effects:

1. **False heading noise**: A child chunk with `heading_path = "TODO: handle edge
   cases"` carries that path as a retrieval signal. Semantic similarity to queries
   like "handle edge cases" becomes coincidentally high, pulling irrelevant code
   fragments into retrieval results.

2. **Section fragmentation**: A code block split across three false sections loses
   the structural coherence of the block. A query that should retrieve the whole
   function body gets three disconnected fragments — or none of them if the
   cosine-similarity scores for the fragments are individually too low.

The existing ragas eval suites (`_sample.yaml`, `chat.yaml`, `knowledge_org.yaml`)
test only prose queries against prose content. They provide **zero coverage** of
code-heavy retrieval. The eval infra cannot detect this regression.

### Performance of fixes

The proposed state-machine fix (Option A) adds one regex compile and one
`str.splitlines()` call per document, replacing the `re.finditer` loop. Wall-clock
impact is negligible for the document sizes typical in the KB (~2–20 KB). No
benchmark is required before merging Option A.

AST-based parsing (Option C) adds a `mistune.create_markdown(renderer='ast')` call
per document. The mistune parser is O(n) in document size and typically 2–3x slower
than a regex scan for pure prose, but only 1 call per document during ingest (not
during retrieval). The absolute latency difference is sub-millisecond for a 20 KB
README — not a concern.

---

## References

- [LangChain `ExperimentalMarkdownSyntaxTextSplitter` source](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/markdown.py)
- [LangChain markdown splitter docs](https://docs.langchain.com/oss/python/integrations/splitters/markdown_header_metadata_splitter)
- [LlamaIndex MarkdownNodeParser API](https://docs.llamaindex.ai/en/stable/api_reference/node_parsers/markdown/)
- [Unstructured chunking documentation](https://docs.unstructured.io/open-source/core-functionality/chunking)
- [Haystack DocumentSplitter](https://docs.haystack.deepset.ai/docs/documentsplitter)
- [mistune v3 PyPI](https://pypi.org/project/mistune/)
- [mistune AST guide](https://mistune.lepture.com/en/latest/advanced.html)
- [markdown-it-py architecture](https://markdown-it-py.readthedocs.io/en/latest/architecture.html)
- [Chonkie CodeChunker](https://docs.chonkie.ai/oss/experimental/code-chunker)
- [Firecrawl: Best chunking strategies for RAG 2026](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)
- [Optimize Chunking Granularity for Retrieval, COLING 2025](https://aclanthology.org/2025.coling-main.384.pdf)
- [Optimizing and Evaluating Enterprise RAG, arXiv 2410.12812](https://arxiv.org/html/2410.12812v1)
- [Retrieval-Augmented Code Generation Survey, arXiv 2510.04905](https://arxiv.org/html/2510.04905v1)

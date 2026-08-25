"""Structure-aware adapter for public JSON feeds.

Supported ``connector.config`` keys:

* ``url`` (required): HTTPS endpoint fetched through the shared SSRF guard.
* ``title``: document title, default ``"JSON feed"``.
* ``group_by``: fields used to group flat record arrays.
* ``record_label_fields``: preferred record label fields.
* ``field_labels``: field-to-display-label overrides.
* ``max_records_per_doc``: batch size without ``group_by`` (default 200).
* ``max_doc_chars``: maximum rendered document size (default 120,000).

Rendered documents are cached by connector id between ``list_documents`` and
``post_sync``.  That interval is one sync lifecycle: ``fetch_document`` only
reads the cache and never downloads the feed again for each emitted document.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from klai_image_storage import PinnedResolverTransport

from app.adapters.base import BaseAdapter, DocumentRef
from app.clients.knowledge_ingest import MAX_INGEST_CONTENT_CHARS
from app.services.url_guard import validate_json_feed_url_strict

# Raw download cap. This bounds memory for the in-flight fetch only; document
# and chunk sizes are bounded separately per rendered group document. The Voys
# PriceRight feed crossed 2 MiB in Aug 2026 (4.9 MB on 2026-08-25) and legitimate
# record feeds grow, so the cap guards against runaway endpoints, not big feeds.
_MAX_FEED_SIZE = 16 * 1024 * 1024
_REQUEST_TIMEOUT = 30.0
_TOTAL_FETCH_TIMEOUT = 60.0
_MIN_KNOWLEDGE_CHARS = 50
_DEFAULT_MAX_RECORDS_PER_DOC = 200
_DEFAULT_MAX_DOC_CHARS = 120_000
# kb_article uses child_size=2000 and overlap=200. The chunker only accepts a
# ``\n\n`` boundary above half the window, so record lines must stay at or below
# roughly child_size / 2 - overlap. Empirically: 0/200 mid-record splits at 900,
# versus 132/200 at 1790.
_MAX_RECORD_LINE_CHARS = 900
_DEFAULT_RECORD_LABEL_FIELDS = ("name", "title", "label", "id")
_PART_SUFFIX_BUDGET = 32


@dataclass(frozen=True)
class _RenderedDocument:
    content: bytes
    extra: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class _RenderedRecord:
    index: int
    label: str
    line: str


def _public_source_url(url: str) -> str:
    """Return a citation-safe origin without path or credential material."""
    parts = urlsplit(url)
    public_netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, public_netloc, "", "", ""))


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "overig"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _is_flat_record_array(parsed: Any) -> bool:
    if not isinstance(parsed, list) or not parsed:
        return False
    for item in parsed:
        if not isinstance(item, dict):
            return False
        non_scalar_count = sum(not _is_scalar(value) for value in item.values())
        if item and non_scalar_count / len(item) > 0.1:
            return False
    return True


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)[1:-1]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not supported")
        return format(value, ".15g")
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _humanize(field_name: str, field_labels: dict[str, str]) -> str:
    return field_labels.get(field_name, field_name.replace("_", " ").strip())


def _group_title(group_by: list[str], values: tuple[str, ...]) -> str:
    if group_by == ["category", "entity", "brand"]:
        return f"{values[0]} — {values[2]} ({values[1]})"
    return " — ".join(values)


class JsonFeedAdapter(BaseAdapter):
    """Fetch and render one HTTPS JSON URL as structure-aware Markdown documents."""

    stale_ref_cleanup_enabled = True

    def __init__(self) -> None:
        self._documents_by_connector: dict[str, dict[str, _RenderedDocument]] = {}
        self._metrics_by_connector: dict[str, dict[str, int]] = {}

    @staticmethod
    def _extract_url(connector: Any) -> str:
        config: dict[str, Any] = connector.config or {}
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(
                "JSON feed connector config missing required field 'url'. "
                "Provide an HTTPS JSON endpoint in connector.config.url."
            )
        return url.strip()

    @staticmethod
    def _config(connector: Any) -> tuple[dict[str, Any], list[str], list[str], dict[str, str], int, int]:
        config: dict[str, Any] = connector.config or {}
        group_by = config.get("group_by", [])
        if not isinstance(group_by, list) or any(not isinstance(field, str) or not field for field in group_by):
            raise ValueError("JSON feed connector config 'group_by' must be a list of non-empty field names")

        label_fields = config.get("record_label_fields", list(_DEFAULT_RECORD_LABEL_FIELDS))
        if not isinstance(label_fields, list) or any(not isinstance(field, str) or not field for field in label_fields):
            raise ValueError("JSON feed connector config 'record_label_fields' must be a list of non-empty field names")

        field_labels = config.get("field_labels", {})
        if not isinstance(field_labels, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in field_labels.items()
        ):
            raise ValueError("JSON feed connector config 'field_labels' must map field names to string labels")

        max_records = config.get("max_records_per_doc", _DEFAULT_MAX_RECORDS_PER_DOC)
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError("JSON feed connector config 'max_records_per_doc' must be a positive integer")

        max_chars = config.get("max_doc_chars", _DEFAULT_MAX_DOC_CHARS)
        if (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or max_chars < 1
            or max_chars > _DEFAULT_MAX_DOC_CHARS
        ):
            raise ValueError(
                f"JSON feed connector config 'max_doc_chars' must be between 1 and {_DEFAULT_MAX_DOC_CHARS}"
            )
        return config, group_by, label_fields, field_labels, max_records, max_chars

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """Fetch once, parse, group, render, and cache all refs for this sync."""
        connector_id = str(connector.id)
        self._documents_by_connector.pop(connector_id, None)
        self._metrics_by_connector.pop(connector_id, None)
        url = self._extract_url(connector)
        config, group_by, label_fields, field_labels, max_records, max_chars = self._config(connector)
        parsed = await self._fetch_json(url, connector_id)

        if _is_flat_record_array(parsed):
            rendered = self._render_flat_records(
                parsed,
                connector_id=connector_id,
                config=config,
                group_by=group_by,
                label_fields=label_fields,
                field_labels=field_labels,
                max_records=max_records,
                max_chars=max_chars,
            )
        else:
            rendered = self._render_nested_json(
                parsed,
                connector_id=connector_id,
                title=self._feed_title(config),
                max_chars=max_chars,
            )

        rendered_chars = sum(len(document.content.decode("utf-8")) for document in rendered.values())
        has_render_errors = any(document.error for document in rendered.values())
        if not rendered or (rendered_chars < _MIN_KNOWLEDGE_CHARS and not has_render_errors):
            raise ValueError(
                f"JSON feed contains too little knowledge (minimum {_MIN_KNOWLEDGE_CHARS} rendered characters)"
            )

        self._documents_by_connector[connector_id] = rendered
        records_total = len(parsed) if _is_flat_record_array(parsed) else 0
        rendered_record_count = sum(
            int(document.extra.get("json_feed_record_count", 0)) for document in rendered.values()
        )
        self._metrics_by_connector[connector_id] = {
            "groups_total": len(rendered),
            "records_total": records_total,
            "duplicates_collapsed": max(records_total - rendered_record_count, 0),
        }
        source_url = _public_source_url(url)
        refs: list[DocumentRef] = []
        for path, document in rendered.items():
            group_slug = path.rsplit("/", 1)[-1]
            refs.append(
                DocumentRef(
                    path=path,
                    ref=f"json-feed:{connector_id}:{group_slug}",
                    size=len(document.content),
                    content_type="kb_article",
                    source_ref=f"json-feed:{connector_id}:{group_slug}",
                    source_url=source_url,
                    extra=document.extra,
                )
            )
        return refs

    def get_sync_metrics(self, connector: Any) -> dict[str, int]:
        """Return feed-shaping counters before ``post_sync`` clears the cache."""
        return dict(self._metrics_by_connector.get(str(connector.id), {}))

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return a rendered document cached for the current sync lifecycle."""
        connector_id = str(connector.id)
        cached = self._documents_by_connector.get(connector_id, {}).get(ref.path)
        if cached is None:
            raise RuntimeError(
                "JSON feed document is not cached for this sync; call list_documents before fetch_document"
            )
        if cached.error:
            raise ValueError(cached.error)
        return cached.content

    async def post_sync(self, connector: Any) -> None:
        """Release rendered feed documents at the end of the sync lifecycle."""
        connector_id = str(connector.id)
        self._documents_by_connector.pop(connector_id, None)
        self._metrics_by_connector.pop(connector_id, None)

    async def _fetch_json(self, url: str, connector_id: str) -> Any:
        validated = await validate_json_feed_url_strict(url, connector_id=connector_id)
        transport = PinnedResolverTransport({validated.hostname: validated.preferred_ip})
        try:
            async with asyncio.timeout(_TOTAL_FETCH_TIMEOUT):
                async with (
                    httpx.AsyncClient(
                        transport=transport,
                        timeout=_REQUEST_TIMEOUT,
                        follow_redirects=False,
                    ) as client,
                    client.stream("GET", url, headers={"Accept": "application/json"}) as response,
                ):
                    if not 200 <= response.status_code < 300:
                        raise ValueError(f"JSON feed returned HTTP {response.status_code}")
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except ValueError:
                            declared_size = 0
                        if declared_size > _MAX_FEED_SIZE:
                            raise ValueError(
                                f"JSON feed is too large: {declared_size} bytes (max {_MAX_FEED_SIZE} bytes)"
                            )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_FEED_SIZE:
                            raise ValueError(f"JSON feed is too large: more than {_MAX_FEED_SIZE} bytes")
                    try:
                        return json.loads(content)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError("JSON feed returned invalid JSON") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"JSON feed request exceeded the {_TOTAL_FETCH_TIMEOUT:g}-second total deadline"
            ) from exc
        except ValueError:
            raise
        except httpx.HTTPError as exc:
            raise RuntimeError(f"JSON feed request failed ({type(exc).__name__})") from exc

    @staticmethod
    def _feed_title(config: dict[str, Any]) -> str:
        title = config.get("title", "JSON feed")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("JSON feed connector config 'title' must be a non-empty string")
        return title.strip()

    def _render_flat_records(
        self,
        records: list[dict[str, Any]],
        *,
        connector_id: str,
        config: dict[str, Any],
        group_by: list[str],
        label_fields: list[str],
        field_labels: dict[str, str],
        max_records: int,
        max_chars: int,
    ) -> dict[str, _RenderedDocument]:
        title = self._feed_title(config)
        if group_by:
            for field in group_by:
                missing = sum(field not in record for record in records)
                if missing > len(records) / 2:
                    raise ValueError(
                        f"JSON feed group_by field '{field}' is absent from {missing}/{len(records)} records (>50%)"
                    )

        if not group_by:
            rendered_records = self._render_records(
                list(enumerate(records)),
                group_title="default batches",
                label_fields=label_fields,
                field_labels=field_labels,
                sort_records=False,
            )
            documents: dict[str, _RenderedDocument] = {}
            for start in range(0, len(rendered_records), max_records):
                batch_number = start // max_records + 1
                slug = f"part-{batch_number:04d}"
                batch = rendered_records[start : start + max_records]
                indexed_batch = [(item.index, records[item.index]) for item in batch]
                documents.update(
                    self._split_record_group(
                        connector_id=connector_id,
                        slug=slug,
                        title=f"{title} — {slug}",
                        schema=self._schema_fields(indexed_batch, field_labels),
                        records=batch,
                        group_values={},
                        max_chars=max_chars,
                    )
                )
            return documents

        groups: OrderedDict[tuple[str, ...], list[tuple[int, dict[str, Any]]]] = OrderedDict()
        for index, record in enumerate(records):
            key = tuple(
                "overig" if field not in record or _is_empty(record[field]) else _display_value(record[field])
                for field in group_by
            )
            groups.setdefault(key, []).append((index, record))

        documents: dict[str, _RenderedDocument] = {}
        for key, indexed_records in groups.items():
            slug = "-".join(_slug(value) for value in key)
            group_title = _group_title(group_by, key)
            group_values = dict(zip(group_by, key, strict=True))
            try:
                rendered_records = self._render_records(
                    indexed_records,
                    group_title=group_title,
                    label_fields=label_fields,
                    field_labels=field_labels,
                    sort_records=True,
                )
                schema = self._schema_fields(indexed_records, field_labels)
                group_documents = self._split_record_group(
                    connector_id=connector_id,
                    slug=slug,
                    title=f"{title} — {group_title}",
                    schema=schema,
                    records=rendered_records,
                    group_values=group_values,
                    max_chars=max_chars,
                )
            except (TypeError, ValueError) as exc:
                error = str(exc)
                if not error.startswith("Failed to render JSON feed group"):
                    error = f"Failed to render JSON feed group '{group_title}': {error}"
                group_documents = {
                    f"json-feed/{connector_id}/{slug}": _RenderedDocument(
                        content=b"",
                        extra={
                            "json_feed_group": group_values,
                            "json_feed_record_count": len(indexed_records),
                        },
                        error=error,
                    )
                }
            overlap = documents.keys() & group_documents.keys()
            if overlap:
                raise ValueError(f"JSON feed group slugs collide: {', '.join(sorted(overlap))}")
            documents.update(group_documents)
        return documents

    @staticmethod
    def _schema_fields(
        indexed_records: list[tuple[int, dict[str, Any]]],
        field_labels: dict[str, str],
    ) -> list[str]:
        fields: list[str] = []
        seen: set[str] = set()
        for _, record in indexed_records:
            for field in record:
                if field not in seen:
                    seen.add(field)
                    fields.append(_humanize(field, field_labels))
        return fields

    @staticmethod
    def _render_records(
        indexed_records: list[tuple[int, dict[str, Any]]],
        *,
        group_title: str,
        label_fields: list[str],
        field_labels: dict[str, str],
        sort_records: bool,
    ) -> list[_RenderedRecord]:
        rendered: list[_RenderedRecord] = []
        for index, record in indexed_records:
            try:
                label_field = next(
                    (field for field in label_fields if field in record and not _is_empty(record[field])),
                    None,
                )
                label = _display_value(record[label_field]) if label_field else str(index + 1)
                pairs = [
                    f"{_humanize(field, field_labels)}: {_display_value(value)}"
                    for field, value in record.items()
                    if field != label_field and not _is_empty(value)
                ]
                line = f"- **{label}**"
                if pairs:
                    line += f" — {'; '.join(pairs)}"
                if len(line) > _MAX_RECORD_LINE_CHARS:
                    raise ValueError(
                        f"rendered record line is {len(line)} characters; maximum is {_MAX_RECORD_LINE_CHARS}"
                    )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Failed to render JSON feed group '{group_title}' record index {index}: {exc}"
                ) from exc
            rendered.append(_RenderedRecord(index=index, label=label, line=line))

        if sort_records:
            rendered.sort(key=lambda item: (item.label.casefold(), item.index))
        unique: list[_RenderedRecord] = []
        seen_lines: set[str] = set()
        for item in rendered:
            if item.line not in seen_lines:
                seen_lines.add(item.line)
                unique.append(item)
        return unique

    def _split_record_group(
        self,
        *,
        connector_id: str,
        slug: str,
        title: str,
        schema: list[str],
        records: list[_RenderedRecord],
        group_values: dict[str, str],
        max_chars: int,
    ) -> dict[str, _RenderedDocument]:
        intro = f"Velden: {', '.join(schema)}"
        full_content = self._compose_document(title, intro, [record.line for record in records])
        if len(full_content) <= max_chars:
            parts = [records]
        else:
            part_limit = max_chars - _PART_SUFFIX_BUDGET
            parts = self._partition_blocks(title, intro, records, part_limit)

        documents: dict[str, _RenderedDocument] = {}
        total = len(parts)
        for part_index, part in enumerate(parts, start=1):
            part_slug = slug if total == 1 else f"{slug}--{part_index}"
            part_title = title if total == 1 else f"{title} — deel {part_index}/{total}"
            content = self._compose_document(part_title, intro, [record.line for record in part])
            self._validate_document_size(content, max_chars)
            path = f"json-feed/{connector_id}/{part_slug}"
            documents[path] = _RenderedDocument(
                content=content.encode("utf-8"),
                extra={
                    "json_feed_group": group_values,
                    "json_feed_record_count": len(part),
                },
            )
        return documents

    @staticmethod
    def _partition_blocks(
        title: str,
        intro: str,
        records: list[_RenderedRecord],
        limit: int,
    ) -> list[list[_RenderedRecord]]:
        parts: list[list[_RenderedRecord]] = []
        current: list[_RenderedRecord] = []
        for record in records:
            candidate = [*current, record]
            content = JsonFeedAdapter._compose_document(title, intro, [item.line for item in candidate])
            if len(content) <= limit:
                current = candidate
                continue
            if not current:
                configured_limit = limit + _PART_SUFFIX_BUDGET
                raise ValueError(
                    f"JSON feed record index {record.index} cannot fit within max_doc_chars={configured_limit}"
                )
            parts.append(current)
            current = [record]
        if current:
            parts.append(current)
        return parts

    @staticmethod
    def _compose_document(title: str, intro: str, blocks: list[str]) -> str:
        components = [f"# {title}", intro, *blocks]
        return "\n\n".join(components)

    @staticmethod
    def _validate_document_size(content: str, max_chars: int) -> None:
        if len(content) > max_chars:
            raise ValueError(f"JSON feed document produces {len(content)} characters; max_doc_chars is {max_chars}")
        if len(content) > MAX_INGEST_CONTENT_CHARS:
            raise ValueError(
                f"JSON feed document produces {len(content)} characters; "
                f"the ingest limit is {MAX_INGEST_CONTENT_CHARS} characters"
            )

    def _render_nested_json(
        self,
        parsed: Any,
        *,
        connector_id: str,
        title: str,
        max_chars: int,
    ) -> dict[str, _RenderedDocument]:
        if isinstance(parsed, dict):
            sections = [(str(key), self._nested_blocks(value, path=str(key), depth=1)) for key, value in parsed.items()]
            sections = [(key, blocks) for key, blocks in sections if blocks]
            combined_blocks = [block for key, section_blocks in sections for block in [f"## {key}", *section_blocks]]
        elif isinstance(parsed, list) and parsed:
            sections = [("document", self._nested_blocks(parsed, path="document", depth=1))]
            combined_blocks = sections[0][1]
        else:
            return {}

        if not sections:
            return {}
        combined_content = "\n\n".join([f"# {title}", *combined_blocks])
        if len(combined_content) <= max_chars:
            self._validate_document_size(combined_content, max_chars)
            return {
                f"json-feed/{connector_id}/document": _RenderedDocument(
                    content=combined_content.encode("utf-8"),
                    extra={},
                )
            }

        documents: dict[str, _RenderedDocument] = {}
        for key, blocks in sections:
            slug = _slug(key)
            section_title = f"{title} — {key}"
            parts = self._partition_text_blocks(section_title, blocks, max_chars)
            total = len(parts)
            for part_index, part in enumerate(parts, start=1):
                part_slug = slug if total == 1 else f"{slug}--{part_index}"
                part_title = section_title if total == 1 else f"{section_title} — deel {part_index}/{total}"
                content = "\n\n".join([f"# {part_title}", *part])
                self._validate_document_size(content, max_chars)
                path = f"json-feed/{connector_id}/{part_slug}"
                if path in documents:
                    raise ValueError(f"JSON feed top-level key slugs collide at '{slug}'")
                documents[path] = _RenderedDocument(content=content.encode("utf-8"), extra={})
        return documents

    def _nested_blocks(self, value: Any, *, path: str, depth: int) -> list[str]:
        if isinstance(value, dict):
            blocks: list[str] = []
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if isinstance(child, dict) and depth == 1:
                    blocks.append(f"## {key}")
                    blocks.extend(self._nested_blocks(child, path=child_path, depth=depth + 1))
                else:
                    blocks.extend(self._nested_blocks(child, path=child_path, depth=depth + 1))
            return blocks
        if isinstance(value, list):
            if all(_is_scalar(item) for item in value):
                return [f"- {path}: {_display_value(value)}"]
            return [f"- {path}[{index}]: {_display_value(item)}" for index, item in enumerate(value)]
        if value is None:
            return [f"- {path}: null"]
        return [f"- {path}: {_display_value(value)}"]

    @staticmethod
    def _partition_text_blocks(title: str, blocks: list[str], max_chars: int) -> list[list[str]]:
        part_limit = max_chars - _PART_SUFFIX_BUDGET
        parts: list[list[str]] = []
        current: list[str] = []
        for block in blocks:
            candidate = [*current, block]
            if len("\n\n".join([f"# {title}", *candidate])) <= part_limit:
                current = candidate
                continue
            if not current:
                raise ValueError(f"JSON feed value at '{block.split(':', 1)[0]}' cannot fit within max_doc_chars")
            parts.append(current)
            current = [block]
        if current:
            parts.append(current)
        return parts

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Omit last_synced_at so reconciliation fetches every manual sync."""
        return {}

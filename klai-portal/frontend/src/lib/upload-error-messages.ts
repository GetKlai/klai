/**
 * Human-readable text for the structured `error_code` / `skipped[].reason`
 * values portal-api returns on the KB file-upload routes.
 *
 * One table, shared by every surface that uploads a file into a knowledge
 * base — the add-source wizard and the per-row "replace file" action — so a
 * new backend reason only has to be translated once.
 *
 * The codes themselves are the contract; see
 * `klai-portal/backend/app/services/file_upload.py` and
 * `app/api/app_knowledge_sources.py`.
 */

import { ApiError } from '@/lib/apiFetch'

export const UPLOAD_FORMATS_LABEL =
  'PDF, Word, Excel, PowerPoint, Markdown, TXT, CSV, JSON, XML, ZIP, TAR'

/** `accept` for adding sources — the full whitelist portal-api classifies. */
export const UPLOAD_ACCEPT_ATTR =
  '.csv,.doc,.docx,.json,.md,.pdf,.pptx,.tar,.txt,.xlsx,.xml,.zip'

/**
 * `accept` for replacing one existing source. Archives are missing on
 * purpose: one archive expands into many sources, so it can add but never
 * replace. `.doc` is missing because portal-api does not extract it yet.
 */
export const UPLOAD_REPLACE_ACCEPT_ATTR =
  '.csv,.docx,.json,.md,.pdf,.pptx,.txt,.xlsx,.xml'

/**
 * Pull the structured `error_code` out of a failed upload call.
 *
 * portal-api answers these routes with `{"error_code": ..., "skipped": [...]}`
 * as the `detail`. Anything else — a network error, an unexpected 500 — falls
 * back to `'default'`, which maps to the generic retry message.
 */
export function uploadErrorCode(err: unknown): string {
  if (!(err instanceof ApiError)) return 'default'
  try {
    const parsed = JSON.parse(err.detail) as { error_code?: string }
    return parsed.error_code ?? 'default'
  } catch {
    return 'default'
  }
}

export function uploadReasonToMessage(reason: string): string {
  switch (reason) {
    case 'unsupported_extension':
      return `Bestandstype niet ondersteund. Toegestane formaten: ${UPLOAD_FORMATS_LABEL}.`
    case 'mime_mismatch':
      return 'Bestand lijkt geen geldig bestandstype voor deze extensie. Controleer of het bestand niet beschadigd is.'
    case 'invalid_text_encoding':
      return 'Tekstbestand kon niet worden gedecodeerd (geen UTF-8 of Windows-1252).'
    case 'empty_content':
      return 'Bestand is leeg.'
    case 'file_too_large':
    case 'oversize':
      return 'Bestand te groot (max 200 MB per bestand).'
    case 'no_file_selected':
    case 'no_files':
      return 'Geen bestand geselecteerd.'
    case 'too_many_files':
      return 'Te veel bestanden geselecteerd (max 10 per upload).'
    case 'phase_pending':
      return 'Dit bestandstype wordt binnenkort ondersteund (.doc volgt).'
    case 'archive_malformed':
      return 'Archief lijkt beschadigd of ongeldig.'
    case 'archive_too_many_entries':
      return 'Archief bevat te veel bestanden (max 50).'
    case 'archive_total_size':
      return 'Archief is uitgepakt te groot (max 500 MB totaal).'
    case 'archive_entry_too_large':
      return 'Een bestand in het archief is te groot (max 50 MB per bestand).'
    case 'archive_compression_ratio':
      return 'Archief lijkt verdacht (compressie-ratio te hoog) - afgewezen.'
    case 'archive_path_traversal':
      return 'Archief bevat een onveilige bestandsnaam (path-traversal).'
    case 'archive_nested':
      return 'Geneste archieven worden niet ondersteund.'
    case 'archive_unsafe_entry':
      return 'Archief bevat een bestand met een niet-toegestaan formaat of type.'
    case 'archive_empty':
      return 'Archief bevat geen bruikbare bestanden.'
    case 'unsupported_archive_type':
      return 'Archieftype wordt niet ondersteund (alleen .zip en .tar).'
    case 'kb_quota_items_exceeded':
      return 'Geen ruimte meer in deze kennisbank.'
    case 'extraction_failed':
      return 'Document kon niet worden verwerkt. Controleer of het bestand niet beschadigd is.'
    case 'docling_timeout':
    case 'docling_unreachable':
      return 'Documentverwerking duurt te lang of is tijdelijk niet bereikbaar. Probeer later opnieuw.'
    case 'kb_or_org_missing':
      return 'Kennisbank niet meer beschikbaar.'
    // Replace-only codes.
    case 'archive_not_replaceable':
      return 'Een archief kan geen bestaande bron vervangen. Pak het uit en vervang het bestand zelf.'
    case 'source_not_replaceable':
      return 'Deze bron komt niet van een geüpload bestand en kan niet worden vervangen.'
    case 'not_your_upload':
      return 'Je kunt alleen bestanden vervangen die je zelf hebt toegevoegd.'
    default:
      return 'Upload mislukt. Probeer opnieuw.'
  }
}

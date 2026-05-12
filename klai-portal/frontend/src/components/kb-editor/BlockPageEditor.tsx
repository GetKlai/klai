import { useEffect, forwardRef, useImperativeHandle } from 'react'
import { useCreateBlockNote, SuggestionMenuController, getDefaultReactSlashMenuItems } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import { BlockNoteSchema, defaultInlineContentSpecs } from '@blocknote/core'
import '@blocknote/mantine/style.css'
import { WikiLink } from '@/components/kb-editor/WikiLink'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { editorLogger } from '@/lib/logger'

// Status -> NL message map for image upload errors. BlockNote surfaces the
// thrown Error message directly to the user (toast/inline), so an opaque
// "500 Internal Server Error" would be unactionable. These cover the three
// real reject paths in POST /kb-images/{kb_slug}; everything else falls back
// to a generic NL message.
const UPLOAD_ERROR_MESSAGES_NL: Record<number, string> = {
  413: 'Afbeelding te groot (max 5 MB).',
  415: 'Dit bestandstype wordt niet ondersteund. Gebruik PNG, JPEG, GIF of WebP.',
  503: 'Afbeeldingen kunnen op dit moment niet worden opgeslagen.',
}

export type BlockPageEditorHandle = {
  getContent: () => string
  insertWikilink: (pageId: string, title: string, icon?: string) => void
}

export type PageIndexEntry = { id: string | null; slug: string; title: string; icon?: string }

const wikilinkSchema = BlockNoteSchema.create({
  inlineContentSpecs: {
    ...defaultInlineContentSpecs,
    wikilink: WikiLink,
  },
})

export const BlockPageEditor = forwardRef<
  BlockPageEditorHandle,
  {
    initialContent: string
    onChange: () => void
    pageIndex?: PageIndexEntry[]
    kbSlug?: string
    currentPageSlug?: string
    onNavigateToPage?: (slug: string) => void
    onRequestWikilinkPicker?: () => void
  }
>(({ initialContent, onChange, pageIndex = [], kbSlug = '', currentPageSlug = '', onNavigateToPage, onRequestWikilinkPicker }, ref) => {
  const editor = useCreateBlockNote({
    schema: wikilinkSchema,
    // SPEC-PORTAL-DOCS-IMAGE-PASTE-001 REQ-6:
    // BlockNote's default paste-flow checks Files BEFORE HTML/Markdown.
    // Configuring `uploadFile` is sufficient for clipboard paste, drag-drop,
    // and slash-menu image insert to all route to the portal-api endpoint —
    // no changes to `pasteHandler` are required.
    uploadFile: async (file: File): Promise<string> => {
      if (!kbSlug) {
        editorLogger.warn('uploadFile invoked without kbSlug — skipping')
        throw new Error('Afbeelding uploaden mislukt: geen kennisbank-context.')
      }
      const fd = new FormData()
      fd.append('file', file)
      try {
        // The kb-images router is mounted at /kb-images (no /api prefix —
        // see klai-portal/backend/app/main.py and SPEC-TI-009's Caddy block).
        // Posting to /api/kb-images/... gives a 404 because the route only
        // exists at /kb-images/...
        const res = await apiFetch<{ url: string; deduplicated: boolean }>(
          `/kb-images/${encodeURIComponent(kbSlug)}`,
          { method: 'POST', body: fd },
        )
        editorLogger.debug('Image uploaded', {
          kbSlug,
          size: file.size,
          type: file.type,
          deduplicated: res.deduplicated,
        })
        return res.url
      } catch (err) {
        if (err instanceof ApiError) {
          editorLogger.warn('Image upload rejected by server', {
            kbSlug,
            status: err.status,
            detail: err.detail,
            size: file.size,
            type: file.type,
          })
          const userMessage =
            UPLOAD_ERROR_MESSAGES_NL[err.status] ??
            'Afbeelding uploaden mislukt. Probeer het opnieuw.'
          throw new Error(userMessage, { cause: err })
        }
        editorLogger.error('Image upload failed', {
          kbSlug,
          err: String(err),
          size: file.size,
          type: file.type,
        })
        throw new Error('Afbeelding uploaden mislukt. Probeer het opnieuw.', { cause: err })
      }
    },
    pasteHandler: ({ event, editor, defaultPasteHandler }) => {
      // When clipboard has both text/html and text/plain (e.g. VS Code copy),
      // BlockNote defaults to HTML which often lacks heading structure.
      // Detect markdown in text/plain and prefer it over lossy HTML.
      const plain = event.clipboardData?.getData('text/plain') ?? ''
      const hasHtml = event.clipboardData?.types.includes('text/html')
      const looksLikeMarkdown = /(?:^|\n) {0,3}#{1,6} ./.test(plain)
      if (hasHtml && looksLikeMarkdown) {
        editor.pasteMarkdown(plain)
        return true
      }
      return defaultPasteHandler()
    },
  })

  useEffect(() => {
    if (!initialContent) {
      editorLogger.debug('initialContent empty on mount')
      return
    }
    // Format detection (newest-first for fast path):
    //   JSON  — saved by current code, lossless native BlockNote format
    //   HTML  — saved by previous code (after wikilink support was added)
    //   Markdown — saved by very first version of the editor
    const trimmed = initialContent.trimStart()
    const format = trimmed.startsWith('[') ? 'json'
                 : trimmed.startsWith('<') ? 'html'
                 : 'markdown'
    editorLogger.debug('Loading content', { format, length: initialContent.length })

    // JSON is fast — parse synchronously for instant LCP.
    // HTML/Markdown parsing is expensive — defer to let the editor shell paint first.
    if (format === 'json') {
      try {
        const blocks = JSON.parse(initialContent) as Parameters<typeof editor.replaceBlocks>[1]
        editor.replaceBlocks(editor.document, blocks)
      } catch (err) {
        editorLogger.error('Failed to parse stored JSON content, falling back to empty', { err })
      }
      return
    }

    const applyLegacyContent = () => {
      const blocks = format === 'html'
        ? editor.tryParseHTMLToBlocks(initialContent)
        : editor.tryParseMarkdownToBlocks(initialContent)
      editor.replaceBlocks(editor.document, blocks)
    }

    // Defer expensive parsing so the editor shell paints immediately.
    // requestIdleCallback lets the browser finish layout first;
    // setTimeout(0) is the fallback for Safari / older browsers.
    if ('requestIdleCallback' in window) {
      const id = requestIdleCallback(applyLegacyContent, { timeout: 150 })
      return () => cancelIdleCallback(id)
    }
    const id = setTimeout(applyLegacyContent, 0)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useImperativeHandle(ref, () => ({
    // Serialize as native BlockNote JSON — lossless (empty paragraphs, custom
    // inline specs like WikiLink with all props) and round-trips without data loss.
    // HTML and Markdown exports are for display/RSS only, not for persistence.
    getContent: () => JSON.stringify(editor.document),
    insertWikilink: (pageId: string, title: string, icon?: string) => {
      editorLogger.debug('Inserting wikilink', { pageId, title, icon })
      editor.focus()
      editor.insertInlineContent([
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { type: "wikilink", props: { pageId, title, kbSlug, icon: icon ?? '' } } as any,
        " ",
      ])
    },
  }))

  return (
    <div
      className="min-h-full mx-auto max-w-[760px] px-12 pt-4 pb-16"
      onClickCapture={(e) => {
        const target = e.target as Element
        const anchor = target.closest('[data-wikilink-page-id]')
        if (anchor) {
          e.preventDefault()
          e.stopPropagation()
          const pageId = anchor.getAttribute('data-wikilink-page-id')
          if (pageId && onNavigateToPage) {
            const page = pageIndex.find((p) => (p.id ?? p.slug) === pageId)
            if (page) onNavigateToPage(page.slug)
          }
        }
      }}
    >
      <BlockNoteView
        editor={editor}
        theme="light"
        className="min-h-full"
        onChange={onChange}
        slashMenu={false}
      >
        <SuggestionMenuController
          triggerCharacter="/"
          getItems={(query) => {
            const defaultItems = getDefaultReactSlashMenuItems(editor)
            const wikilinkItem = {
              title: "Link to page",
              subtext: "Insert a link to another page",
              icon: <span style={{ fontSize: '1.1em' }}>🔗</span>,
              group: "Basic blocks",
              onItemClick: () => {
                onRequestWikilinkPicker?.()
              },
            }
            const allItems = [...defaultItems, wikilinkItem]
            return Promise.resolve(allItems.filter((item) =>
              query === "" || item.title.toLowerCase().includes(query.toLowerCase())
            ))
          }}
        />
        <SuggestionMenuController
          triggerCharacter="["
          getItems={(query) => {
            const search = query.toLowerCase()
            const filtered = pageIndex.filter((p) => {
              if (p.slug === currentPageSlug) return false
              return (
                search === "" ||
                p.title.toLowerCase().includes(search) ||
                p.slug.includes(search)
              )
            })
            return Promise.resolve(filtered.slice(0, 10).map((p) => ({
              title: p.title,
              icon: <span style={{ fontSize: '1em' }}>{p.icon ?? '📄'}</span>,
              onItemClick: () => {
                editor.insertInlineContent([
                  {
                    type: "wikilink",
                    props: { pageId: p.id ?? p.slug, title: p.title, kbSlug, icon: p.icon ?? '' },
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  } as any,
                  " ",
                ])
              },
            })))
          }}
        />
      </BlockNoteView>
    </div>
  )
})
BlockPageEditor.displayName = 'BlockPageEditor'

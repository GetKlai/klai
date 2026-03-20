import { useEffect, forwardRef, useImperativeHandle } from 'react'
import { useCreateBlockNote, SuggestionMenuController, getDefaultReactSlashMenuItems } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import { BlockNoteSchema, defaultInlineContentSpecs } from '@blocknote/core'
import '@blocknote/mantine/style.css'
import { WikiLink } from '@/routes/app/docs/WikiLink'
import { editorLogger } from '@/lib/logger'

export type BlockPageEditorHandle = {
  getMarkdown: () => string
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
  const editor = useCreateBlockNote({ schema: wikilinkSchema })

  useEffect(() => {
    if (!initialContent) {
      editorLogger.debug('initialContent empty on mount')
      return
    }
    // HTML content (saved after wikilink support): parse as HTML so custom
    // inline specs (wikilink) are restored via their parse() method.
    // Legacy markdown content (no leading '<'): fall back to markdown parser.
    const format = initialContent.trimStart().startsWith('<') ? 'html' : 'markdown'
    editorLogger.debug('Loading content', { format, length: initialContent.length })
    const blocks = format === 'html'
      ? editor.tryParseHTMLToBlocks(initialContent)
      : editor.tryParseMarkdownToBlocks(initialContent)
    editor.replaceBlocks(editor.document, blocks)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useImperativeHandle(ref, () => ({
    getMarkdown: () => editor.blocksToHTMLLossy(editor.document),
    insertWikilink: (pageId: string, title: string, icon?: string) => {
      editorLogger.debug('Inserting wikilink', { pageId, title, icon })
      editor.focus()
      editor.insertInlineContent([
        { type: "wikilink", props: { pageId, title, kbSlug, icon: icon ?? '' } } as any,
        " ",
      ])
    },
  }))

  return (
    <div
      className="min-h-full"
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
          getItems={async (query) => {
            const defaultItems = await getDefaultReactSlashMenuItems(editor)
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
            return allItems.filter((item) =>
              query === "" || item.title.toLowerCase().includes(query.toLowerCase())
            )
          }}
        />
        <SuggestionMenuController
          triggerCharacter="["
          getItems={async (query) => {
            const search = query.toLowerCase()
            const filtered = pageIndex.filter((p) => {
              if (p.slug === currentPageSlug) return false
              return (
                search === "" ||
                p.title.toLowerCase().includes(search) ||
                p.slug.includes(search)
              )
            })
            return filtered.slice(0, 10).map((p) => ({
              title: p.title,
              icon: <span style={{ fontSize: '1em' }}>{p.icon ?? '📄'}</span>,
              onItemClick: () => {
                editor.insertInlineContent([
                  {
                    type: "wikilink",
                    props: { pageId: p.id ?? p.slug, title: p.title, kbSlug, icon: p.icon ?? '' },
                  } as any,
                  " ",
                ])
              },
            }))
          }}
        />
      </BlockNoteView>
    </div>
  )
})
BlockPageEditor.displayName = 'BlockPageEditor'

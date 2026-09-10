// Live-preview draft state for the widget admin screen (SPEC-WIDGET-PREVIEW-001).
//
// The preview panel sits next to the tabs and must follow the NOT-YET-SAVED
// form input: an admin typing a name, welcome text, starters, colour or theme
// sees it immediately. Each tab owns its form state locally, so tabs publish
// their preview-relevant fields into this provider while mounted, and clear
// them on unmount - unsaved edits die with their tab exactly like they do in
// the form today, and the preview can never drift ahead of what the form
// actually shows.
//
// Two context values: the publisher (stable identity, consumed by tabs) and
// the resolved values (consumed by the panel). Keeping them apart means a
// keystroke in one tab only re-renders the panel, not the other tabs.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  WIDGET_DEFAULT_PRIMARY_COLOR,
  WIDGET_MAX_CONVERSATION_STARTERS,
} from '@/features/widgets/config/appearance'
import type { WidgetDetailResponse } from './-types'


export type WidgetPreviewScope = 'details' | 'appearance'

// Deliberately primitive-only (starters travel as raw textarea text) so the
// publish effect can compare by value and no fresh array/object identity per
// render can loop the provider.
export interface WidgetPreviewDraft {
  name?: string
  description?: string
  // True while answer-behaviour settings (instructions, template,
  // customer-facing mode, page context) are edited but not saved; the model
  // cannot follow them live, so the panel flags that instead.
  modelBehaviorDirty?: boolean
  headerTitle?: string
  welcome?: string
  starters?: string
  primaryColor?: string
  backgroundColor?: string
  theme?: 'light' | 'dark'
  showSources?: boolean
  showMeta?: boolean
  collectUserInfo?: boolean
  hideDisclaimer?: boolean
  aiDisclosureOverride?: string
  footerText?: string
}

// What the panel renders - every field resolved to the tab draft when one is
// mounted, otherwise to the saved widget.
export interface WidgetPreviewValues {
  botName: string
  headerTitle: string
  description: string
  welcomeMessage: string
  conversationStarters: string[]
  hideDisclaimer: boolean
  primaryColor: string
  backgroundColor?: string
  aiDisclosureOverride?: string
  footerText?: string | null
  theme: 'light' | 'dark'
  showSources: boolean
  showMeta: boolean
  collectUserInfo: boolean
  modelBehaviorDirty: boolean
}

type Scopes = Partial<Record<WidgetPreviewScope, WidgetPreviewDraft>>

const PublishContext = createContext<
  ((scope: WidgetPreviewScope, values: WidgetPreviewDraft | null) => void) | null
>(null)
const ValuesContext = createContext<WidgetPreviewValues | null>(null)

function isSameDraft(a: WidgetPreviewDraft, b: WidgetPreviewDraft): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  for (const key of keys) {
    if (a[key as keyof WidgetPreviewDraft] !== b[key as keyof WidgetPreviewDraft]) {
      return false
    }
  }
  return true
}

function parseStarters(raw: string): string[] {
  return raw.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, WIDGET_MAX_CONVERSATION_STARTERS)
}

function resolvePreview(widget: WidgetDetailResponse, scopes: Scopes): WidgetPreviewValues {
  const config = widget.widget_config
  const details = scopes.details ?? {}
  const appearance = scopes.appearance ?? {}
  const name = details.name ?? widget.name
  return {
    botName: name.trim() || widget.name,
    headerTitle: appearance.headerTitle ?? config.title ?? (name.trim() || widget.name),
    description: details.description ?? widget.description ?? '',
    welcomeMessage: appearance.welcome ?? config.welcome_message,
    conversationStarters:
      appearance.starters !== undefined
        ? parseStarters(appearance.starters)
        : config.conversation_starters ?? [],
    primaryColor:
      appearance.primaryColor || config.primary_color || WIDGET_DEFAULT_PRIMARY_COLOR,
    backgroundColor:
      appearance.backgroundColor || config.css_variables['--klai-background-color'],
    theme: appearance.theme ?? config.theme ?? 'light',
    showSources: appearance.showSources ?? config.show_sources ?? true,
    showMeta: appearance.showMeta ?? config.show_meta ?? false,
    collectUserInfo: appearance.collectUserInfo ?? config.collect_user_info ?? false,
    hideDisclaimer: appearance.hideDisclaimer ?? config.hide_disclaimer ?? false,
    aiDisclosureOverride:
      appearance.aiDisclosureOverride !== undefined
        ? appearance.aiDisclosureOverride
        : config.ai_disclosure_override ?? undefined,
    footerText:
      appearance.footerText !== undefined
        ? appearance.footerText
        : config.footer_text ?? undefined,
    modelBehaviorDirty: details.modelBehaviorDirty ?? false,
  }
}

export function WidgetPreviewProvider({
  widget,
  children,
}: {
  widget: WidgetDetailResponse
  children: ReactNode
}) {
  const [scopes, setScopes] = useState<Scopes>({})

  const publish = useCallback(
    (scope: WidgetPreviewScope, values: WidgetPreviewDraft | null) => {
      setScopes((prev) => {
        if (values === null) {
          if (!(scope in prev)) return prev
          const next = { ...prev }
          delete next[scope]
          return next
        }
        const current = prev[scope]
        if (current && isSameDraft(current, values)) return prev
        return { ...prev, [scope]: values }
      })
    },
    [],
  )

  const preview = useMemo(() => resolvePreview(widget, scopes), [widget, scopes])

  return (
    <PublishContext.Provider value={publish}>
      <ValuesContext.Provider value={preview}>{children}</ValuesContext.Provider>
    </PublishContext.Provider>
  )
}

export function useWidgetPreview(): WidgetPreviewValues {
  const preview = useContext(ValuesContext)
  if (!preview) {
    throw new Error('useWidgetPreview must be used within WidgetPreviewProvider')
  }
  return preview
}

// Publishes the caller's preview-relevant field values for as long as it is
// mounted. Without a provider (e.g. a tab rendered standalone in a test) the
// hook does nothing.
export function usePublishWidgetPreview(
  scope: WidgetPreviewScope,
  values: WidgetPreviewDraft,
): void {
  const publish = useContext(PublishContext)
  const serialized = JSON.stringify(values)
  useEffect(() => {
    if (!publish) return
    publish(scope, JSON.parse(serialized) as WidgetPreviewDraft)
    return () => publish(scope, null)
  }, [publish, scope, serialized])
}

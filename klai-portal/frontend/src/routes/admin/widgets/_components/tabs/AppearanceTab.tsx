import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { WidgetToggleCard } from '@/features/widgets/components/WidgetToggleCard'
import { WidgetStyleOverridesFields } from '@/features/widgets/config/WidgetStyleOverridesFields'
import {
  WIDGET_DEFAULT_PRIMARY_COLOR,
  WIDGET_MAX_CONVERSATION_STARTERS,
} from '@/features/widgets/config/appearance'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse, WidgetConfig } from '../../-types'
import { useUpdateWidget } from '../../-hooks'
import { usePublishWidgetPreview } from '../../-preview'


interface Props {
  widget: WidgetDetailResponse
}

const BACKGROUND_COLOR_VARIABLE = '--klai-background-color'
const THEME_BACKGROUND_COLORS = { light: '#fffef2', dark: '#191918' } as const

// TWD-parity Appearance tab - five sub-sections (Brand & theme,
// Welkomstbericht, Conversation starters, Chat display toggles, Widget
// position). Wired fields land in widget_config JSON; widget client
// renders those it supports (title, welcome, starters, hide_disclaimer
// today; others queued as backend stubs).
export function AppearanceTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const defaultAiDisclosure = m.admin_widgets_ai_disclosure_default({ name: widget.name })
  const defaultFooterText = m.widget_ai_disclaimer()

  const [headerTitle, setHeaderTitle] = useState(config.title ?? widget.name)
  const [welcome, setWelcome] = useState(config.welcome_message)
  const [primaryColor, setPrimaryColor] = useState(config.primary_color || WIDGET_DEFAULT_PRIMARY_COLOR)
  const [cssVariables, setCssVariables] = useState<Record<string, string>>({ ...config.css_variables })
  const backgroundColor = cssVariables[BACKGROUND_COLOR_VARIABLE] || ''
  const [theme, setTheme] = useState<'light' | 'dark'>(config.theme || 'light')
  const [startersRaw, setStartersRaw] = useState((config.conversation_starters ?? []).join('\n'))
  const [showSources, setShowSources] = useState(config.show_sources ?? true)
  const [showMeta, setShowMeta] = useState(config.show_meta ?? false)
  const [collectUserInfo, setCollectUserInfo] = useState(config.collect_user_info ?? false)
  const [aiDisclosureOverride, setAiDisclosureOverride] = useState(
    config.ai_disclosure_override ?? defaultAiDisclosure,
  )
  const [footerText, setFooterText] = useState(config.footer_text ?? defaultFooterText)
  const [widgetPosition, setWidgetPosition] = useState<'left' | 'right'>(config.widget_position || 'right')

  // Everything on this tab is presentation, so the preview panel can follow
  // it verbatim - before save. Starters travel as the raw textarea text.
  usePublishWidgetPreview('appearance', {
    headerTitle,
    welcome,
    starters: startersRaw,
    primaryColor,
    backgroundColor,
    theme,
    showSources,
    showMeta,
    collectUserInfo,
    hideDisclaimer: false,
    aiDisclosureOverride,
    footerText,
    cssVariablesJson: JSON.stringify(cssVariables),
  })

  useEffect(() => {
    setHeaderTitle(config.title ?? widget.name)
    setWelcome(config.welcome_message)
    setPrimaryColor(config.primary_color || WIDGET_DEFAULT_PRIMARY_COLOR)
    setCssVariables({ ...config.css_variables })
    setTheme(config.theme || 'light')
    setStartersRaw((config.conversation_starters ?? []).join('\n'))
    setShowSources(config.show_sources ?? true)
    setShowMeta(config.show_meta ?? false)
    setCollectUserInfo(config.collect_user_info ?? false)
    setAiDisclosureOverride(config.ai_disclosure_override ?? defaultAiDisclosure)
    setFooterText(config.footer_text ?? defaultFooterText)
    setWidgetPosition(config.widget_position || 'right')
  }, [config, defaultAiDisclosure, defaultFooterText, widget.name])

  const starters = startersRaw.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, WIDGET_MAX_CONVERSATION_STARTERS)
  const introductionChanged = aiDisclosureOverride.trim() !== (config.ai_disclosure_override ?? defaultAiDisclosure)
  const footerChanged = footerText.trim() !== (config.footer_text ?? defaultFooterText)

  const isDirty =
    headerTitle !== (config.title ?? widget.name) ||
    welcome.trim() !== config.welcome_message ||
    primaryColor !== (config.primary_color || WIDGET_DEFAULT_PRIMARY_COLOR) ||
    JSON.stringify(cssVariables) !== JSON.stringify(config.css_variables) ||
    theme !== (config.theme || 'light') ||
    JSON.stringify(starters) !== JSON.stringify(config.conversation_starters ?? []) ||
    showSources !== (config.show_sources ?? true) ||
    showMeta !== (config.show_meta ?? false) ||
    collectUserInfo !== (config.collect_user_info ?? false) ||
    introductionChanged ||
    footerChanged ||
    widgetPosition !== (config.widget_position || 'right')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const next: WidgetConfig = {
      ...config,
      title: headerTitle.trim(),
      welcome_message: welcome.trim(),
      primary_color: primaryColor,
      css_variables: cssVariables,
      theme,
      conversation_starters: starters,
      show_sources: showSources,
      show_meta: showMeta,
      collect_user_info: collectUserInfo,
      ...(introductionChanged ? { hide_disclaimer: false, ai_disclosure_override: aiDisclosureOverride.trim() } : {}),
      ...(footerChanged ? { footer_text: footerText.trim() } : {}),
      widget_position: widgetPosition,
    }
    updateMutation.mutate(
      { widget_config: next },
      { onSuccess: () => toast.success(m.admin_shared_success_updated()) },
    )
  }

  function setBackgroundColor(nextValue: string) {
    setCssVariables((current) => {
      const next = { ...current }
      if (nextValue) next[BACKGROUND_COLOR_VARIABLE] = nextValue
      else delete next[BACKGROUND_COLOR_VARIABLE]
      return next
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      {/* Brand & Theme */}
      <section>
        <SectionHeading>{m.admin_widgets_appearance_section_brand()}</SectionHeading>
        <div className="mb-5 max-w-sm space-y-1.5">
          <Label htmlFor="widget-header-title">{m.admin_widgets_widget_title_label()}</Label>
          <p className="text-xs text-gray-600">{m.admin_widgets_widget_title_help()}</p>
          <Input
            id="widget-header-title"
            value={headerTitle}
            onChange={(e) => setHeaderTitle(e.target.value)}
          />
        </div>
        <div className="grid max-w-2xl gap-5 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="widget-primary-color">{m.admin_widgets_brand_color_label()}</Label>
            <p className="text-xs text-gray-600">{m.admin_widgets_brand_color_help()}</p>
            <div className="flex items-center gap-2">
              <Input
                id="widget-primary-color"
                type="color"
                value={primaryColor}
                onChange={(e) => setPrimaryColor(e.target.value)}
                className="h-10 w-12 cursor-pointer rounded-md border border-gray-200 p-1"
              />
              <Input
                value={primaryColor}
                onChange={(e) => setPrimaryColor(e.target.value)}
                pattern="^#[0-9a-fA-F]{6}$"
                placeholder={m.admin_widgets_brand_color_placeholder()}
                className="font-mono text-sm"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="widget-background-color">{m.admin_widgets_background_color_label()}</Label>
            <p className="text-xs text-gray-600">{m.admin_widgets_background_color_help()}</p>
            <div className="flex items-center gap-2">
              <Input
                id="widget-background-color"
                type="color"
                value={backgroundColor || THEME_BACKGROUND_COLORS[theme]}
                onChange={(e) => setBackgroundColor(e.target.value)}
                className="h-10 w-12 cursor-pointer rounded-md border border-gray-200 p-1"
              />
              <Input
                value={backgroundColor || THEME_BACKGROUND_COLORS[theme]}
                onChange={(e) => setBackgroundColor(e.target.value)}
                aria-label={m.admin_widgets_background_color_label()}
                pattern="^#[0-9a-fA-F]{6}$"
                placeholder={m.admin_widgets_background_color_placeholder()}
                className="font-mono text-sm"
              />
            </div>
          </div>
        </div>

        <div className="mt-5 space-y-1.5">
          <Label>{m.admin_widgets_theme_label()}</Label>
          <div role="radiogroup" className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5">
            {(['light', 'dark'] as const).map((t) => (
              <Button
                key={t}
                type="button"
                onClick={() => setTheme(t)}
                role="radio"
                aria-checked={theme === t}
                variant="outline"
                size="sm"
                className={
                  theme === t
                    ? 'rounded-full bg-gray-900 px-4 py-1.5 text-[0.75rem] font-medium text-white transition-colors'
                    : 'rounded-full px-4 py-1.5 text-[0.75rem] text-gray-500 hover:text-gray-900 klai-hover'
                }
              >
                {t === 'light' ? m.admin_widgets_theme_light() : m.admin_widgets_theme_dark()}
              </Button>
            ))}
          </div>
        </div>
        <div className="mt-5 max-w-2xl">
          <WidgetStyleOverridesFields value={cssVariables} onChange={setCssVariables} />
        </div>
      </section>

      {/* Welkomstbericht */}
      <section className="border-t border-gray-200 pt-6">
        <SectionHeading>{m.admin_widgets_appearance_section_welcome()}</SectionHeading>
        <div className="space-y-1.5">
          <Label htmlFor="widget-welcome">{m.admin_widgets_welcome_label()}</Label>
          <p className="text-xs text-gray-600">{m.admin_widgets_welcome_help()}</p>
          <Input
            id="widget-welcome"
            value={welcome}
            onChange={(e) => setWelcome(e.target.value)}
            placeholder={m.admin_widgets_widget_welcome_placeholder()}
          />
        </div>
      </section>

      {/* Conversatie starters */}
      <section className="border-t border-gray-200 pt-6">
        <SectionHeading>{m.admin_widgets_appearance_section_starters()}</SectionHeading>
        <div className="space-y-1.5">
          <Label htmlFor="widget-starters">{m.admin_widgets_widget_starters_label()}</Label>
          <p className="text-xs text-gray-600">{m.admin_widgets_widget_starters_help()}</p>
          <Textarea
            id="widget-starters"
            value={startersRaw}
            onChange={(e) => setStartersRaw(e.target.value)}
            rows={4}
            placeholder={m.admin_widgets_widget_starters_placeholder()}
          />
          <p className="text-xs text-gray-600">{starters.length}/{WIDGET_MAX_CONVERSATION_STARTERS}</p>
        </div>
      </section>

      {/* Chat Weergave */}
      <section className="border-t border-gray-200 pt-6">
        <SectionHeading>{m.admin_widgets_appearance_section_chat_display()}</SectionHeading>
        <div className="space-y-3">
          <WidgetToggleCard id="show-sources" checked={showSources} onChange={setShowSources}
            label={m.admin_widgets_show_sources_label()} help={m.admin_widgets_show_sources_help()} />
          <WidgetToggleCard id="show-meta" checked={showMeta} onChange={setShowMeta}
            label={m.admin_widgets_show_meta_label()} help={m.admin_widgets_show_meta_help()} />
          <WidgetToggleCard id="collect-user-info" checked={collectUserInfo} onChange={setCollectUserInfo}
            label={m.admin_widgets_collect_user_info_label()} help={m.admin_widgets_collect_user_info_help()} />
        </div>
        <div className="mt-3 space-y-1.5">
          <Label htmlFor="widget-ai-disclosure-override">{m.admin_widgets_ai_disclosure_override_label()}</Label>
          <Textarea
            id="widget-ai-disclosure-override"
            aria-describedby="widget-ai-disclosure-help"
            value={aiDisclosureOverride}
            onChange={(e) => setAiDisclosureOverride(e.target.value)}
            maxLength={500}
            rows={3}
          />
          <p id="widget-ai-disclosure-help" className="text-xs text-gray-600">{m.admin_widgets_ai_disclosure_override_help()}</p>
        </div>
        <div className="mt-3 space-y-1.5">
          <Label htmlFor="widget-footer-text">{m.admin_widgets_footer_text_label()}</Label>
          <p className="text-xs text-gray-600">{m.admin_widgets_footer_text_help()}</p>
          <Textarea
            id="widget-footer-text"
            value={footerText}
            onChange={(e) => setFooterText(e.target.value)}
            maxLength={2000}
            rows={3}
            placeholder={m.admin_widgets_footer_text_placeholder()}
          />
        </div>
      </section>

      {/* Widget positie */}
      <section className="border-t border-gray-200 pt-6">
        <SectionHeading>{m.admin_widgets_appearance_section_position()}</SectionHeading>
        <div role="radiogroup" className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5">
          {(['left', 'right'] as const).map((p) => (
            <Button
              key={p}
              type="button"
              onClick={() => setWidgetPosition(p)}
              role="radio"
              aria-checked={widgetPosition === p}
              variant="outline"
              size="sm"
              className={
                widgetPosition === p
                  ? 'rounded-full bg-gray-900 px-4 py-1.5 text-[0.75rem] font-medium text-white transition-colors'
                  : 'rounded-full px-4 py-1.5 text-[0.75rem] text-gray-500 hover:text-gray-900 klai-hover'
              }
            >
              {p === 'left' ? m.admin_widgets_position_left() : m.admin_widgets_position_right()}
            </Button>
          ))}
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error ? updateMutation.error.message : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending || !isDirty}>
          {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-gray-600 mb-3">{children}</h3>
  )
}

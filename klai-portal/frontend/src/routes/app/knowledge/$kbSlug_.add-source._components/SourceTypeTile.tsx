import { Link } from '@tanstack/react-router'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import type { SourceTypeMeta, UploadType } from './source-types'

interface SourceTypeTileProps {
  meta: SourceTypeMeta
  kbSlug: string
  onSelectUpload: (type: UploadType) => void
}

export function SourceTypeTile({ meta, kbSlug, onSelectUpload }: SourceTypeTileProps) {
  const { Icon, label, subtitle, available, group, type, routeTo, disabledHint } = meta

  // Three visual states:
  //   1. available           → full colour, clickable, no pill
  //   2. !available + hint   → muted but clickable (click opens explainer),
  //                            pill shows the hint text (e.g. "Temporarily off")
  //   3. !available + no hint → greyed out, not clickable, generic "Coming soon" pill
  const clickable = available || Boolean(disabledHint)
  const muted = !available
  const pillText = disabledHint
    ? disabledHint()
    : !available
      ? m.knowledge_add_source_coming_soon()
      : null

  const tileClasses = [
    'flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
    clickable
      ? muted
        ? 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50 opacity-70 cursor-pointer'
        : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50 cursor-pointer'
      : 'border-[var(--color-border)] bg-[var(--color-card)] opacity-50 cursor-default',
  ].join(' ')

  const inner = (
    <div className={tileClasses} aria-disabled={!available}>
      <Icon className="h-4 w-4 text-[var(--color-accent)]" />
      <span className="text-sm font-medium text-[var(--color-foreground)]">{label()}</span>
      <span className="text-xs text-[var(--color-muted-foreground)]">{subtitle()}</span>
      {pillText && (
        <span className="inline-flex items-center rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted-foreground)]">
          {pillText}
        </span>
      )}
    </div>
  )

  // Non-clickable, no-hint disabled: static tooltip with generic "Coming soon".
  if (!clickable) {
    return (
      <Tooltip label={m.knowledge_add_source_coming_soon()}>
        <span className="block">{inner}</span>
      </Tooltip>
    )
  }

  // Connector group — deep-link to the add-connector flow.
  if (available && group === 'connector' && routeTo) {
    return (
      <Link to={routeTo(kbSlug)} className="block">
        {inner}
      </Link>
    )
  }

  // Upload group — handled inline by the orchestrator. Also handles the
  // disabled-with-hint case: clicking routes to the same form component,
  // which decides whether to render a form or an explainer based on
  // ``available``.
  return (
    <button
      type="button"
      className="block w-full text-left"
      onClick={() => onSelectUpload(type as UploadType)}
    >
      {inner}
    </button>
  )
}

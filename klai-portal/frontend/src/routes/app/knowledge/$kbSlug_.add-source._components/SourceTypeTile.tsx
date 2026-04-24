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
  const { Icon, label, subtitle, available, group, type, routeTo } = meta

  const tileClasses = [
    'flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
    available
      ? 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50 cursor-pointer'
      : 'border-[var(--color-border)] bg-[var(--color-card)] opacity-50 cursor-default',
  ].join(' ')

  const inner = (
    <div className={tileClasses} aria-disabled={!available}>
      <Icon className="h-4 w-4 text-[var(--color-accent)]" />
      <span className="text-sm font-medium text-[var(--color-foreground)]">{label()}</span>
      <span className="text-xs text-[var(--color-muted-foreground)]">{subtitle()}</span>
      {!available && (
        <span className="inline-flex items-center rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted-foreground)]">
          {m.knowledge_add_source_coming_soon()}
        </span>
      )}
    </div>
  )

  if (!available) {
    return (
      <Tooltip label={m.knowledge_add_source_coming_soon()}>
        <span className="block">{inner}</span>
      </Tooltip>
    )
  }

  if (group === 'connector' && routeTo) {
    return (
      <Link to={routeTo(kbSlug)} className="block">
        {inner}
      </Link>
    )
  }

  // Upload type — handled inline by the orchestrator
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

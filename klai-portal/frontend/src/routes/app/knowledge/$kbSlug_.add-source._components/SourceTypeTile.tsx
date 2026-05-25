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
  const { Icon, label, subtitle, available, badges, group, type, routeTo } = meta

  const tileClasses = [
    'flex min-h-[136px] flex-col items-start gap-2 rounded-lg border p-4 text-left transition-all',
    available
      ? 'border-gray-200 bg-white klai-hover cursor-pointer hover:border-gray-300 hover:bg-gray-50/60'
      : 'border-gray-200 bg-white opacity-50 cursor-default',
  ].join(' ')

  const inner = (
    <div className={tileClasses} aria-disabled={!available}>
      <Icon className="h-4 w-4 text-gray-400" />
      <span className="text-sm font-medium text-gray-900">{label()}</span>
      <span className="text-xs leading-5 text-gray-400">{subtitle()}</span>
      {badges && badges.length > 0 && (
        <span className="mt-auto flex flex-wrap gap-1.5 pt-1">
          {badges.map((badge) => (
            <span
              key={badge}
              className="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] leading-4 text-gray-500"
            >
              {badge}
            </span>
          ))}
        </span>
      )}
      {!available && (
        <span className="inline-flex items-center rounded-full border border-gray-200 px-2 py-0.5 text-xs text-gray-400">
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

  // Upload type - handled inline by the orchestrator
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

import * as m from '@/paraglide/messages'
import { SOURCE_TYPES, type UploadType } from './source-types'
import { SourceTypeTile } from './SourceTypeTile'
import { useEffectiveCapabilities } from '@/hooks/useEffectiveCapabilities'
import { Tooltip } from '@/components/ui/tooltip'
import { Lock } from 'lucide-react'

interface SourceTypeGridProps {
  kbSlug: string
  onSelectUpload: (type: UploadType) => void
}

// SPEC-PORTAL-PROFILES-001 P3.7: Filter connector types by effective capabilities.
// personal and company roles have kb.connectors but not kb.connectors.external.
// Only url/upload types are shown without the external capability.
export function SourceTypeGrid({ kbSlug, onSelectUpload }: SourceTypeGridProps) {
  const capabilities = useEffectiveCapabilities()
  const hasExternalConnectors = capabilities.includes('kb.connectors.external')

  const uploadTypes = SOURCE_TYPES.filter((s) => s.group === 'upload')
  const connectorTypes = SOURCE_TYPES.filter((s) => s.group === 'connector')

  return (
    <div className="space-y-6">
      {/* Upload group */}
      <section>
        <h2 className="text-xs font-medium text-gray-400 mb-3 tracking-wide">
          {m.knowledge_add_source_group_upload()}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {uploadTypes.map((meta) => (
            <SourceTypeTile
              key={meta.type}
              meta={meta}
              kbSlug={kbSlug}
              onSelectUpload={onSelectUpload}
            />
          ))}
        </div>
      </section>

      {/* Connector group */}
      <section>
        <h2 className="text-xs font-medium text-gray-400 mb-3 tracking-wide">
          {m.knowledge_add_source_group_connectors()}
        </h2>
        {!hasExternalConnectors ? (
          <Tooltip label={m.connector_type_locked_tooltip()}>
            <div className="flex items-center gap-2 py-4 text-sm text-[var(--color-muted-foreground)] opacity-60 cursor-default select-none">
              <Lock className="h-4 w-4" />
              {m.connector_type_locked_tooltip()}
            </div>
          </Tooltip>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {connectorTypes.map((meta) => (
              <SourceTypeTile
                key={meta.type}
                meta={meta}
                kbSlug={kbSlug}
                onSelectUpload={onSelectUpload}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

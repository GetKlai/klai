import * as m from '@/paraglide/messages'
import { SOURCE_TYPES, type UploadType } from './source-types'
import { SourceTypeTile } from './SourceTypeTile'

interface SourceTypeGridProps {
  kbSlug: string
  onSelectUpload: (type: UploadType) => void
}

export function SourceTypeGrid({ kbSlug, onSelectUpload }: SourceTypeGridProps) {
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
      </section>
    </div>
  )
}

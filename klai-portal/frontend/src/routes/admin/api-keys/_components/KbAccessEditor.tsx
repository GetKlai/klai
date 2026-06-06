import * as m from '@/paraglide/messages'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import { useOrgKnowledgeBases } from '../-hooks'
import type { AccessLevel, OrgKnowledgeBase } from '../-types'

interface KbAccessRow {
  kb_id: number
  access_level: AccessLevel
}

interface KbAccessEditorProps {
  value: KbAccessRow[]
  onChange: (value: KbAccessRow[]) => void
  knowledgeAppendEnabled: boolean
  disabled?: boolean
  /**
   * Hide the read_write column entirely. Used by widget-type integrations
   * where write access is never allowed (bot can only query).
   */
  hideReadWrite?: boolean
}

function getAccessLevel(
  rows: KbAccessRow[],
  kbId: number,
): AccessLevel {
  const found = rows.find((r) => r.kb_id === kbId)
  return found?.access_level ?? 'none'
}

function setAccessLevel(
  rows: KbAccessRow[],
  kbId: number,
  level: AccessLevel,
): KbAccessRow[] {
  if (level === 'none') {
    return rows.filter((r) => r.kb_id !== kbId)
  }
  const existing = rows.find((r) => r.kb_id === kbId)
  if (existing) {
    return rows.map((r) => (r.kb_id === kbId ? { ...r, access_level: level } : r))
  }
  return [...rows, { kb_id: kbId, access_level: level }]
}

export function KbAccessEditor({
  value,
  onChange,
  knowledgeAppendEnabled,
  disabled = false,
  hideReadWrite = false,
}: KbAccessEditorProps) {
  const { data: kbsData, isLoading } = useOrgKnowledgeBases()
  const kbs: OrgKnowledgeBase[] = kbsData?.knowledge_bases ?? []

  if (isLoading) {
    return <ListLoadingState label={m.admin_shared_loading()} />
  }

  if (kbs.length === 0) {
    return <ListEmptyState title={m.admin_shared_kb_empty()} />
  }

  function handleChange(kbId: number, level: AccessLevel) {
    onChange(setAccessLevel(value, kbId, level))
  }

  return (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.admin_shared_kb_name()}</DataTableHead>
          <DataTableHead align="center" className="w-24">
            {m.admin_api_keys_kb_none()}
          </DataTableHead>
          <DataTableHead align="center" className="w-24">
            {m.admin_shared_kb_read()}
          </DataTableHead>
          {!hideReadWrite && (
            <DataTableHead align="center" className="w-28">
              {m.admin_api_keys_kb_read_write()}
            </DataTableHead>
          )}
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {kbs.map((kb) => {
          const currentLevel = getAccessLevel(value, kb.id)
          return (
            <DataTableRow key={kb.id}>
              <DataTableCell>{kb.name}</DataTableCell>
              <DataTableCell align="center">
                <input
                  type="radio"
                  name={`kb-access-${kb.id}`}
                  checked={currentLevel === 'none'}
                  onChange={() => handleChange(kb.id, 'none')}
                  disabled={disabled}
                  className="accent-[var(--color-accent)]"
                />
              </DataTableCell>
              <DataTableCell align="center">
                <input
                  type="radio"
                  name={`kb-access-${kb.id}`}
                  checked={currentLevel === 'read'}
                  onChange={() => handleChange(kb.id, 'read')}
                  disabled={disabled}
                  className="accent-[var(--color-accent)]"
                />
              </DataTableCell>
              {!hideReadWrite && (
                <DataTableCell align="center">
                  <input
                    type="radio"
                    name={`kb-access-${kb.id}`}
                    checked={currentLevel === 'read_write'}
                    onChange={() => handleChange(kb.id, 'read_write')}
                    disabled={disabled || !knowledgeAppendEnabled}
                    className="accent-[var(--color-accent)]"
                    title={
                      !knowledgeAppendEnabled
                        ? m.admin_api_keys_kb_read_write_disabled_hint()
                        : undefined
                    }
                  />
                </DataTableCell>
              )}
            </DataTableRow>
          )
        })}
      </DataTableBody>
    </DataTable>
  )
}

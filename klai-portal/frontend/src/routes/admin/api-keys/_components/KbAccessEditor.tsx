import { Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
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
  const kbs: OrgKnowledgeBase[] = (kbsData?.knowledge_bases ?? []).filter(
    (kb) => kb.owner_type === 'org',
  )

  if (isLoading) {
    return (
      <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
        <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
        {m.admin_integrations_loading()}
      </p>
    )
  }

  if (kbs.length === 0) {
    return (
      <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
        {m.admin_integrations_kb_empty()}
      </p>
    )
  }

  function handleChange(kbId: number, level: AccessLevel) {
    onChange(setAccessLevel(value, kbId, level))
  }

  return (
    <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
      <thead>
        <tr className="border-b border-[var(--color-border)]">
          <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]">
            {m.admin_integrations_kb_name()}
          </th>
          <th className="py-3 pr-4 text-center text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-24">
            {m.admin_integrations_kb_none()}
          </th>
          <th className="py-3 pr-4 text-center text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-24">
            {m.admin_integrations_kb_read()}
          </th>
          {!hideReadWrite && (
            <th className="py-3 text-center text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-28">
              {m.admin_integrations_kb_read_write()}
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {kbs.map((kb) => {
          const currentLevel = getAccessLevel(value, kb.id)
          return (
            <tr
              key={kb.id}
              className="border-b border-[var(--color-border)] last:border-b-0"
            >
              <td className="py-3 pr-4 align-middle text-[var(--color-foreground)]">
                {kb.name}
              </td>
              <td className="py-3 pr-4 text-center align-middle">
                <input
                  type="radio"
                  name={`kb-access-${kb.id}`}
                  checked={currentLevel === 'none'}
                  onChange={() => handleChange(kb.id, 'none')}
                  disabled={disabled}
                  className="accent-[var(--color-accent)]"
                />
              </td>
              <td className="py-3 pr-4 text-center align-middle">
                <input
                  type="radio"
                  name={`kb-access-${kb.id}`}
                  checked={currentLevel === 'read'}
                  onChange={() => handleChange(kb.id, 'read')}
                  disabled={disabled}
                  className="accent-[var(--color-accent)]"
                />
              </td>
              {!hideReadWrite && (
                <td className="py-3 text-center align-middle">
                  <input
                    type="radio"
                    name={`kb-access-${kb.id}`}
                    checked={currentLevel === 'read_write'}
                    onChange={() => handleChange(kb.id, 'read_write')}
                    disabled={disabled || !knowledgeAppendEnabled}
                    className="accent-[var(--color-accent)]"
                    title={
                      !knowledgeAppendEnabled
                        ? m.admin_integrations_kb_read_write_disabled_hint()
                        : undefined
                    }
                  />
                </td>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

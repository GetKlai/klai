import { Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useOrgKnowledgeBases } from '../-hooks'
import type { OrgKnowledgeBase } from '../-types'

interface Props {
  value: number[]
  onChange: (kbIds: number[]) => void
  disabled?: boolean
}

/**
 * Widget KB selector — multi-select checkbox list.
 * Widgets only have read access, so there's no access level column.
 */
export function KbAccessEditor({ value, onChange, disabled = false }: Props) {
  const { data: kbsData, isLoading } = useOrgKnowledgeBases()
  const kbs: OrgKnowledgeBase[] = (kbsData?.knowledge_bases ?? []).filter(
    (kb) => kb.owner_type === 'org',
  )

  if (isLoading) {
    return (
      <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
        <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
        {m.admin_shared_loading()}
      </p>
    )
  }

  if (kbs.length === 0) {
    return (
      <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
        {m.admin_shared_kb_empty()}
      </p>
    )
  }

  function toggle(kbId: number, checked: boolean) {
    if (checked) {
      onChange([...value, kbId])
    } else {
      onChange(value.filter((id) => id !== kbId))
    }
  }

  return (
    <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
      <thead>
        <tr className="border-b border-[var(--color-border)]">
          <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
            {m.admin_shared_kb_name()}
          </th>
          <th className="py-3 text-center text-xs font-medium text-gray-400 tracking-wide w-24">
            {m.admin_shared_kb_read()}
          </th>
        </tr>
      </thead>
      <tbody>
        {kbs.map((kb) => (
          <tr
            key={kb.id}
            className="border-b border-[var(--color-border)] last:border-b-0"
          >
            <td className="py-3 pr-4 align-middle text-[var(--color-foreground)]">
              {kb.name}
            </td>
            <td className="py-3 text-center align-middle">
              <input
                type="checkbox"
                checked={value.includes(kb.id)}
                onChange={(e) => toggle(kb.id, e.target.checked)}
                disabled={disabled}
                className="accent-[var(--color-accent)]"
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

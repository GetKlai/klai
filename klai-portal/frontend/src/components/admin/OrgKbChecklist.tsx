import { useQuery } from '@tanstack/react-query'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import * as m from '@/paraglide/messages'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'

interface OrgKnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}

function useOrgKnowledgeBases() {
  const auth = useAuth()

  return useQuery({
    queryKey: ['app-knowledge-bases-for-widgets'],
    queryFn: async () =>
      apiFetch<{ knowledge_bases: OrgKnowledgeBase[] }>('/api/app/knowledge-bases?owner_type=org'),
    enabled: auth.isAuthenticated,
  })
}

interface Props {
  value: number[]
  onChange: (kbIds: number[]) => void
  disabled?: boolean
}

/**
 * Org-KB selector - multi-select checkbox list shared by widgets and Slack.
 * Both only get read access to org KBs, so there's no access level column.
 */
export function OrgKbChecklist({ value, onChange, disabled = false }: Props) {
  const { data: kbsData, isLoading } = useOrgKnowledgeBases()
  const kbs: OrgKnowledgeBase[] = (kbsData?.knowledge_bases ?? []).filter(
    (kb) => kb.owner_type === 'org',
  )

  if (isLoading) {
    return <ListLoadingState label={m.admin_shared_loading()} />
  }

  if (kbs.length === 0) {
    return <ListEmptyState title={m.admin_shared_kb_empty()} />
  }

  function toggle(kbId: number, checked: boolean) {
    if (checked) {
      onChange([...value, kbId])
    } else {
      onChange(value.filter((id) => id !== kbId))
    }
  }

  return (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.admin_shared_kb_name()}</DataTableHead>
          <DataTableHead align="center" className="w-24">
            {m.admin_shared_kb_read()}
          </DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {kbs.map((kb) => (
          <DataTableRow key={kb.id}>
            <DataTableCell>{kb.name}</DataTableCell>
            <DataTableCell align="center">
              <Checkbox
                checked={value.includes(kb.id)}
                onChange={(e) => toggle(kb.id, e.target.checked)}
                disabled={disabled}
              />
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
  )
}

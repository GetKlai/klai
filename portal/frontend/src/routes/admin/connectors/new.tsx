import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import { CONNECTOR_API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/connectors/new')({
  component: NewConnectorPage,
})

type ConnectorType = 'github' | 'google_drive' | 'notion' | 'ms_docs'

interface ConnectorTypeOption {
  type: ConnectorType
  label: () => string
  available: boolean
}

interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
  kb_slug: string
}

function NewConnectorPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [selectedType, setSelectedType] = useState<ConnectorType | null>(null)
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('')
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '',
    repo_owner: '',
    repo_name: '',
    branch: 'main',
    path_filter: '',
    kb_slug: '',
  })

  const connectorTypes: ConnectorTypeOption[] = [
    { type: 'github', label: m.admin_connectors_type_github, available: true },
    { type: 'google_drive', label: m.admin_connectors_type_google_drive, available: false },
    { type: 'notion', label: m.admin_connectors_type_notion, available: false },
    { type: 'ms_docs', label: m.admin_connectors_type_ms_docs, available: false },
  ]

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedType) return
      const config: Record<string, unknown> = {}
      if (selectedType === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
        config.kb_slug = githubConfig.kb_slug
      }
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name,
          connector_type: selectedType,
          config,
          schedule: schedule || null,
        }),
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_create({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-connectors'] })
      void navigate({ to: '/admin/connectors' })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createMutation.mutate()
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_connectors_new_heading()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigate({ to: '/admin/connectors' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

      <div className="space-y-6">
        <div className="space-y-2">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">
            {m.admin_connectors_field_type()}
          </p>
          <div className="grid grid-cols-2 gap-3">
            {connectorTypes.map(({ type, label, available }) => (
              <button
                key={type}
                type="button"
                disabled={!available}
                onClick={() => { if (available) setSelectedType(type) }}
                className={[
                  'relative flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
                  !available && 'cursor-not-allowed opacity-50',
                  available && selectedType === type
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                    : available
                      ? 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50'
                      : 'border-[var(--color-border)] bg-[var(--color-card)]',
                ].join(' ')}
              >
                <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                  {label()}
                </span>
                {!available && (
                  <Badge variant="outline" className="text-xs">
                    {m.admin_connectors_coming_soon()}
                  </Badge>
                )}
              </button>
            ))}
          </div>
        </div>

        {selectedType === 'github' && (
          <Card>
            <CardContent className="pt-6">
              <form id="connector-form" onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="name">{m.admin_connectors_field_name()}</Label>
                  <Input
                    id="name"
                    type="text"
                    required
                    placeholder={m.admin_connectors_field_name_placeholder()}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="installation-id">{m.admin_connectors_github_installation_id()}</Label>
                  <Input
                    id="installation-id"
                    type="number"
                    required
                    value={githubConfig.installation_id}
                    onChange={(e) =>
                      setGithubConfig((prev) => ({ ...prev, installation_id: e.target.value }))
                    }
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="repo-owner">{m.admin_connectors_github_repo_owner()}</Label>
                    <Input
                      id="repo-owner"
                      type="text"
                      required
                      value={githubConfig.repo_owner}
                      onChange={(e) =>
                        setGithubConfig((prev) => ({ ...prev, repo_owner: e.target.value }))
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="repo-name">{m.admin_connectors_github_repo_name()}</Label>
                    <Input
                      id="repo-name"
                      type="text"
                      required
                      value={githubConfig.repo_name}
                      onChange={(e) =>
                        setGithubConfig((prev) => ({ ...prev, repo_name: e.target.value }))
                      }
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="branch">{m.admin_connectors_github_branch()}</Label>
                  <Input
                    id="branch"
                    type="text"
                    required
                    placeholder={m.admin_connectors_github_branch_placeholder()}
                    value={githubConfig.branch}
                    onChange={(e) =>
                      setGithubConfig((prev) => ({ ...prev, branch: e.target.value }))
                    }
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="path-filter">{m.admin_connectors_github_path_filter()}</Label>
                  <Input
                    id="path-filter"
                    type="text"
                    placeholder={m.admin_connectors_github_path_filter_placeholder()}
                    value={githubConfig.path_filter}
                    onChange={(e) =>
                      setGithubConfig((prev) => ({ ...prev, path_filter: e.target.value }))
                    }
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="kb-slug">{m.admin_connectors_github_kb_slug()}</Label>
                  <Input
                    id="kb-slug"
                    type="text"
                    required
                    placeholder={m.admin_connectors_github_kb_slug_placeholder()}
                    value={githubConfig.kb_slug}
                    onChange={(e) =>
                      setGithubConfig((prev) => ({ ...prev, kb_slug: e.target.value }))
                    }
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="schedule">{m.admin_connectors_field_schedule()}</Label>
                  <Input
                    id="schedule"
                    type="text"
                    placeholder={m.admin_connectors_field_schedule_placeholder()}
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                  />
                  <p className="text-xs text-[var(--color-muted-foreground)]">
                    {m.admin_connectors_field_schedule_hint()}
                  </p>
                </div>

                {createMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createMutation.error instanceof Error
                      ? createMutation.error.message
                      : m.admin_connectors_error_create_generic()}
                  </p>
                )}

                <div className="pt-2">
                  <Button type="submit" disabled={createMutation.isPending}>
                    {createMutation.isPending
                      ? m.admin_connectors_create_submit_loading()
                      : m.admin_connectors_create_submit()}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

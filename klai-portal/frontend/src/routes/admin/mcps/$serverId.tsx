import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { ArrowLeft, CheckCircle, XCircle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/mcps/$serverId')({
  component: McpEditPage,
})

interface McpServer {
  id: string
  display_name: string
  description: string
  enabled: boolean
  managed: boolean
  required_env_vars: string[]
  configured_env_vars: string[]
}

interface TestResult {
  status: 'ok' | 'error'
  response_time_ms?: number
  tools_available?: string[]
  error?: string
}

// ---------------------------------------------------------------------------
// McpTestButton
// ---------------------------------------------------------------------------

function McpTestButton({ serverId, token }: { serverId: string; token: string }) {
  const [result, setResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)

  async function runTest() {
    setTesting(true)
    setResult(null)
    try {
      const data = await apiFetch<TestResult>(`/api/mcp-servers/${serverId}/test`, token, {
        method: 'POST',
      })
      setResult(data)
    } catch (err) {
      queryLogger.error('MCP test request failed', { serverId, err })
      setResult({ status: 'error', error: 'Request failed' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <Button type="button" variant="outline" size="sm" onClick={() => void runTest()} disabled={testing}>
        {testing ? (
          <>
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            {m.admin_integrations_testing()}
          </>
        ) : (
          m.admin_integrations_test()
        )}
      </Button>
      {result && (
        <span className="flex items-center gap-1 text-sm">
          {result.status === 'ok' ? (
            <>
              <CheckCircle className="h-4 w-4 text-[var(--color-success)]" />
              <span className="text-[var(--color-success)]">
                {m.admin_integrations_test_ok()}
                {result.response_time_ms != null && ` (${result.response_time_ms}ms)`}
              </span>
            </>
          ) : (
            <>
              <XCircle className="h-4 w-4 text-[var(--color-destructive)]" />
              <span className="text-[var(--color-destructive)]">
                {m.admin_integrations_test_error()}
                {result.error && `: ${result.error}`}
              </span>
            </>
          )}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// McpEditPage
// ---------------------------------------------------------------------------

function McpEditPage() {
  const { serverId } = Route.useParams()
  const auth = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = auth.user?.access_token ?? ''

  const { data, isLoading, isError } = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: async () => apiFetch<{ servers: McpServer[] }>('/api/mcp-servers', token),
    enabled: !!token,
  })

  const server = data?.servers.find((s) => s.id === serverId)

  function handleBack() {
    void navigate({ to: '/admin/mcps' })
  }

  // Managed servers cannot be configured — bounce back to the list.
  if (server?.managed) {
    void navigate({ to: '/admin/mcps' })
    return null
  }

  if (isLoading) {
    return (
      <div className="p-6 max-w-lg">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_integrations_loading()}
        </p>
      </div>
    )
  }

  if (isError || !server) {
    return (
      <div className="p-6 max-w-lg space-y-4">
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_integrations_back()}
        </Button>
        <p className="text-sm text-[var(--color-destructive)]">
          {m.admin_integrations_save_error()}
        </p>
      </div>
    )
  }

  return <McpEditForm server={server} token={token} onBack={handleBack} queryClient={queryClient} />
}

// ---------------------------------------------------------------------------
// McpEditForm
// ---------------------------------------------------------------------------

function McpEditForm({
  server,
  token,
  onBack,
  queryClient,
}: {
  server: McpServer
  token: string
  onBack: () => void
  queryClient: ReturnType<typeof useQueryClient>
}) {
  // Secret fields start empty; non-secret fields pre-filled from configured state
  const [envValues, setEnvValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {}
    for (const v of server.required_env_vars) {
      const isSecret = /KEY|SECRET|TOKEN|PASSWORD/i.test(v)
      init[v] = isSecret ? '' : server.configured_env_vars.includes(v) ? '••••••••' : ''
    }
    return init
  })
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      const env: Record<string, string> = {}
      for (const [k, v] of Object.entries(envValues)) {
        if (v && v !== '••••••••') env[k] = v
        else if (server.configured_env_vars.includes(k)) env[k] = v
      }
      return apiFetch(`/api/mcp-servers/${server.id}`, token, {
        method: 'PUT',
        body: JSON.stringify({ enabled: true, env }),
      })
    },
    onSuccess: () => {
      setSuccessMsg(m.admin_integrations_save_success())
      setErrorMsg(null)
      void queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (err) => {
      queryLogger.error('MCP save failed', { serverId: server.id, err })
      setErrorMsg(m.admin_integrations_save_error())
      setSuccessMsg(null)
    },
  })

  const allRequiredFilled = server.required_env_vars.every(
    (v) => server.configured_env_vars.includes(v) || (envValues[v] && envValues[v] !== '••••••••'),
  )

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    mutation.mutate()
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {server.display_name || server.id}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{server.description}</p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_integrations_back()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm font-medium text-[var(--color-foreground)]">
          {m.admin_integrations_required_vars()}
        </p>

        {server.required_env_vars.map((varName) => {
          const isSecret = /KEY|SECRET|TOKEN|PASSWORD/i.test(varName)
          return (
            <div key={varName} className="space-y-1.5">
              <Label htmlFor={`${server.id}-${varName}`}>{varName}</Label>
              <Input
                id={`${server.id}-${varName}`}
                type={isSecret ? 'password' : 'text'}
                placeholder={isSecret ? m.admin_integrations_secret_placeholder() : ''}
                value={envValues[varName] ?? ''}
                onChange={(e) =>
                  setEnvValues((prev) => ({ ...prev, [varName]: e.target.value }))
                }
                autoComplete="off"
              />
            </div>
          )
        })}

        {successMsg && (
          <p className="text-sm text-[var(--color-success)]">{successMsg}</p>
        )}
        {errorMsg && (
          <p className="text-sm text-[var(--color-destructive)]">{errorMsg}</p>
        )}

        <p className="text-xs text-[var(--color-muted-foreground)]">
          {m.admin_integrations_restart_notice()}
        </p>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <Button
            type="submit"
            size="sm"
            disabled={mutation.isPending || !allRequiredFilled}
          >
            {mutation.isPending
              ? m.admin_integrations_saving()
              : m.admin_integrations_save()}
          </Button>
          {server.enabled && server.configured_env_vars.length > 0 && (
            <McpTestButton serverId={server.id} token={token} />
          )}
        </div>
      </form>
    </div>
  )
}

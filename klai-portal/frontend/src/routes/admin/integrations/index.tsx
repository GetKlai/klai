import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { CheckCircle, XCircle, Loader2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/integrations/')({
  component: IntegrationsPage,
})

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface McpServer {
  id: string
  description: string
  enabled: boolean
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
// McpTestButton (T14)
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
// McpServerCard (T14)
// ---------------------------------------------------------------------------

function McpServerCard({
  server,
  token,
  onSaved,
}: {
  server: McpServer
  token: string
  onSaved: () => void
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
  const [enabled, setEnabled] = useState(server.enabled)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      // Only send real values (not placeholder dots for already-configured non-secrets)
      const env: Record<string, string> = {}
      for (const [k, v] of Object.entries(envValues)) {
        if (v && v !== '••••••••') env[k] = v
        else if (server.configured_env_vars.includes(k)) env[k] = v // keep placeholder — backend ignores blanks
      }
      return apiFetch(`/api/mcp-servers/${server.id}`, token, {
        method: 'PUT',
        body: JSON.stringify({ enabled, env }),
      })
    },
    onSuccess: () => {
      setSuccessMsg(m.admin_integrations_save_success())
      setErrorMsg(null)
      onSaved()
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

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="text-base">{server.id}</CardTitle>
            <CardDescription className="mt-0.5">{server.description}</CardDescription>
          </div>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              server.enabled
                ? 'bg-[var(--color-success)]/10 text-[var(--color-success)]'
                : 'bg-[var(--color-muted)] text-[var(--color-muted-foreground)]'
            }`}
          >
            {server.enabled ? m.admin_integrations_enabled() : m.admin_integrations_disabled()}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Env var fields */}
        <div className="space-y-3">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">
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
                  onChange={(e) => setEnvValues((prev) => ({ ...prev, [varName]: e.target.value }))}
                  autoComplete="off"
                />
              </div>
            )
          })}
        </div>

        {/* Enable / disable toggle */}
        <div className="flex items-center gap-2 pt-1">
          <input
            id={`${server.id}-enabled`}
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-accent)]"
          />
          <Label htmlFor={`${server.id}-enabled`} className="cursor-pointer">
            {enabled ? m.admin_integrations_enabled() : m.admin_integrations_enable()}
          </Label>
        </div>

        {/* Feedback */}
        {successMsg && <p className="text-sm text-[var(--color-success)]">{successMsg}</p>}
        {errorMsg && <p className="text-sm text-[var(--color-destructive)]">{errorMsg}</p>}

        {/* Restart notice */}
        <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_integrations_restart_notice()}</p>

        {/* Actions */}
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Button
            type="button"
            size="sm"
            disabled={mutation.isPending || (!allRequiredFilled && enabled)}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? m.admin_integrations_saving() : m.admin_integrations_save()}
          </Button>
          {server.enabled && server.configured_env_vars.length > 0 && (
            <McpTestButton serverId={server.id} token={token} />
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// IntegrationsPage (T12)
// ---------------------------------------------------------------------------

function IntegrationsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const token = auth.user?.access_token ?? ''

  const { data, isLoading, isError } = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: async () => apiFetch<{ servers: McpServer[] }>('/api/mcp-servers', token),
    enabled: !!token,
  })

  function handleSaved() {
    void queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
  }

  return (
    <div className="p-8 max-w-2xl">
      <div className="mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_integrations_title()}
        </h1>
        <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">{m.admin_integrations_subtitle()}</p>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>Laden…</span>
        </div>
      )}

      {isError && (
        <p className="text-sm text-[var(--color-destructive)]">{m.admin_integrations_save_error()}</p>
      )}

      {data && data.servers.length === 0 && (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_integrations_no_servers()}</p>
      )}

      <div className="space-y-4">
        {data?.servers.map((server) => (
          <McpServerCard
            key={server.id}
            server={server}
            token={token}
            onSaved={handleSaved}
          />
        ))}
      </div>
    </div>
  )
}

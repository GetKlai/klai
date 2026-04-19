import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useMemo } from 'react'
import { ArrowLeft, CheckCircle, XCircle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { useMcpServers, mcpServersQueryKey, type McpServer } from './_api'

export const Route = createFileRoute('/admin/mcps/$serverId')({
  component: McpEditPage,
})

const SECRET_VAR_PATTERN = /KEY|SECRET|TOKEN|PASSWORD/i
const SECRET_PLACEHOLDER = '••••••••'

interface TestResult {
  status: 'ok' | 'error'
  response_time_ms?: number
  tools_available?: string[]
  error?: string
}

// ---------------------------------------------------------------------------
// Test connection button — isolated so its local state does not re-trigger
// form re-renders.
// ---------------------------------------------------------------------------

function McpTestButton({ serverId }: { serverId: string }) {
  const [result, setResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)

  async function runTest() {
    setTesting(true)
    setResult(null)
    try {
      const data = await apiFetch<TestResult>(`/api/mcp-servers/${serverId}/test`, {
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
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void runTest()}
        disabled={testing}
      >
        {testing ? (
          <>
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            {m.admin_mcps_testing()}
          </>
        ) : (
          m.admin_mcps_test()
        )}
      </Button>
      {result && (
        <span className="flex items-center gap-1 text-sm">
          {result.status === 'ok' ? (
            <>
              <CheckCircle className="h-4 w-4 text-[var(--color-success)]" />
              <span className="text-[var(--color-success)]">
                {m.admin_mcps_test_ok()}
                {result.response_time_ms != null && ` (${result.response_time_ms}ms)`}
              </span>
            </>
          ) : (
            <>
              <XCircle className="h-4 w-4 text-[var(--color-destructive)]" />
              <span className="text-[var(--color-destructive)]">
                {m.admin_mcps_test_error()}
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
// Initial env-var state: secrets start empty (never leak configured values),
// non-secrets show a placeholder if already configured so the user knows not
// to clear them.
// ---------------------------------------------------------------------------

function buildInitialEnv(server: McpServer): Record<string, string> {
  const init: Record<string, string> = {}
  for (const varName of server.required_env_vars) {
    const isSecret = SECRET_VAR_PATTERN.test(varName)
    const isConfigured = server.configured_env_vars.includes(varName)
    init[varName] = isSecret ? '' : isConfigured ? SECRET_PLACEHOLDER : ''
  }
  return init
}

// ---------------------------------------------------------------------------
// Edit page
// ---------------------------------------------------------------------------

function McpEditPage() {
  const { serverId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useMcpServers()
  const server = data?.servers.find((s) => s.id === serverId)

  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Initialize form state once the server data arrives.
  useEffect(() => {
    if (server) setEnvValues(buildInitialEnv(server))
  }, [server])

  // Managed servers cannot be configured — bounce back to the list.
  useEffect(() => {
    if (server?.managed) void navigate({ to: '/admin/mcps' })
  }, [server?.managed, navigate])

  const mutation = useMutation({
    mutationFn: async () => {
      if (!server) throw new Error('Server not loaded')
      // Skip placeholder values; the backend keeps whatever is already stored
      // for non-secret vars when we send the placeholder string.
      const env: Record<string, string> = {}
      for (const [key, value] of Object.entries(envValues)) {
        if (value && value !== SECRET_PLACEHOLDER) {
          env[key] = value
        } else if (server.configured_env_vars.includes(key)) {
          env[key] = value
        }
      }
      return apiFetch(`/api/mcp-servers/${server.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: true, env }),
      })
    },
    onSuccess: () => {
      setSuccessMsg(m.admin_mcps_save_success())
      setErrorMsg(null)
      void queryClient.invalidateQueries({ queryKey: mcpServersQueryKey })
    },
    onError: (err) => {
      queryLogger.error('MCP save failed', { serverId, err })
      setErrorMsg(m.admin_mcps_save_error())
      setSuccessMsg(null)
    },
  })

  const allRequiredFilled = useMemo(() => {
    if (!server) return false
    return server.required_env_vars.every(
      (varName) =>
        server.configured_env_vars.includes(varName) ||
        (envValues[varName] && envValues[varName] !== SECRET_PLACEHOLDER),
    )
  }, [server, envValues])

  function handleBack() {
    void navigate({ to: '/admin/mcps' })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    mutation.mutate()
  }

  // --- Loading / error / not-found states -----------------------------------

  if (isLoading) {
    return (
      <div className="p-6 max-w-lg">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_mcps_loading()}
        </p>
      </div>
    )
  }

  if (isError || !server) {
    return (
      <div className="p-6 max-w-lg space-y-4">
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_mcps_back()}
        </Button>
        <p className="text-sm text-[var(--color-destructive)]">
          {m.admin_mcps_load_error()}
        </p>
      </div>
    )
  }

  // Render nothing while the managed-redirect effect fires; avoids a flash of
  // the edit form for MCPs the user is not allowed to configure.
  if (server.managed) return null

  // --- Form -----------------------------------------------------------------

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {server.display_name || server.id}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{server.description}</p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_mcps_back()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm font-medium text-[var(--color-foreground)]">
          {m.admin_mcps_required_vars()}
        </p>

        {server.required_env_vars.map((varName) => {
          const isSecret = SECRET_VAR_PATTERN.test(varName)
          const inputId = `${server.id}-${varName}`
          return (
            <div key={varName} className="space-y-1.5">
              <Label htmlFor={inputId}>{varName}</Label>
              <Input
                id={inputId}
                type={isSecret ? 'password' : 'text'}
                placeholder={isSecret ? m.admin_mcps_secret_placeholder() : ''}
                value={envValues[varName] ?? ''}
                onChange={(e) =>
                  setEnvValues((prev) => ({ ...prev, [varName]: e.target.value }))
                }
                autoComplete="off"
              />
            </div>
          )
        })}

        {successMsg && <p className="text-sm text-[var(--color-success)]">{successMsg}</p>}
        {errorMsg && <p className="text-sm text-[var(--color-destructive)]">{errorMsg}</p>}

        <p className="text-xs text-[var(--color-muted-foreground)]">
          {m.admin_mcps_restart_notice()}
        </p>

        <div className="flex flex-wrap items-center gap-3 pt-2">
          <Button type="submit" size="sm" disabled={mutation.isPending || !allRequiredFilled}>
            {mutation.isPending
              ? m.admin_mcps_saving()
              : m.admin_mcps_save()}
          </Button>
          {server.enabled && server.configured_env_vars.length > 0 && (
            <McpTestButton serverId={server.id} />
          )}
        </div>
      </form>
    </div>
  )
}

import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { deprovisionLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/deprovisioning-status')({
  component: DeprovisioningStatusPage,
})

/** Time in ms before we increase poll interval. */
const INTERVAL_FAST_MS = 2000
const INTERVAL_MEDIUM_MS = 5000
const INTERVAL_SLOW_MS = 10000
const MEDIUM_AFTER_MS = 30_000
const SLOW_AFTER_MS = 60_000

/** After 5 minutes without completion, show the timeout warning. */
const TIMEOUT_MS = 5 * 60_000

interface DeprovisionStatus {
  status: 'deprovisioning' | 'failed_deprovisioning' | 'ready' | 'gone'
  // Backend deliberately strips `error` + `attempt` from the owner-facing
  // payload — they may contain internal infra detail. Full fields are
  // available to platform admins via direct DB query / VictoriaLogs.
  last_failure?: {
    step: string
    failed_at?: string
  }
}

// @MX:NOTE: Polls /api/admin/org/me/deprovision-status.
// 404 = org already gone (success). Ready = owner navigated here without active deprovision.
// @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001 Phase 11 R10
export function DeprovisioningStatusPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // Lazy useState initializer keeps Date.now() out of render-pure scope while
  // still capturing the mount-time value once. The ref pattern triggers
  // react-hooks/purity because Date.now() is evaluated during render.
  const [startedAt] = useState<number>(() => Date.now())
  const startedAtRef = useRef<number>(startedAt)
  const [elapsed, setElapsed] = useState(0)

  // Track elapsed time for progressive poll interval and timeout check
  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Date.now() - startedAtRef.current)
    }, 1000)
    return () => clearInterval(id)
  }, [])

  // Compute refetch interval based on elapsed time
  function getRefetchInterval() {
    if (elapsed < MEDIUM_AFTER_MS) return INTERVAL_FAST_MS
    if (elapsed < SLOW_AFTER_MS) return INTERVAL_MEDIUM_MS
    return INTERVAL_SLOW_MS
  }

  const { data, isError } = useQuery<DeprovisionStatus>({
    queryKey: ['deprovision-status'],
    queryFn: async () => {
      try {
        return await apiFetch<DeprovisionStatus>('/api/admin/org/me/deprovision-status')
      } catch (err) {
        // 404 = org row already gone (deprovisioning succeeded)
        if (err instanceof ApiError && err.status === 404) {
          return { status: 'gone' }
        }
        throw err
      }
    },
    refetchInterval: getRefetchInterval(),
    staleTime: 0,
  })

  // Navigate on terminal states
  useEffect(() => {
    if (!data) return

    if (data.status === 'gone') {
      deprovisionLogger.info('Org deprovisioning complete — navigating to tenant-deleted')
      // Clear all cached queries so stale auth data is gone
      queryClient.clear()
      void navigate({ to: '/tenant-deleted' })
      return
    }

    if (data.status === 'ready') {
      deprovisionLogger.info('No active deprovisioning — navigating back to admin')
      void navigate({ to: '/admin' })
      return
    }
  }, [data, navigate, queryClient])

  const isTimeout = elapsed > TIMEOUT_MS && data?.status === 'deprovisioning'

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="w-full max-w-md space-y-6 px-6 text-center">
        {data?.status === 'failed_deprovisioning' ? (
          <FailedView step={data.last_failure?.step} />
        ) : isError ? (
          <ErrorView />
        ) : (
          <PollingView isTimeout={isTimeout} />
        )}
      </div>
    </div>
  )
}

function PollingView({ isTimeout }: { isTimeout: boolean }) {
  return (
    <>
      <Loader2 className="h-10 w-10 animate-spin text-gray-900 mx-auto" />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-gray-900">
          {m.deprovisioning_status_heading()}
        </p>
        <p className="text-sm text-gray-400">
          {isTimeout
            ? m.deprovisioning_status_timeout()
            : m.deprovisioning_status_subtitle()}
        </p>
        {isTimeout && (
          <a
            href="mailto:support@getklai.com"
            className="inline-block mt-2 text-sm font-medium text-[var(--color-rl-accent-dark)] underline"
          >
            {m.deprovisioning_status_failed_support()}
          </a>
        )}
      </div>
    </>
  )
}

function FailedView({ step }: { step?: string }) {
  return (
    <>
      <AlertCircle size={40} className="mx-auto text-[var(--color-destructive)]" strokeWidth={1.5} />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-gray-900">
          {m.deprovisioning_status_failed_heading()}
        </p>
        {step && (
          <p className="text-sm text-gray-400">
            {m.deprovisioning_status_failed_step({ step })}
          </p>
        )}
        <a
          href="mailto:support@getklai.com"
          className="inline-block mt-2 text-sm font-medium text-[var(--color-rl-accent-dark)] underline"
        >
          {m.deprovisioning_status_failed_support()}
        </a>
      </div>
    </>
  )
}

function ErrorView() {
  return (
    <>
      <AlertCircle size={40} className="mx-auto text-[var(--color-destructive)]" strokeWidth={1.5} />
      <div className="space-y-2">
        <p className="text-xl font-semibold text-gray-900">
          {m.deprovisioning_status_failed_heading()}
        </p>
        <p className="text-sm text-gray-400">{m.error_generic()}</p>
        <Button variant="link" size="sm" onClick={() => window.location.reload()}>
          {m.provisioning_error_retry()}
        </Button>
      </div>
    </>
  )
}

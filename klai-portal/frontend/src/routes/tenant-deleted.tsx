import { createFileRoute } from '@tanstack/react-router'
import { CheckCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/tenant-deleted')({
  component: TenantDeletedPage,
})

// @MX:NOTE: Public landing page - no auth required. Works even with stale auth cookies.
// Shown after org hard-delete completes (deprovisioning-status detects 404 and redirects here).
// @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001 Phase 11 R10
function TenantDeletedPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
      <div className="w-full max-w-md space-y-6 px-6 text-center">
        <CheckCircle
          size={40}
          className="mx-auto text-[var(--color-success)]"
          strokeWidth={1.5}
        />
        <div className="space-y-2">
          <p className="text-xl font-semibold text-gray-900">
            {m.tenant_deleted_heading()}
          </p>
          <p className="text-sm text-gray-400">
            {m.tenant_deleted_body()}
          </p>
        </div>
        <a
          href="https://getklai.com"
          className="inline-block text-sm font-medium text-[var(--color-rl-accent-dark)] underline"
        >
          {m.tenant_deleted_back_link()}
        </a>
      </div>
    </div>
  )
}

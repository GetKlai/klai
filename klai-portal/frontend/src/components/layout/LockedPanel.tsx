/**
 * LockedPanel
 *
 * Shared locked-state UI used by ProductGuard and RoleGuard.
 * Renders a centered lock icon + message when a feature is unavailable.
 */
import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import * as m from '@/paraglide/messages'

interface LockedPanelProps {
  /** Primary locked message shown below the icon. */
  message?: ReactNode
  /** Optional secondary CTA line. Defaults to the product_guard_cta string. */
  cta?: ReactNode
}

export function LockedPanel({ message, cta }: LockedPanelProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-muted)]">
        <Lock className="h-6 w-6 text-gray-400" />
      </div>
      <div className="space-y-1">
        {message && (
          <p className="text-sm text-gray-400">{message}</p>
        )}
      </div>
      <p className="text-xs text-gray-400">
        {cta ?? m.product_guard_cta()}
      </p>
    </div>
  )
}

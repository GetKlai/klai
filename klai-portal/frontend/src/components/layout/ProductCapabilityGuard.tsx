/**
 * ProductCapabilityGuard
 *
 * SPEC-PORTAL-UNIFY-KB-001 — D4: missing capabilities are rendered grayed,
 * not hidden, not clickable, with a tooltip.  No lock icon, no upgrade CTA.
 *
 * Usage:
 *   <ProductCapabilityGuard capability="kb.connectors" tooltip="Beschikbaar op Klai Knowledge.">
 *     <TabLink … />
 *   </ProductCapabilityGuard>
 *
 * When the user lacks the capability:
 *   - Renders a <span> wrapper with opacity-50, cursor-default, pointer-events-none
 *   - Shows a Tooltip on hover via the existing components/ui/tooltip
 *   - Sets aria-disabled="true" for accessibility
 *
 * When fallback="hidden": renders nothing (rare; for elements that should not
 * even be discoverable, e.g. deeply nested admin actions).
 */
import type { ReactNode } from 'react'
import { Tooltip } from '@/components/ui/tooltip'
import { useCurrentUser } from '@/hooks/useCurrentUser'

interface ProductCapabilityGuardProps {
  /** Capability string, e.g. "kb.connectors" */
  capability: string
  children: ReactNode
  /** Tooltip text shown when grayed out. Defaults to the i18n key value. */
  tooltip?: string
  /** "grayed" (default): visible but disabled. "hidden": render nothing. */
  fallback?: 'grayed' | 'hidden'
}

export function ProductCapabilityGuard({
  capability,
  children,
  tooltip,
  fallback = 'grayed',
}: ProductCapabilityGuardProps) {
  const { user } = useCurrentUser()

  // While user data is loading, optimistically render children (avoids flash).
  if (!user) return <>{children}</>

  const hasCapability = user.hasCapability(capability)
  if (hasCapability) return <>{children}</>

  if (fallback === 'hidden') return null

  const tooltipText = tooltip ?? 'Beschikbaar op Klai Knowledge.'

  return (
    <Tooltip label={tooltipText}>
      <span
        className="opacity-50 cursor-default pointer-events-none select-none"
        aria-disabled="true"
        data-capability-guard={capability}
      >
        {children}
      </span>
    </Tooltip>
  )
}

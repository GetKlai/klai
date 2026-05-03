import type { ReactNode } from 'react'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { LockedPanel } from '@/components/layout/LockedPanel'

interface ProductGuardProps {
  product: string
  children: ReactNode
}

export function ProductGuard({ product, children }: ProductGuardProps) {
  const { user } = useCurrentUser()
  if (user?.isAdmin || user?.products.includes(product)) {
    return <>{children}</>
  }

  return (
    <LockedPanel
      message={m.product_guard_description()}
      cta={m.product_guard_cta()}
    />
  )
}

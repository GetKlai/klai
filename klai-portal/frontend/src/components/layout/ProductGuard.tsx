import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { STORAGE_KEYS } from '@/lib/storage'

function getStoredProducts(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEYS.products) ?? '[]') as string[]
  } catch {
    return []
  }
}

function isAdmin(): boolean {
  return sessionStorage.getItem(STORAGE_KEYS.isAdmin) === 'true'
}

interface ProductGuardProps {
  product: string
  children: ReactNode
}

export function ProductGuard({ product, children }: ProductGuardProps) {
  if (isAdmin() || getStoredProducts().includes(product)) {
    return <>{children}</>
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-muted)]">
        <Lock className="h-6 w-6 text-[var(--color-muted-foreground)]" />
      </div>
      <div className="space-y-1">
        <h2 className="text-base font-semibold text-[var(--color-foreground)]">
          {m.product_guard_title()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.product_guard_description()}
        </p>
      </div>
      <p className="text-xs text-[var(--color-muted-foreground)]">
        {m.product_guard_cta()}
      </p>
    </div>
  )
}

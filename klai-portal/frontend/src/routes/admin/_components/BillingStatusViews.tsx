import { AlertCircle, CheckCircle, CreditCard, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'

export function BillingFreeView() {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-rl-accent)]/10 p-4">
          <CheckCircle size={24} className="text-[var(--color-rl-accent)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-foreground)]">{m.admin_billing_free_title()}</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            {m.admin_billing_free_description()}
          </p>
        </div>
        <Badge variant="secondary">{m.admin_billing_free_badge()}</Badge>
      </CardContent>
    </Card>
  )
}

export function BillingMandateRequestedView() {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-rl-accent)]/10 p-4">
          <CreditCard size={24} className="text-[var(--color-rl-accent)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-foreground)]">{m.admin_billing_mandate_title()}</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            {m.admin_billing_mandate_description()}
          </p>
        </div>
        <Badge variant="secondary">{m.admin_billing_mandate_badge()}</Badge>
      </CardContent>
    </Card>
  )
}

export function BillingPaymentFailedView({ onRetry }: { onRetry: () => void }) {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-destructive-bg)] p-4">
          <AlertCircle size={24} className="text-[var(--color-destructive)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-foreground)]">{m.admin_billing_payment_failed_title()}</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            {m.admin_billing_payment_failed_description()}
          </p>
        </div>
        <Badge variant="destructive">{m.admin_billing_payment_failed_badge()}</Badge>
        <Button onClick={onRetry} className="gap-2">
          <CreditCard size={16} />
          {m.admin_billing_payment_failed_retry()}
        </Button>
      </CardContent>
    </Card>
  )
}

export function BillingCancelledView({ onReactivate }: { onReactivate: () => void }) {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-rl-cream)] p-4">
          <XCircle size={24} className="text-[var(--color-muted-foreground)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-foreground)]">{m.admin_billing_cancelled_title()}</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            {m.admin_billing_cancelled_description()}
          </p>
        </div>
        <Badge variant="secondary">{m.admin_billing_cancelled_badge()}</Badge>
        <Button variant="outline" onClick={onReactivate} className="gap-2">
          <CheckCircle size={16} />
          {m.admin_billing_cancelled_reactivate()}
        </Button>
      </CardContent>
    </Card>
  )
}

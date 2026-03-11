import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/logged-out')({
  component: LoggedOutPage,
})

function LoggedOutPage() {
  const auth = useAuth()

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      <div className="flex w-full flex-col items-center justify-center px-8">
        <div className="w-full max-w-sm space-y-8">
          <img src="/klai-logo.svg" alt="Klai" className="h-7 w-auto block" />

          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.logged_out_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.logged_out_body()}
            </p>
          </div>

          <Button
            onClick={() => auth.signinRedirect()}
            size="lg"
            className="w-full gap-3"
          >
            {m.logged_out_cta()}
            <ArrowRight size={16} />
          </Button>
        </div>
      </div>
    </div>
  )
}

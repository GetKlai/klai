import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

export const Route = createFileRoute('/no-account')({
  component: NoAccountPage,
})

function NoAccountPage() {
  useLocale()
  const navigate = useNavigate()

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.no_account_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.no_account_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.no_account_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.no_account_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.no_account_body()}
        </p>
      </div>

      <Button
        onClick={() => { void navigate({ to: '/join-request' }) }}
        size="lg"
        className="w-full gap-3"
      >
        {m.no_account_request_access()}
        <ArrowRight size={16} />
      </Button>

      <Button
        variant="ghost"
        onClick={() => { window.location.replace('/') }}
        size="lg"
        className="w-full"
      >
        {m.no_account_cta()}
      </Button>
    </AuthPageLayout>
  )
}

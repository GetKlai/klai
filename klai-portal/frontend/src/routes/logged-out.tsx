import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

export const Route = createFileRoute('/logged-out')({
  component: LoggedOutPage,
})

function LoggedOutPage() {
  useLocale()
  const auth = useAuth()

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.logged_out_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.logged_out_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.logged_out_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
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
    </AuthPageLayout>
  )
}

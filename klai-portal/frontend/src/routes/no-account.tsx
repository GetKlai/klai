import { createFileRoute, Link } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import { LogIn, UserPlus } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

type SearchParams = {
  email: string
  first_name: string
  last_name: string
}

export const Route = createFileRoute('/no-account')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    email: typeof search.email === 'string' ? search.email : '',
    first_name: typeof search.first_name === 'string' ? search.first_name : '',
    last_name: typeof search.last_name === 'string' ? search.last_name : '',
  }),
  component: NoAccountPage,
})

function NoAccountPage() {
  const { locale } = useLocale()
  const { email, first_name, last_name } = Route.useSearch()
  const canContinueSocialSignup = Boolean(email)

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
        <h2 className="text-xl font-semibold text-gray-900">
          {m.no_account_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.no_account_body()}
        </p>
      </div>

      {canContinueSocialSignup ? (
        <Button asChild size="lg" className="w-full gap-3">
          <Link
            to="/$locale/signup/social"
            params={{ locale }}
            search={{ email, first_name, last_name }}
          >
            {m.no_account_request_access()}
            <UserPlus size={16} />
          </Link>
        </Button>
      ) : (
        <Button asChild size="lg" className="w-full gap-3">
          <Link to="/$locale/signup" params={{ locale }}>
            {m.no_account_request_access()}
            <UserPlus size={16} />
          </Link>
        </Button>
      )}

      <Button asChild variant="ghost" size="lg" className="w-full gap-3">
        <a href="/">
          {m.no_account_cta()}
          <LogIn size={16} />
        </a>
      </Button>
    </AuthPageLayout>
  )
}

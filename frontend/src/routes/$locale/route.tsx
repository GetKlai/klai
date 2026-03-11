import { createFileRoute, Outlet, redirect } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useLocale, type Locale } from '@/lib/locale'

const VALID_LOCALES = ['nl', 'en'] as const

export const Route = createFileRoute('/$locale')({
  beforeLoad: ({ params }) => {
    if (!(VALID_LOCALES as readonly string[]).includes(params.locale)) {
      throw redirect({ to: '/$locale/signup', params: { locale: 'nl' } })
    }
  },
  component: LocaleLayout,
})

function LocaleLayout() {
  const { locale: paramLocale } = Route.useParams()
  const { locale, switchLocale } = useLocale()

  useEffect(() => {
    if ((paramLocale === 'nl' || paramLocale === 'en') && paramLocale !== locale) {
      switchLocale(paramLocale as Locale)
    }
  }, [paramLocale])

  return <Outlet />
}

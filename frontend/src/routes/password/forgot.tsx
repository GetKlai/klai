import { createFileRoute, redirect } from '@tanstack/react-router'

type SearchParams = {
  email?: string
}

// Canonical URL is /$locale/password/forgot. This route redirects bare
// /password/forgot to the correct locale version, preserving the email param.
export const Route = createFileRoute('/password/forgot')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    email: typeof search.email === 'string' ? search.email : undefined,
  }),
  beforeLoad: ({ search }) => {
    const saved = localStorage.getItem('klai-locale')
    const locale = saved === 'nl' || saved === 'en' ? saved : 'nl'
    throw redirect({
      to: '/$locale/password/forgot',
      params: { locale },
      search: search.email ? { email: search.email } : {},
    })
  },
  component: () => null,
})

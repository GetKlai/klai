import { createFileRoute, redirect } from '@tanstack/react-router'

// Canonical signup URLs are /$locale/signup (e.g. /nl/signup, /en/signup).
// This route redirects bare /signup to the correct locale version.
export const Route = createFileRoute('/signup')({
  beforeLoad: () => {
    const saved = localStorage.getItem('klai-locale')
    const locale = saved === 'nl' || saved === 'en' ? saved : 'nl'
    throw redirect({ to: '/$locale/signup', params: { locale } })
  },
  component: () => null,
})

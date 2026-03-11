import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/account')({
  component: AccountPage,
})

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

function AccountPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const { locale, switchLocale } = useLocale()

  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>(locale)

  // Fetch current user's preferred language from the portal DB
  const { data: meData } = useQuery({
    queryKey: ['me-language', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return null
      return res.json() as Promise<{ preferred_language?: 'nl' | 'en' }>
    },
    enabled: !!token,
  })

  useEffect(() => {
    if (meData?.preferred_language) {
      setSelectedLang(meData.preferred_language)
    }
  }, [meData])

  const saveMutation = useMutation({
    mutationFn: async (preferred_language: 'nl' | 'en') => {
      const res = await fetch(`${API_BASE}/api/me/language`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ preferred_language }),
      })
      if (!res.ok) throw new Error(m.account_error_save())
      return preferred_language
    },
    onSuccess: (lang) => {
      switchLocale(lang)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  const name = auth.user?.profile?.name ?? auth.user?.profile?.preferred_username ?? ''
  const email = auth.user?.profile?.email ?? ''

  return (
    <div className="p-8 space-y-6 max-w-2xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.account_heading()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.account_subtitle()}
        </p>
      </div>

      {/* Profile info (display only) */}
      <Card>
        <CardContent className="pt-6">
          <dl className="space-y-3">
            {name && (
              <div className="flex gap-4">
                <dt className="w-32 shrink-0 text-sm text-[var(--color-muted-foreground)]">Naam</dt>
                <dd className="text-sm font-medium text-[var(--color-purple-deep)]">{name}</dd>
              </div>
            )}
            {email && (
              <div className="flex gap-4">
                <dt className="w-32 shrink-0 text-sm text-[var(--color-muted-foreground)]">E-mail</dt>
                <dd className="text-sm font-medium text-[var(--color-purple-deep)]">{email}</dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Language preference */}
      <Card>
        <CardHeader>
          <CardTitle>{m.account_language_title()}</CardTitle>
          <CardDescription>
            {m.account_language_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <label className="text-sm font-medium text-[var(--color-purple-deep)]">
              {m.account_language_label()}
            </label>
            <select
              value={selectedLang}
              onChange={(e) => setSelectedLang(e.target.value as 'nl' | 'en')}
              className="w-full max-w-xs rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
            >
              <option value="nl">{m.account_language_nl()}</option>
              <option value="en">{m.account_language_en()}</option>
            </select>
          </div>
          {saveMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">{m.account_error_save()}</p>
          )}
          <Button
            onClick={() => saveMutation.mutate(selectedLang)}
            disabled={saveMutation.isPending || saved}
          >
            {saved
              ? m.account_saved()
              : saveMutation.isPending
                ? m.account_saving()
                : m.account_save()}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/account')({
  component: AccountPage,
})

function AccountPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const { locale, switchLocale } = useLocale()

  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>(locale)

  // Fetch current user's preferred language from the portal DB
  const { data: meData } = useQuery({
    queryKey: ['me-language'],
    queryFn: async () => {
      try {
        return await apiFetch<{ preferred_language?: 'nl' | 'en' }>(`/api/me`, token)
      } catch {
        return null
      }
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
      await apiFetch(`/api/me/language`, token, {
        method: 'PATCH',
        body: JSON.stringify({ preferred_language }),
      })
      return preferred_language
    },
    onSuccess: (lang) => {
      switchLocale(lang)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  const sarMutation = useMutation({
    mutationFn: async () => {
      return apiFetch(`/api/me/sar-export`, token, { method: 'POST' })
    },
    onSuccess: (data: unknown) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const date = new Date().toISOString().split('T')[0]
      a.download = `sar-export-${date}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    },
  })

  const name = auth.user?.profile?.name ?? auth.user?.profile?.preferred_username ?? ''
  const email = auth.user?.profile?.email ?? ''

  return (
    <div className="p-12 space-y-6 max-w-2xl">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.account_heading()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.account_subtitle()}
        </p>
      </div>

      {/* Profile info (display only) */}
      <Card data-help-id="account-profile">
        <CardContent className="pt-6">
          <dl className="space-y-3">
            {name && (
              <div className="flex gap-4">
                <dt className="w-32 shrink-0 text-sm text-[var(--color-muted-foreground)]">Naam</dt>
                <dd className="text-sm font-medium text-[var(--color-foreground)]">{name}</dd>
              </div>
            )}
            {email && (
              <div className="flex gap-4">
                <dt className="w-32 shrink-0 text-sm text-[var(--color-muted-foreground)]">E-mail</dt>
                <dd className="text-sm font-medium text-[var(--color-foreground)]">{email}</dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Language preference */}
      <Card data-help-id="account-2fa">
        <CardHeader>
          <CardTitle>{m.account_language_title()}</CardTitle>
          <CardDescription>
            {m.account_language_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="account-language">
              {m.account_language_label()}
            </Label>
            <Select
              id="account-language"
              value={selectedLang}
              onChange={(e) => setSelectedLang(e.target.value as 'nl' | 'en')}
              className="max-w-xs"
            >
              <option value="nl">{m.account_language_nl()}</option>
              <option value="en">{m.account_language_en()}</option>
            </Select>
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

      {/* Subject Access Request — AVG art. 15 */}
      <Card>
        <CardHeader>
          <CardTitle>{m.account_sar_title()}</CardTitle>
          <CardDescription>
            {m.account_sar_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {sarMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">{m.account_sar_error()}</p>
          )}
          <Button
            onClick={() => sarMutation.mutate()}
            disabled={sarMutation.isPending}
          >
            {sarMutation.isPending ? m.account_sar_downloading() : m.account_sar_button()}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

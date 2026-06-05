import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { usePlatformCreateTenant } from './-hooks'

export const Route = createFileRoute('/admin/platform/new')({
  component: NewTenantPage,
})

function NewTenantPage() {
  const navigate = useNavigate()
  const create = usePlatformCreateTenant()

  const [companyName, setCompanyName] = useState('')
  const [ownerEmail, setOwnerEmail] = useState('')
  const [ownerFirstName, setOwnerFirstName] = useState('')
  const [ownerLastName, setOwnerLastName] = useState('')
  const [language, setLanguage] = useState<'nl' | 'en'>('nl')
  const [error, setError] = useState<string | null>(null)

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    create.mutate(
      {
        company_name: companyName.trim(),
        owner_email: ownerEmail.trim(),
        owner_first_name: ownerFirstName.trim(),
        owner_last_name: ownerLastName.trim(),
        preferred_language: language,
      },
      {
        onSuccess: (result) => {
          toast.success(result.message)
          void navigate({
            to: '/admin/platform/orgs/$orgId',
            params: { orgId: String(result.org_id) },
          })
        },
        onError: (err) => {
          const msg = err instanceof Error ? err.message : m.platform_create_failed()
          setError(msg)
          toast.error(msg)
        },
      },
    )
  }

  return (
    <div className="mx-auto max-w-lg px-6 pt-4 pb-10">
      <div className="flex items-start justify-between gap-3 mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.platform_new_tenant_title()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigate({ to: '/admin/platform' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.platform_new_tenant_description()}
      </p>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="company-name">{m.platform_company_name()}</Label>
          <Input
            id="company-name"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder={m.platform_company_name_placeholder()}
            required
            minLength={2}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="owner-email">{m.platform_owner_email()}</Label>
          <Input
            id="owner-email"
            type="email"
            value={ownerEmail}
            onChange={(e) => setOwnerEmail(e.target.value)}
            placeholder={m.platform_owner_email_placeholder()}
            required
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="owner-first">{m.platform_owner_first_name()}</Label>
            <Input
              id="owner-first"
              value={ownerFirstName}
              onChange={(e) => setOwnerFirstName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="owner-last">{m.platform_owner_last_name()}</Label>
            <Input
              id="owner-last"
              value={ownerLastName}
              onChange={(e) => setOwnerLastName(e.target.value)}
              required
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="owner-language">{m.platform_owner_language()}</Label>
          <Select
            id="owner-language"
            value={language}
            onChange={(e) => setLanguage(e.target.value as 'nl' | 'en')}
            className="max-w-xs"
          >
            <option value="nl">{m.platform_language_nl()}</option>
            <option value="en">{m.platform_language_en()}</option>
          </Select>
        </div>

        {error && (
          <p className="text-sm text-[var(--color-destructive)]">{error}</p>
        )}

        <div className="flex items-center gap-3 pt-2">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {m.platform_create_tenant()}
          </Button>
        </div>
      </form>
    </div>
  )
}

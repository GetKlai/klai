import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { useDefaultLanguageMutation, type OrgSettings } from '../-settings-hooks'

interface LanguageSettingsSectionProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

export function LanguageSettingsSection({
  settings,
  isLoading,
  error,
}: LanguageSettingsSectionProps) {
  const [selectedLang, setSelectedLang] = useState<OrgSettings['default_language']>('nl')
  const [savedLang, setSavedLang] = useState(false)
  const langMutation = useDefaultLanguageMutation(() => {
    setSavedLang(true)
    setTimeout(() => setSavedLang(false), 2500)
  })

  useEffect(() => {
    if (settings) {
      setSelectedLang(settings.default_language)
    }
  }, [settings])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    langMutation.mutate(selectedLang)
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_language_title()}
        </h2>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="settings-language">
                {m.admin_settings_language_label()}
              </Label>
              <Select
                id="settings-language"
                value={selectedLang}
                onChange={(e) => setSelectedLang(e.target.value as OrgSettings['default_language'])}
                containerClassName="max-w-xs"
              >
                <option value="nl">{m.admin_settings_language_nl()}</option>
                <option value="en">{m.admin_settings_language_en()}</option>
              </Select>
            </div>
            {langMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            <div className="pt-2">
              <Button
                type="submit"
                disabled={
                  langMutation.isPending ||
                  savedLang ||
                  selectedLang === settings?.default_language
                }
              >
                {savedLang
                  ? m.admin_settings_saved()
                  : langMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            </div>
          </>
        )}
      </form>
    </section>
  )
}

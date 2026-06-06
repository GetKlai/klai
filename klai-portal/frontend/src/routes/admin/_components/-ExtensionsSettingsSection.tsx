import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { extensionDescription, extensionLabel } from '@/lib/extensions-i18n'
import * as m from '@/paraglide/messages'
import {
  useAdminExtensions,
  useAdminSettingsMe,
  useExtensionsMutation,
} from '../-settings-hooks'

export function ExtensionsSettingsSection() {
  const { data: me } = useAdminSettingsMe()
  const { data: extensions, isLoading: extensionsLoading, error: extensionsError } = useAdminExtensions()
  const [stagedExtensions, setStagedExtensions] = useState<Set<string>>(new Set())
  const [savedExtensions, setSavedExtensions] = useState(false)
  const extensionsMutation = useExtensionsMutation(() => {
    setSavedExtensions(true)
    setTimeout(() => setSavedExtensions(false), 2500)
  })

  useEffect(() => {
    if (extensions) {
      setStagedExtensions(
        new Set(extensions.extensions.filter((e) => e.enabled).map((e) => e.key)),
      )
    }
  }, [extensions])

  function stageExtension(key: string, enabled: boolean) {
    setStagedExtensions((prev) => {
      const next = new Set(prev)
      if (enabled) next.add(key)
      else next.delete(key)
      return next
    })
  }

  const savedEnabled = new Set(
    extensions?.extensions.filter((e) => e.enabled).map((e) => e.key) ?? [],
  )
  const extensionsDirty =
    extensions != null &&
    (stagedExtensions.size !== savedEnabled.size ||
      [...stagedExtensions].some((key) => !savedEnabled.has(key)))
  const isPlatformAdmin = me?.is_platform_admin ?? false

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!extensions) return
    extensionsMutation.mutate({
      org_slug: extensions.org_slug,
      enabled_features: [...stagedExtensions].sort(),
    })
  }

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_extensions_title()}
        </h2>
        <p className="text-sm text-gray-400">
          {isPlatformAdmin
            ? m.admin_settings_extensions_description_platform()
            : m.admin_settings_extensions_description_tenant()}
        </p>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {extensionsLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : extensionsError ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : !extensions ? null : (
          <>
            <ul className="divide-y divide-gray-200 border-t border-b border-gray-200">
              {extensions.extensions.map((item) => {
                const staged = stagedExtensions.has(item.key)
                return (
                  <li key={item.key} className="flex items-center justify-between gap-4 px-2 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[15px] font-display text-gray-900">{extensionLabel(item.key)}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{extensionDescription(item.key)}</p>
                    </div>
                    {item.manageable_by_caller ? (
                      <Checkbox
                        checked={staged}
                        onChange={(e) => stageExtension(item.key, e.target.checked)}
                        disabled={extensionsMutation.isPending}
                        label=""
                      />
                    ) : (
                      <span
                        className={[
                          'shrink-0 rounded-full px-3 py-0.5 text-xs font-medium',
                          item.enabled
                            ? 'bg-[var(--color-rl-cream)] text-[var(--color-rl-accent-dark)]'
                            : 'bg-gray-100 text-gray-400',
                        ].join(' ')}
                      >
                        {item.enabled
                          ? m.admin_settings_extensions_status_on()
                          : m.admin_settings_extensions_status_off()}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
            {!isPlatformAdmin && (
              <p className="text-xs text-gray-400">
                {m.admin_settings_extensions_managed_by_klai()}
              </p>
            )}
            {extensionsMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            {isPlatformAdmin && (
              <Button
                type="submit"
                disabled={extensionsMutation.isPending || savedExtensions || !extensionsDirty}
              >
                {savedExtensions
                  ? m.admin_settings_saved()
                  : extensionsMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            )}
          </>
        )}
      </form>
    </section>
  )
}

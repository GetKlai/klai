import { useNavigate } from '@tanstack/react-router'
import { useLocale } from '@/lib/locale'

export function LocaleSwitcher() {
  const { locale, switchLocale } = useLocale()
  const navigate = useNavigate()

  function handleSwitch(newLocale: 'nl' | 'en') {
    if (newLocale === locale) return

    // If on a locale-prefixed route (/nl/... or /en/...), navigate to the
    // sibling locale URL so the path and the displayed language stay in sync.
    const path = window.location.pathname
    if (/^\/(nl|en)(\/|$)/.test(path)) {
      const newPath = path.replace(/^\/(nl|en)/, `/${newLocale}`)
      navigate({ to: newPath })
      return
    }

    // For non-prefixed routes (login, verify, etc.) update state + localStorage only.
    switchLocale(newLocale)
  }

  return (
    <div className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
      <button
        onClick={() => handleSwitch('nl')}
        className={locale === 'nl' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
      >
        NL
      </button>
      <span className="opacity-30">/</span>
      <button
        onClick={() => handleSwitch('en')}
        className={locale === 'en' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
      >
        EN
      </button>
    </div>
  )
}

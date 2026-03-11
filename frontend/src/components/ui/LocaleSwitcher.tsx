import { useLocale } from '@/lib/locale'

export function LocaleSwitcher() {
  const { locale, switchLocale } = useLocale()

  return (
    <div className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
      <button
        onClick={() => switchLocale('nl')}
        className={locale === 'nl' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
      >
        NL
      </button>
      <span className="opacity-30">/</span>
      <button
        onClick={() => switchLocale('en')}
        className={locale === 'en' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
      >
        EN
      </button>
    </div>
  )
}

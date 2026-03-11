import { createContext, useContext, useState, type ReactNode } from 'react'
import { setLocale as paraglideSetLocale } from '@/paraglide/runtime'

export type Locale = 'nl' | 'en'

interface LocaleContextValue {
  locale: Locale
  switchLocale: (l: Locale) => void
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: 'nl',
  switchLocale: () => {},
})

function getInitialLocale(): Locale {
  // 1. URL param (present on email links or when passed explicitly)
  const params = new URLSearchParams(window.location.search)
  const urlParam = params.get('lang') ?? params.get('ui_locales')?.split(' ')[0]
  if (urlParam === 'nl' || urlParam === 'en') {
    localStorage.setItem('klai-locale', urlParam)
    return urlParam
  }
  // 2. localStorage (explicit user choice on this device)
  const saved = localStorage.getItem('klai-locale')
  if (saved === 'nl' || saved === 'en') return saved as Locale
  // 3. Browser preference
  const browserLang = navigator.language.slice(0, 2).toLowerCase()
  const resolved: Locale = (browserLang === 'nl' || browserLang === 'en') ? browserLang as Locale : 'nl'
  // Always persist so subsequent page loads (e.g. after OIDC redirect) find a consistent value
  localStorage.setItem('klai-locale', resolved)
  return resolved
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = getInitialLocale()
    paraglideSetLocale(initial)
    return initial
  })

  function switchLocale(l: Locale) {
    paraglideSetLocale(l)
    setLocaleState(l)
    localStorage.setItem('klai-locale', l)
  }

  return (
    <LocaleContext.Provider value={{ locale, switchLocale }}>
      {children}
    </LocaleContext.Provider>
  )
}

export function useLocale() {
  return useContext(LocaleContext)
}

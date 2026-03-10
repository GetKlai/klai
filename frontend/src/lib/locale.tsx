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
  const saved = localStorage.getItem('klai-locale')
  return saved === 'en' ? 'en' : 'nl'
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

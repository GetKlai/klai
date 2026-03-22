// Ambient type declarations for Paraglide compiled output (src/paraglide/).
// Paraglide's CLI does not emit .d.ts files; the Vite plugin does during
// `vite build`, but `tsc -b` runs first. These declarations bridge that gap.

declare module '*/paraglide/messages' {
  // Each message is a function that returns a localized string.
  // Messages with parameters accept an object; those without accept nothing.
  type MessageFn = (params?: Record<string, string>) => string
  const messages: Record<string, MessageFn>
  export = messages
}

declare module '*/paraglide/runtime' {
  export function setLocale(locale: string): void
  export function getLocale(): string
  export function languageTag(): string
  export const isAvailableLanguageTag: (tag: string) => boolean
  export const availableLanguageTags: readonly string[]
  export const sourceLanguageTag: string
}

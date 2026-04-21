import { LocaleSwitcher } from '@/components/ui/LocaleSwitcher'

interface AuthPageLayoutProps {
  leftContent: React.ReactNode
  children: React.ReactNode
  showLocale?: boolean
}

/**
 * Two-panel auth page shell shared by login, signup, forgot-password,
 * set-password, verify-email, and 2FA setup pages.
 *
 * Left panel: bg-1.webp with a warm rl-dark overlay (~60%) + extra fade at
 * the bottom to keep hero copy + bullets legible on any part of the image.
 * Right panel: ivory content area with optional locale switcher.
 */
export function AuthPageLayout({ leftContent, children, showLocale = false }: AuthPageLayoutProps) {
  return (
    <div className="flex min-h-screen bg-[var(--color-background)]">
      {/* Left panel — branding on bg-1 */}
      <div className="relative hidden lg:flex lg:w-1/2 flex-col text-[var(--color-rl-cream)]">
        {/* Background image */}
        <div
          className="absolute inset-0 z-0"
          style={{
            backgroundImage: "url('/bg-1.webp')",
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            backgroundRepeat: 'no-repeat',
          }}
        />
        {/* Overlay — warm rl-dark, heavy in the content band (40–90%) where
            the body paragraph + bullets live, lighter at the logo edge. */}
        <div
          className="absolute inset-0 z-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(25,25,24,0.35) 0%, rgba(25,25,24,0.55) 25%, rgba(25,25,24,0.78) 45%, rgba(25,25,24,0.80) 80%, rgba(25,25,24,0.88) 100%)',
          }}
        />

        {/* Content — above overlays */}
        <div className="relative z-10 flex flex-1 flex-col justify-between p-14">
          <div>
            <img src="/klai-logo-white.svg" alt="Klai" className="h-7 w-auto block" />
          </div>
          <div className="max-w-md space-y-6">{leftContent}</div>
          <div className="text-xs text-[var(--color-rl-cream)]/60">
            © Klai. Privé AI voor Europese teams.
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex w-full flex-col items-center justify-center px-8 py-12 lg:w-1/2">
        <div className="w-full max-w-sm space-y-8">
          <div className="flex items-center justify-between">
            <div className="lg:hidden">
              <img src="/klai-logo.svg" alt="Klai" className="h-7 w-auto block" />
            </div>
            {showLocale && (
              <div className="ml-auto">
                <LocaleSwitcher />
              </div>
            )}
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}

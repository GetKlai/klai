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
 * Left panel: hero-bg.webp (same as marketing homepage) with a dark gradient
 * overlay for legibility of cream hero copy + white logo.
 * Right panel: ivory content area with optional locale switcher.
 */
export function AuthPageLayout({ leftContent, children, showLocale = false }: AuthPageLayoutProps) {
  return (
    <div className="flex min-h-screen bg-[var(--color-background)]">
      {/* Left panel — branding on hero bg */}
      <div
        className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 text-[var(--color-rl-cream)]"
        style={{
          backgroundImage:
            "linear-gradient(to top, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.35) 50%, rgba(0,0,0,0.2) 100%), url('/hero-bg.webp')",
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          backgroundRepeat: 'no-repeat',
        }}
      >
        <div>
          <img src="/klai-logo-white.svg" alt="Klai" className="h-7 w-auto block" />
        </div>
        <div className="space-y-6 my-auto">
          {leftContent}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
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

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
 * Left panel: purple branding with logo + hero content.
 * Right panel: white content area with optional locale switcher.
 */
export function AuthPageLayout({ leftContent, children, showLocale = false }: AuthPageLayoutProps) {
  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
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

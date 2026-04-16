import { useState } from 'react'
import { HelpCircle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useHelp } from '@/lib/help/use-help'
import * as m from '@/paraglide/messages'

export function HelpButton() {
  const { enabled, toggle, showIntro, dismissIntro, introContent } = useHelp()
  const [hovered, setHovered] = useState(false)

  return (
    <>
      {showIntro && (
        <div className="fixed bottom-20 right-6 z-[10003] w-72 rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] shadow-lg">
          <div className="flex items-start justify-between p-4 pb-2">
            <h3 className="font-semibold text-[var(--color-foreground)]">
              {introContent.title()}
            </h3>
            <button
              onClick={dismissIntro}
              className="ml-2 text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
              aria-label="Sluiten"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <p className="px-4 pb-3 text-sm text-[var(--color-muted-foreground)]">
            {introContent.description()}
          </p>
          <div className="border-t border-[var(--color-border)] px-4 py-3">
            <Button size="sm" onClick={dismissIntro}>
              {m.help_intro_got_it()}
            </Button>
          </div>
        </div>
      )}

      {/* Wrapper handles fixed position and hover detection */}
      <div
        className="fixed bottom-6 right-6 z-[10003]"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {/* Tooltip — absolute, centered above the button */}
        {!enabled && hovered && (
          <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 pointer-events-none">
            <div className="relative px-2 py-1 text-xs text-white bg-[var(--color-foreground)] rounded whitespace-nowrap">
              {m.help_btn_enable()}
              <div
                className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0"
                style={{
                  borderLeft: '5px solid transparent',
                  borderRight: '5px solid transparent',
                  borderTop: '5px solid var(--color-foreground)',
                }}
              />
            </div>
          </div>
        )}

        <button
          onClick={toggle}
          data-help-id="help-button"
          className={`flex items-center rounded-full shadow-md cursor-pointer outline-none ${
            enabled ? 'gap-2 px-4 h-10' : 'w-10 h-10 justify-center p-0'
          }`}
          style={{
            backgroundColor: 'var(--color-foreground)',
            color: 'white',
            border: 'none',
          }}
          aria-label={m.help_toggle_label()}
          aria-pressed={enabled}
        >
          <HelpCircle className="h-5 w-5 shrink-0" />
          <span
            className={`text-sm font-medium whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-200 ${
              enabled ? 'max-w-[120px] opacity-100' : 'max-w-0 opacity-0'
            }`}
          >
            {hovered ? m.help_btn_disable() : m.help_btn_label()}
          </span>
        </button>
      </div>
    </>
  )
}

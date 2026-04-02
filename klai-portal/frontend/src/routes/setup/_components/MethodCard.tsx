import { ShieldCheck } from 'lucide-react'
import * as m from '@/paraglide/messages'

interface MethodCardProps {
  icon: React.ReactNode
  title: string
  description: string
  recommended?: boolean
  selected: boolean
  onClick: () => void
}

export function MethodCard({
  icon,
  title,
  description,
  recommended,
  selected,
  onClick,
}: MethodCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border-2 p-4 text-left transition-all
        ${selected
          ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5'
          : 'border-[var(--color-border)] bg-white hover:border-[var(--color-purple-accent)]/50'
        }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${selected ? 'text-[var(--color-purple-accent)]' : 'text-[var(--color-sand-mid)]'}`}>
          {icon}
        </div>
        <div className="flex-1 space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[var(--color-foreground)]">{title}</span>
            {recommended && (
              <span className="rounded-full bg-[var(--color-purple-accent)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                {m.setup_mfa_badge_recommended()}
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--color-muted-foreground)]">{description}</p>
        </div>
        {selected && (
          <div className="mt-0.5 shrink-0 text-[var(--color-purple-accent)]">
            <ShieldCheck size={16} />
          </div>
        )}
      </div>
    </button>
  )
}

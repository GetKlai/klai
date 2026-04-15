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
      className={`w-full rounded-lg border-2 p-4 text-left transition-all
        ${selected
          ? 'border-gray-900 bg-gray-50'
          : 'border-gray-200 bg-white hover:border-gray-400'
        }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${selected ? 'text-gray-700' : 'text-gray-300'}`}>
          {icon}
        </div>
        <div className="flex-1 space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{title}</span>
            {recommended && (
              <span className="rounded-full bg-gray-900 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-white">
                {m.setup_mfa_badge_recommended()}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-400">{description}</p>
        </div>
        {selected && (
          <div className="mt-0.5 shrink-0 text-gray-700">
            <ShieldCheck size={16} />
          </div>
        )}
      </div>
    </button>
  )
}

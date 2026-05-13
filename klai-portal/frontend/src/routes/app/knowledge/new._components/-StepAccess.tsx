import { Globe, Lock, Users } from 'lucide-react'
import * as m from '@/paraglide/messages'
import type { WizardData, WizardDataSetter } from '../new._types'

export function StepAccess({
  data,
  setData,
}: {
  data: WizardData
  setData: WizardDataSetter
}) {
  const options = [
    {
      key: 'public' as const,
      icon: Globe,
      title: m.knowledge_sharing_visibility_public(),
      desc: m.knowledge_sharing_visibility_public_description(),
    },
    {
      key: 'org' as const,
      icon: Users,
      title: m.knowledge_sharing_visibility_org(),
      desc: m.knowledge_sharing_visibility_org_description(),
    },
    {
      key: 'restricted' as const,
      icon: Lock,
      title: m.knowledge_sharing_visibility_restricted(),
      desc: m.knowledge_sharing_visibility_restricted_description(),
    },
  ] as const

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-gray-900">
        {m.knowledge_wizard_title_step2({ name: data.name })}
      </p>

      <div className="flex flex-col gap-2">
        {options.map(({ key, icon: Icon, title, desc }) => (
          <button
            key={key}
            type="button"
            onClick={() =>
              setData((prev) => ({
                ...prev,
                visibilityMode: key,
                allowContribute: key === 'restricted' ? false : prev.allowContribute,
              }))
            }
            className={[
              'flex items-start gap-3 rounded-xl border p-4 text-left transition-all',
              data.visibilityMode === key
                ? 'border-gray-200 bg-black/[0.06] ring-1 ring-gray-900'
                : 'border-gray-200 bg-[var(--color-card)] hover:border-gray-300',
            ].join(' ')}
          >
            <Icon className="h-5 w-5 mt-0.5 text-gray-400" />
            <div>
              <span className="text-sm font-medium text-gray-900">
                {title}
              </span>
              <span className="block text-xs text-gray-400">{desc}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

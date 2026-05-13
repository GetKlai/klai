import { User, Users } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WizardData, WizardDataSetter, WizardErrorKey } from '../new._types'

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function StepName({
  data,
  setData,
  errorKey,
  isLimitedPlan,
}: {
  data: WizardData
  setData: WizardDataSetter
  errorKey: WizardErrorKey
  isLimitedPlan: boolean
}) {
  function handleNameChange(value: string) {
    setData((prev) => ({
      ...prev,
      name: value,
      slug: prev.slugManuallyEdited ? prev.slug : slugify(value),
    }))
  }

  function handleSlugChange(value: string) {
    setData((prev) => ({
      ...prev,
      slug: slugify(value),
      slugManuallyEdited: true,
    }))
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-gray-900">
        {m.knowledge_wizard_title_step1()}
      </p>

      {!isLimitedPlan && (
        <div className="flex flex-col gap-1.5">
          <Label>{m.knowledge_new_scope_label()}</Label>
          <div className="grid grid-cols-2 gap-3">
            {(['org', 'user'] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() =>
                  setData((prev) => ({
                    ...prev,
                    ownerType: type,
                    visibilityMode: type === 'user' ? 'org' : prev.visibilityMode,
                  }))
                }
                className={[
                  'flex flex-col items-start gap-1 rounded-xl border p-4 text-left transition-all',
                  data.ownerType === type
                    ? 'border-gray-200 bg-black/[0.06] ring-1 ring-gray-900'
                    : 'border-gray-200 bg-[var(--color-card)] hover:border-gray-300',
                ].join(' ')}
              >
                {type === 'org' ? (
                  <Users className="h-4 w-4 text-gray-400" />
                ) : (
                  <User className="h-4 w-4 text-gray-400" />
                )}
                <span className="text-sm font-medium text-gray-900">
                  {type === 'org' ? m.knowledge_new_scope_org() : m.knowledge_new_scope_personal()}
                </span>
                <span className="text-xs text-gray-400">
                  {type === 'org'
                    ? m.knowledge_new_scope_org_description()
                    : m.knowledge_new_scope_personal_description()}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-name">{m.knowledge_new_name_label()}</Label>
        <Input
          id="kb-name"
          value={data.name}
          onChange={(e) => handleNameChange(e.target.value)}
          placeholder={m.knowledge_new_name_placeholder()}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-slug">{m.knowledge_new_slug_label()}</Label>
        <Input
          id="kb-slug"
          value={data.slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          pattern="[a-z0-9\-]+"
        />
        <p className="text-xs text-gray-400">
          {m.knowledge_new_slug_hint()}
        </p>
        {errorKey === 'conflict' && (
          <p className="text-xs text-[var(--color-destructive)]">
            {m.knowledge_new_slug_conflict()}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-description">{m.knowledge_wizard_description_label()}</Label>
        <textarea
          id="kb-description"
          value={data.description}
          onChange={(e) => setData((prev) => ({ ...prev, description: e.target.value }))}
          placeholder={m.knowledge_wizard_description_placeholder()}
          rows={3}
          className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50 resize-none"
        />
      </div>
    </div>
  )
}

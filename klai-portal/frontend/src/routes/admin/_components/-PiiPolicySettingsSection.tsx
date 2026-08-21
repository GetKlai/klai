import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { ChevronDown, ChevronRight, Lock } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  ListFrame,
  ListRow,
  ListRowContent,
  ListRowDescription,
  ListRowIcon,
  ListRowTitle,
} from '@/components/ui/list'
import { meetsMinRole } from '@/lib/profiles'
import * as m from '@/paraglide/messages'
import {
  useAdminSettingsMe,
  usePiiEntitiesMutation,
  type OrgSettings,
} from '../-settings-hooks'

// SPEC-PRIVACY-PII-POLICY-ADMIN-001 D6 — the seven return-set entity types
// this PR ships (NL_CITY is out of scope: no recogniser exists yet, see
// REQ-6). Storage stays per entity (`pii_masked_entities`); the four groups
// below are presentational only.
type PiiEntity =
  | 'EMAIL_ADDRESS'
  | 'PHONE_NUMBER'
  | 'IBAN_CODE'
  | 'CREDIT_CARD'
  | 'NL_KVK'
  | 'NL_BTW'
  | 'NL_POSTCODE'

interface PiiGroupConfig {
  key: string
  title: string
  description: string
  entities: PiiEntity[]
}

function entityLabel(entity: PiiEntity): string {
  switch (entity) {
    case 'EMAIL_ADDRESS':
      return m.admin_settings_pii_entity_email()
    case 'PHONE_NUMBER':
      return m.admin_settings_pii_entity_phone()
    case 'IBAN_CODE':
      return m.admin_settings_pii_entity_iban()
    case 'CREDIT_CARD':
      return m.admin_settings_pii_entity_creditcard()
    case 'NL_KVK':
      return m.admin_settings_pii_entity_kvk()
    case 'NL_BTW':
      return m.admin_settings_pii_entity_btw()
    case 'NL_POSTCODE':
      return m.admin_settings_pii_entity_postcode()
    default:
      return entity
  }
}

interface GroupCheckboxProps {
  checked: boolean
  indeterminate: boolean
  disabled: boolean
  label: string
  onChange: (event: ChangeEvent<HTMLInputElement>) => void
}

// Native <input type="checkbox"> exposes tri-state via the `.indeterminate`
// DOM property (not an HTML attribute), so it has to be set imperatively.
// `aria-checked="mixed"` mirrors that for assistive tech. Clicking an
// indeterminate checkbox always resolves to checked=true — that native
// behavior is exactly D6's "click on a mixed group turns it fully on".
function GroupCheckbox({ checked, indeterminate, disabled, label, onChange }: GroupCheckboxProps) {
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate
  }, [indeterminate])

  return (
    <Checkbox
      ref={ref}
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      aria-label={label}
      aria-checked={indeterminate ? 'mixed' : checked}
      label=""
    />
  )
}

interface PiiPolicySettingsSectionProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

export function PiiPolicySettingsSection({
  settings,
  isLoading,
  error,
}: PiiPolicySettingsSectionProps) {
  const { data: me } = useAdminSettingsMe()
  const isTenantAdmin = meetsMinRole(me?.portal_role, 'admin')

  const [stagedEntities, setStagedEntities] = useState<Set<string>>(new Set())
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [savedEntities, setSavedEntities] = useState(false)
  const entitiesMutation = usePiiEntitiesMutation(() => {
    setSavedEntities(true)
    setTimeout(() => setSavedEntities(false), 2500)
  })

  useEffect(() => {
    if (settings) {
      setStagedEntities(new Set(settings.pii_masked_entities))
    }
  }, [settings])

  const groups: PiiGroupConfig[] = [
    {
      key: 'contact',
      title: m.admin_settings_pii_group_contact_title(),
      description: m.admin_settings_pii_group_contact_description(),
      entities: ['EMAIL_ADDRESS', 'PHONE_NUMBER'],
    },
    {
      key: 'financial',
      title: m.admin_settings_pii_group_financial_title(),
      description: m.admin_settings_pii_group_financial_description(),
      entities: ['IBAN_CODE', 'CREDIT_CARD'],
    },
    {
      key: 'company',
      title: m.admin_settings_pii_group_company_title(),
      description: m.admin_settings_pii_group_company_description(),
      entities: ['NL_KVK', 'NL_BTW'],
    },
    {
      key: 'location',
      title: m.admin_settings_pii_group_location_title(),
      description: m.admin_settings_pii_group_location_description(),
      entities: ['NL_POSTCODE'],
    },
  ]

  function toggleGroup(group: PiiGroupConfig, turnOn: boolean) {
    setStagedEntities((prev) => {
      const next = new Set(prev)
      for (const entity of group.entities) {
        if (turnOn) next.add(entity)
        else next.delete(entity)
      }
      return next
    })
  }

  function toggleEntity(entity: PiiEntity, enabled: boolean) {
    setStagedEntities((prev) => {
      const next = new Set(prev)
      if (enabled) next.add(entity)
      else next.delete(entity)
      return next
    })
  }

  function toggleExpanded(key: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const savedSet = new Set(settings?.pii_masked_entities ?? [])
  const entitiesDirty =
    settings != null &&
    (stagedEntities.size !== savedSet.size ||
      [...stagedEntities].some((entity) => !savedSet.has(entity)))

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    entitiesMutation.mutate([...stagedEntities].sort())
  }

  return (
    <section className="space-y-4" data-help-id="admin-settings-pii-policy">
      <div className="space-y-1">
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_pii_title()}
        </h2>
        <p className="text-sm text-gray-600">{m.admin_settings_pii_intro()}</p>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <ListFrame>
              {groups.map((group) => {
                const onCount = group.entities.filter((entity) => stagedEntities.has(entity)).length
                const allOn = onCount === group.entities.length
                const allOff = onCount === 0
                const mixed = !allOn && !allOff
                const expanded = expandedGroups.has(group.key)
                return (
                  <div key={group.key}>
                    <ListRow>
                      <GroupCheckbox
                        checked={allOn}
                        indeterminate={mixed}
                        disabled={!isTenantAdmin || entitiesMutation.isPending}
                        label={group.title}
                        onChange={(e) => toggleGroup(group, e.target.checked)}
                      />
                      <ListRowContent>
                        <div className="flex items-center gap-2">
                          <ListRowTitle>{group.title}</ListRowTitle>
                          {mixed && (
                            <Badge variant="warning">{m.admin_settings_pii_mixed_hint()}</Badge>
                          )}
                        </div>
                        <ListRowDescription>{group.description}</ListRowDescription>
                      </ListRowContent>
                      {group.entities.length > 1 && (
                        <button
                          type="button"
                          className="shrink-0 self-center rounded-md p-1.5 text-gray-400 hover:bg-[var(--color-muted)]/60 hover:text-gray-900"
                          aria-expanded={expanded}
                          aria-label={
                            expanded
                              ? m.admin_settings_pii_collapse_details()
                              : m.admin_settings_pii_expand_details()
                          }
                          onClick={() => toggleExpanded(group.key)}
                        >
                          {expanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                      )}
                    </ListRow>
                    {expanded && (
                      <div className="space-y-2 border-t border-gray-100 bg-[var(--color-muted)]/30 py-3 pl-12 pr-4">
                        {group.entities.map((entity) => (
                          <Checkbox
                            key={entity}
                            checked={stagedEntities.has(entity)}
                            disabled={!isTenantAdmin || entitiesMutation.isPending}
                            onChange={(e) => toggleEntity(entity, e.target.checked)}
                            label={entityLabel(entity)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
              <ListRow>
                <ListRowIcon>
                  <Lock className="h-4 w-4" />
                </ListRowIcon>
                <ListRowContent>
                  <ListRowTitle>{m.admin_settings_pii_locked_title()}</ListRowTitle>
                  <ListRowDescription>{m.admin_settings_pii_locked_description()}</ListRowDescription>
                  <p className="mt-1 text-xs text-gray-400">{m.admin_settings_pii_locked_reason()}</p>
                </ListRowContent>
              </ListRow>
            </ListFrame>
            {!isTenantAdmin && (
              <p className="text-xs text-gray-400">{m.admin_settings_pii_readonly_hint()}</p>
            )}
            {entitiesMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            {isTenantAdmin && (
              <div className="pt-2">
                <Button
                  type="submit"
                  disabled={entitiesMutation.isPending || savedEntities || !entitiesDirty}
                >
                  {savedEntities
                    ? m.admin_settings_saved()
                    : entitiesMutation.isPending
                      ? m.admin_settings_saving()
                      : m.admin_settings_save()}
                </Button>
              </div>
            )}
          </>
        )}
      </form>
      <div className="space-y-2 border-t border-gray-100 pt-4">
        <h3 className="text-sm font-display-bold text-gray-900">
          {m.admin_settings_pii_limitations_title()}
        </h3>
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-gray-600">
          <li>{m.admin_settings_pii_limitation_names()}</li>
          <li>{m.admin_settings_pii_limitation_address()}</li>
          <li>{m.admin_settings_pii_limitation_structured()}</li>
          <li>{m.admin_settings_pii_limitation_context()}</li>
          <li>{m.admin_settings_pii_limitation_false_positives()}</li>
          <li>{m.admin_settings_pii_limitation_storage()}</li>
          <li>{m.admin_settings_pii_limitation_locked_categories()}</li>
        </ul>
      </div>
    </section>
  )
}

import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import {
  usePiiEntitiesMutation,
  useTelemetryLevelMutation,
  type OrgSettings,
  type TelemetryLevel,
} from '../-settings-hooks'
import { PiiPolicySettingsSection } from './-PiiPolicySettingsSection'
import { TelemetrySettingsSection } from './-TelemetrySettingsSection'

interface PrivacySettingsTabProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

// Owns the privacy tab's single save button. Both child sections are
// controlled + presentational: they stage local edits here and persist
// nothing on their own. Save fires only the mutations for whichever staged
// value actually changed, so an untouched field never lands in the audit
// trail as a no-op write.
export function PrivacySettingsTab({ settings, isLoading, error }: PrivacySettingsTabProps) {
  const [selectedTelemetry, setSelectedTelemetry] = useState<TelemetryLevel>('shadow')
  const [stagedEntities, setStagedEntities] = useState<Set<string>>(new Set())
  const [saved, setSaved] = useState(false)

  const telemetryMutation = useTelemetryLevelMutation(() => {})
  const entitiesMutation = usePiiEntitiesMutation(() => {})

  useEffect(() => {
    if (settings?.telemetry_level) {
      setSelectedTelemetry(settings.telemetry_level)
    }
  }, [settings?.telemetry_level])

  // Depend on the entity list's content, not on `settings` — TanStack Query
  // hands back a fresh object on every refetch, so an object-identity dep
  // resets staged choices whenever any OTHER section on this tab saves.
  const serverEntities = settings?.pii_masked_entities
  const serverEntitiesKey = serverEntities ? [...serverEntities].sort().join(',') : null
  useEffect(() => {
    if (serverEntitiesKey !== null) {
      setStagedEntities(new Set(serverEntitiesKey ? serverEntitiesKey.split(',') : []))
    }
  }, [serverEntitiesKey])

  const telemetryDirty = settings != null && selectedTelemetry !== settings.telemetry_level
  const stagedEntitiesKey = [...stagedEntities].sort().join(',')
  const entitiesDirty = serverEntitiesKey !== null && stagedEntitiesKey !== serverEntitiesKey
  const dirty = telemetryDirty || entitiesDirty

  const isPending = telemetryMutation.isPending || entitiesMutation.isPending
  const saveError = telemetryMutation.error ?? entitiesMutation.error

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!dirty) return

    const tasks: Promise<unknown>[] = []
    if (telemetryDirty) tasks.push(telemetryMutation.mutateAsync(selectedTelemetry))
    if (entitiesDirty) tasks.push(entitiesMutation.mutateAsync([...stagedEntities].sort()))

    try {
      await Promise.all(tasks)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      // Each mutation's own `.error` state surfaces the failure below.
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      <TelemetrySettingsSection
        selectedTelemetry={selectedTelemetry}
        onTelemetryChange={setSelectedTelemetry}
        isLoading={isLoading}
        error={error}
        disabled={isPending}
      />
      <PiiPolicySettingsSection
        stagedEntities={stagedEntities}
        onEntitiesChange={setStagedEntities}
        isLoading={isLoading}
        error={error}
        disabled={isPending}
      />
      {saveError != null && (
        <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
      )}
      <div className="pt-2">
        <Button type="submit" disabled={isPending || saved || !dirty}>
          {saved
            ? m.admin_settings_saved()
            : isPending
              ? m.admin_settings_saving()
              : m.admin_settings_save()}
        </Button>
      </div>
    </form>
  )
}

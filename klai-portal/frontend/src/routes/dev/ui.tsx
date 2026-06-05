import { createFileRoute } from '@tanstack/react-router'
import type { ComponentProps, ReactNode } from 'react'
import { useState } from 'react'
import {
  FileText,
  Loader2,
  Settings,
  Sparkles,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  ListFrame,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import {
  RowActionButton,
  RowActionGroup,
  RowActionIconButton,
  type RowActionKind,
  type RowActionTone,
} from '@/components/ui/row-action'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

export const Route = createFileRoute('/dev/ui')({
  component: UiCatalogPage,
})

const actionKinds: RowActionKind[] = [
  'add',
  'edit',
  'rename',
  'configure',
  'open',
  'external',
  'sync',
  'retry',
  'copy',
  'reauth',
  'send',
  'view',
  'download',
  'upload',
  'search',
  'save',
  'delete',
  'stop',
  'cancel',
  'more',
  'expand',
  'collapse',
  'suspend',
  'reactivate',
  'leave',
  'offboard',
  'info',
]

const tones: RowActionTone[] = ['neutral', 'primary', 'info', 'success', 'warning', 'danger']

const toneSampleActions: Record<RowActionTone, RowActionKind> = {
  neutral: 'edit',
  primary: 'save',
  info: 'info',
  success: 'sync',
  warning: 'suspend',
  danger: 'delete',
}

const actionColorSemantics: Array<{
  tone: RowActionTone
  label: string
  meaning: string
  examples: string
  swatchClassName: string
}> = [
  {
    tone: 'neutral',
    label: 'Neutral',
    meaning: 'Utility, navigation and low-risk changes',
    examples: 'edit, rename, configure, view, copy, more',
    swatchClassName: 'bg-gray-500',
  },
  {
    tone: 'primary',
    label: 'Primary',
    meaning: 'Primary create, submit or send action',
    examples: 'add, send',
    swatchClassName: 'bg-[var(--color-primary)]',
  },
  {
    tone: 'info',
    label: 'Information',
    meaning: 'Information, progress or system context',
    examples: 'info',
    swatchClassName: 'bg-[var(--color-info)]',
  },
  {
    tone: 'success',
    label: 'Success',
    meaning: 'Positive status change or recovery',
    examples: 'save, sync, reactivate',
    swatchClassName: 'bg-[var(--color-success)]',
  },
  {
    tone: 'warning',
    label: 'Warning',
    meaning: 'Caution, security attention or risky reversible action',
    examples: 'suspend, retry',
    swatchClassName: 'bg-[var(--color-warning)]',
  },
  {
    tone: 'danger',
    label: 'Danger',
    meaning: 'Destructive or high-impact action',
    examples: 'delete, stop, leave, offboard',
    swatchClassName: 'bg-[var(--color-destructive)]',
  },
]

function BorderedRowActionIconButton({
  action,
  tone,
  className,
  ...props
}: ComponentProps<typeof RowActionIconButton>) {
  return (
    <RowActionIconButton
      action={action}
      tone={tone}
      className={[
        'h-7 w-7 border border-current bg-transparent [&_svg]:h-3.5 [&_svg]:w-3.5',
        className,
      ].filter(Boolean).join(' ')}
      {...props}
    />
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
      {children}
    </section>
  )
}

function UiCatalogPage() {
  const [confirming, setConfirming] = useState(false)
  const [checked, setChecked] = useState(true)

  if (!import.meta.env.DEV) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-sm text-gray-400">UI catalog is alleen lokaal beschikbaar.</p>
      </div>
    )
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 space-y-10">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          UI catalog
        </h1>
        <p className="text-sm text-gray-400">
          Lokale referentie voor list rows, row actions en basiscomponenten.
        </p>
      </div>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Default</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="destructive">Destructive</Button>
          <Button size="sm">
            <Sparkles />
            Small
          </Button>
          <Button size="icon" aria-label="Icon button">
            <Settings />
          </Button>
        </div>
      </Section>

      <Section title="Action color semantics">
        <div className="divide-y divide-gray-200 border-y border-gray-200">
          {actionColorSemantics.map((item) => (
            <div key={item.tone} className="grid gap-3 px-2 py-3 sm:grid-cols-[160px_1fr_180px] sm:items-center">
              <div className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full ${item.swatchClassName}`} />
                <RowActionIconButton
                  label={`${item.label} action`}
                  action={toneSampleActions[item.tone]}
                  tone={item.tone}
                  tooltip={false}
                  className="h-7 w-7 [&_svg]:h-3.5 [&_svg]:w-3.5"
                />
                <span className="text-sm font-medium text-gray-900">{item.label}</span>
              </div>
              <p className="text-sm text-gray-500">{item.meaning}</p>
              <p className="text-xs text-gray-400 sm:text-right">{item.examples}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Row actions">
        <div className="space-y-4">
          <RowActionGroup className="justify-start">
            {tones.map((tone) => (
              <RowActionIconButton
                key={tone}
                label={`${tone} action`}
                action={toneSampleActions[tone]}
                tone={tone}
              />
            ))}
            <RowActionIconButton
              label="Loading action"
              action="sync"
              disabled
              spinner={<Loader2 className="animate-spin" />}
            />
          </RowActionGroup>

          <div className="flex flex-wrap gap-2">
            {actionKinds.map((action) => (
              <div key={action} className="flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1">
                <RowActionIconButton label={action} action={action} />
                <span className="text-xs text-gray-400">{action}</span>
              </div>
            ))}
          </div>

          <RowActionGroup className="justify-start flex-wrap">
            <RowActionButton label="Re-authenticate" action="reauth">
              Re-authenticate
            </RowActionButton>
            <RowActionButton label="Save changes" action="save">
              Save changes
            </RowActionButton>
            <RowActionButton label="Remove item" action="delete">
              Remove item
            </RowActionButton>
          </RowActionGroup>
        </div>
      </Section>

      <Section title="Divider lists">
        <ListFrame>
          <ListRow interactive>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <ListRowTitle className="text-sm font-sans font-medium">Kennisbank bronnen</ListRowTitle>
                <Badge variant="secondary">12 bronnen</Badge>
              </div>
              <ListRowDescription>Rustige lijst met links padding en compacte acties.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center">
              <BorderedRowActionIconButton label="Bewerken" action="edit" />
              <BorderedRowActionIconButton label="Openen" action="open" />
              <BorderedRowActionIconButton label="Verwijderen" action="delete" />
            </ListRowActions>
          </ListRow>

          <ListRow interactive>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <ListRowTitle className="text-sm font-sans font-medium">Persoonlijke kennis</ListRowTitle>
                <Badge variant="success">Gesynct</Badge>
              </div>
              <ListRowDescription>Meta-informatie blijft subtiel en truncatet op een regel.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center">
              <BorderedRowActionIconButton label="Synchroniseren" action="sync" />
              <BorderedRowActionIconButton label="Configureren" action="configure" />
              <BorderedRowActionIconButton label="Meer acties" action="more" />
            </ListRowActions>
          </ListRow>

          <ListRow confirming={confirming}>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-gray-400" />
                <ListRowTitle className="text-sm font-sans font-medium">Inline delete confirm</ListRowTitle>
              </div>
              <ListRowDescription>De overlay houdt dezelfde action-cell breedte.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center">
              <InlineDeleteConfirm
                isConfirming={confirming}
                isPending={false}
                label="Verwijderen"
                cancelLabel="Annuleren"
                onConfirm={() => setConfirming(false)}
                onCancel={() => setConfirming(false)}
              >
                <RowActionGroup>
                  <BorderedRowActionIconButton label="Bewerken" action="edit" />
                  <BorderedRowActionIconButton label="Verwijderen" action="delete" onClick={() => setConfirming(true)} />
                </RowActionGroup>
              </InlineDeleteConfirm>
            </ListRowActions>
          </ListRow>
        </ListFrame>
      </Section>

      <Section title="Table spacing">
        <table className="w-full border-y border-gray-200 text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="px-2 py-3 text-left text-xs font-medium text-gray-400">Naam</th>
              <th className="px-2 py-3 text-left text-xs font-medium text-gray-400">Status</th>
              <th className="px-2 py-3 text-right text-xs font-medium text-gray-400">Acties</th>
            </tr>
          </thead>
          <tbody>
            {['Admin users', 'Widgets', 'Sources'].map((name) => (
              <tr key={name} className="border-b border-gray-200 last:border-b-0 klai-hover">
                <td className="px-2 py-4 font-medium text-gray-900">{name}</td>
                <td className="px-2 py-4">
                  <Badge variant="secondary">Actief</Badge>
                </td>
                <td className="px-2 py-4">
                  <RowActionGroup>
                    <BorderedRowActionIconButton label="Bewerken" action="edit" />
                    <BorderedRowActionIconButton label="Verwijderen" action="delete" />
                    <BorderedRowActionIconButton label="Meer acties" action="more" />
                  </RowActionGroup>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Form controls">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="catalog-name">Naam</Label>
            <Input id="catalog-name" defaultValue="Klai component" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="catalog-type">Type</Label>
            <Select id="catalog-type" defaultValue="list">
              <option value="list">List</option>
              <option value="table">Table</option>
            </Select>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="catalog-description">Beschrijving</Label>
            <Textarea id="catalog-description" defaultValue="Compacte portal UI met rustige padding." />
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-900">
            <Checkbox checked={checked} onChange={(event) => setChecked(event.currentTarget.checked)} />
            Subtiele kleur voor herkenning
          </label>
        </div>
      </Section>
    </main>
  )
}

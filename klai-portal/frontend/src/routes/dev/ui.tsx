import { createFileRoute } from '@tanstack/react-router'
import type { ComponentProps, ReactNode } from 'react'
import { useState } from 'react'
import {
  Check,
  FileText,
  Loader2,
  MessageSquare,
  Mic,
  Settings,
  Sparkles,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { InlineEdit } from '@/components/ui/inline-edit'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { QueryErrorState } from '@/components/ui/query-error-state'
import {
  ListFrame,
  ListRow,
  ListRowActions,
  ListRowChevron,
  ListRowContent,
  ListRowDescription,
  ListRowIcon,
  ListRowTitle,
} from '@/components/ui/list'
import { RadioCardGroup } from '@/components/ui/radio-card-group'
import {
  RowActionButton,
  RowActionGroup,
  RowActionIconButton,
  type RowActionKind,
  type RowActionTone,
} from '@/components/ui/row-action'
import { SearchInput } from '@/components/ui/search-input'
import { Select } from '@/components/ui/select'
import { StepIndicator } from '@/components/ui/step-indicator'
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
    <section className="space-y-4 py-10 first:pt-0 last:pb-0">
      <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
      {children}
    </section>
  )
}

const wizardSteps = ['Details', 'Bron', 'Bevestigen']
const tabItems = ['Details', 'Activiteit', 'Instellingen']
const badgeVariants = ['default', 'secondary', 'accent', 'outline', 'info', 'success', 'warning', 'destructive'] as const
const navListItems = [
  { icon: MessageSquare, title: 'Chat', description: 'Privé AI-gesprekken op Europese servers' },
  { icon: Settings, title: 'Instructies', description: 'Beheer instructies die je in chats kunt aanzetten.' },
  { icon: Mic, title: 'Scribe', description: 'Audio, video en vergaderingen omzetten naar tekst' },
]
const radioCardOptions = [
  { value: 'personal', label: 'Persoonlijke chat', description: 'Privé-kennis: upload documenten en chat met je eigen kennisbank.' },
  { value: 'company', label: 'Bedrijfschat', description: 'Alles van Persoonlijke chat, plus toegang tot bedrijfskennis.' },
  { value: 'manager', label: 'Kennisbeheerder', description: 'Beheer connectoren en bedrijfskennisbanken.' },
]
const multiSelectOptions = [
  { value: 'kb', label: 'Kennisbank' },
  { value: 'chat', label: 'Chat' },
  { value: 'connectors', label: 'Connectors' },
  { value: 'widgets', label: 'Widgets' },
]
const commandItems = ['Kennisbank', 'Chat', 'Connectors', 'Widgets', 'Instellingen']

function UiCatalogPage() {
  const [confirming, setConfirming] = useState(false)
  const [checked, setChecked] = useState(true)
  const [wizardStep, setWizardStep] = useState(1)
  const [activeTab, setActiveTab] = useState('Details')
  const [multiValue, setMultiValue] = useState<string[]>(['kb'])
  const [inlineEditing, setInlineEditing] = useState(false)
  const [inlineValue, setInlineValue] = useState('Klai component')
  const [searchValue, setSearchValue] = useState('')
  const [radioValue, setRadioValue] = useState('personal')

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

      <div className="divide-y divide-gray-200">
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

      <Section title="Navigation list">
        <ListFrame>
          {navListItems.map((item) => (
            <ListRow key={item.title} asChild interactive>
              <a href="#">
                <ListRowIcon>
                  <item.icon className="h-4 w-4" />
                </ListRowIcon>
                <ListRowContent>
                  <ListRowTitle>{item.title}</ListRowTitle>
                  <ListRowDescription>{item.description}</ListRowDescription>
                </ListRowContent>
                <ListRowChevron />
              </a>
            </ListRow>
          ))}
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

      <Section title="Wizard steps">
        <div className="space-y-4">
          <StepIndicator
            steps={wizardSteps.map((label, i) => ({
              label,
              onClick: i < wizardStep ? () => setWizardStep(i) : undefined,
            }))}
            currentIndex={wizardStep}
          />
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={wizardStep === 0}
              onClick={() => setWizardStep((s) => Math.max(0, s - 1))}
            >
              Vorige
            </Button>
            <Button
              size="sm"
              disabled={wizardStep === wizardSteps.length - 1}
              onClick={() => setWizardStep((s) => Math.min(wizardSteps.length - 1, s + 1))}
            >
              Volgende
            </Button>
          </div>
        </div>
      </Section>

      <Section title="Tabs">
        <div className="space-y-4">
          <div className="flex gap-6 border-b border-gray-200">
            {tabItems.map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={
                  activeTab === tab
                    ? 'border-b-2 border-gray-900 pb-2 text-sm font-medium text-gray-900'
                    : 'border-b-2 border-transparent pb-2 text-sm text-gray-400 hover:text-gray-900'
                }
              >
                {tab}
              </button>
            ))}
          </div>
          <p className="text-sm text-gray-500">Actieve tab: {activeTab}</p>
        </div>
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
            <Label htmlFor="catalog-search">Zoeken</Label>
            <SearchInput
              id="catalog-search"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              placeholder="Zoek kennisbanken..."
            />
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

      <Section title="Badges">
        <div className="flex flex-wrap items-center gap-2">
          {badgeVariants.map((variant) => (
            <Badge key={variant} variant={variant}>
              {variant}
            </Badge>
          ))}
        </div>
      </Section>

      <Section title="Cards">
        <Card className="max-w-sm">
          <CardHeader>
            <CardTitle>Kennisbank</CardTitle>
            <CardDescription>Rustig omkaderd blok voor herhaalde items of stats.</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-gray-500">12 bronnen, laatst gesynct 2 uur geleden.</p>
          </CardContent>
          <CardFooter>
            <Button variant="secondary" size="sm">Openen</Button>
          </CardFooter>
        </Card>
      </Section>

      <Section title="Overlays and menus">
        <div className="flex flex-wrap items-center gap-3">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="secondary">Dialog</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Voorbeeld dialog</DialogTitle>
                <DialogDescription>Generieke modal voor een formulier of detail.</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button>Opslaan</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive">Alert dialog</Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Item verwijderen?</AlertDialogTitle>
                <AlertDialogDescription>
                  Bevestiging voor een destructieve actie buiten een rij.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Annuleren</AlertDialogCancel>
                <AlertDialogAction>Verwijderen</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">Dropdown menu</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>Acties</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem>Bewerken</DropdownMenuItem>
              <DropdownMenuItem>Dupliceren</DropdownMenuItem>
              <DropdownMenuItem className="text-[var(--color-destructive)]">Verwijderen</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost">Popover</Button>
            </PopoverTrigger>
            <PopoverContent>
              <p className="text-sm text-gray-500">Zwevend paneel op een trigger.</p>
            </PopoverContent>
          </Popover>
        </div>
      </Section>

      <Section title="Selection inputs">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Multi-select</Label>
            <MultiSelect options={multiSelectOptions} value={multiValue} onChange={setMultiValue} />
          </div>
          <div className="space-y-1.5">
            <Label>Command</Label>
            <Command className="rounded-md border border-gray-200">
              <CommandInput placeholder="Zoeken..." />
              <CommandList>
                <CommandEmpty>Geen resultaten.</CommandEmpty>
                <CommandGroup>
                  {commandItems.map((item) => (
                    <CommandItem key={item}>{item}</CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </div>
        </div>
      </Section>

      <Section title="Radio cards">
        <RadioCardGroup
          options={radioCardOptions}
          value={radioValue}
          onChange={setRadioValue}
          aria-label="Profiel"
          className="max-w-md"
        />
      </Section>

      <Section title="Inline edit">
        <div className="flex items-center gap-3">
          <InlineEdit
            isEditing={inlineEditing}
            value={inlineValue}
            onValueChange={setInlineValue}
            onSave={() => setInlineEditing(false)}
            onCancel={() => setInlineEditing(false)}
            inputClassName="text-sm font-medium"
          >
            <span className="text-sm font-medium text-gray-900">{inlineValue}</span>
          </InlineEdit>
          {inlineEditing ? (
            <div className="flex items-center gap-1 whitespace-nowrap">
              <Button
                size="sm"
                className="h-6 gap-1 px-2 text-[10px] [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
                onClick={() => setInlineEditing(false)}
              >
                <Check />
                Opslaan
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 gap-1 px-2 text-[10px] [&_svg]:size-2.5"
                onClick={() => setInlineEditing(false)}
              >
                <X />
                Annuleren
              </Button>
            </div>
          ) : (
            <BorderedRowActionIconButton
              label="Naam bewerken"
              action="edit"
              onClick={() => setInlineEditing(true)}
            />
          )}
        </div>
      </Section>

      <Section title="Feedback">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => toast.success('Opgeslagen')}>
              Toast success
            </Button>
            <Button variant="secondary" size="sm" onClick={() => toast.error('Er ging iets mis')}>
              Toast error
            </Button>
          </div>
          <div className="rounded-md border border-gray-200">
            <QueryErrorState
              error={new Error('Kon de lijst niet laden.')}
              onRetry={() => toast('Opnieuw proberen...')}
            />
          </div>
        </div>
      </Section>
      </div>
    </main>
  )
}

import { createFileRoute } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useState } from 'react'
import {
  FileText,
  Loader2,
  MessageSquare,
  Mic,
  Plus,
  Settings,
  Sparkles,
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
import { ActionTag } from '@/components/ui/action-tag'
import { Alert } from '@/components/ui/alert'
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
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { InlineEditRow } from '@/components/ui/inline-edit-row'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { QueryErrorState } from '@/components/ui/query-error-state'
import {
  ListFrame,
  ListHeader,
  ListRow,
  ListRowActions,
  ListRowChevron,
  ListRowContent,
  ListRowDescription,
  ListRowIcon,
  ListRowTitle,
} from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { RadioCardGroup } from '@/components/ui/radio-card-group'
import {
  BorderedRowActionIconButton,
  RowActionButton,
  RowActionGroup,
  RowActionIconButton,
  type RowActionKind,
  type RowActionTone,
} from '@/components/ui/row-action'
import { SearchInput } from '@/components/ui/search-input'
import { useListControls } from '@/components/ui/use-list-controls'
import { Select } from '@/components/ui/select'
import { StatCard } from '@/components/ui/stat-card'
import { StepIndicator } from '@/components/ui/step-indicator'
import { Switch } from '@/components/ui/switch'
import { Tabs, type TabItem } from '@/components/ui/tabs'
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
const tabItems: TabItem[] = [
  { id: 'Details', label: 'Details' },
  { id: 'Activiteit', label: 'Activiteit' },
  { id: 'Instellingen', label: 'Instellingen' },
]
const tabItemsWithIcons: TabItem[] = [
  { id: 'Details', label: 'Details', icon: Settings },
  { id: 'Activiteit', label: 'Activiteit', icon: MessageSquare },
  { id: 'Instellingen', label: 'Instellingen', icon: Mic },
]
const tabItemsWithCount: TabItem[] = [
  { id: 'Details', label: 'Details' },
  { id: 'Activiteit', label: 'Activiteit', count: 3 },
  { id: 'Instellingen', label: 'Instellingen' },
]
const badgeVariants = ['default', 'secondary', 'accent', 'outline', 'info', 'success', 'warning', 'destructive'] as const
const alertVariants = ['info', 'success', 'warning', 'destructive'] as const
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
const dividerListGrid = 'lg:grid-cols-[minmax(0,1fr)_144px]'

// Sample collection for the "Volledig lijstoverzicht" anatomy section. 14 rows
// so it crosses the 10-item threshold and the search + pagination chrome shows.
interface OverviewItem {
  id: number
  name: string
  scope: 'org' | 'personal'
  description: string
}
const overviewItems: OverviewItem[] = [
  { id: 1, name: 'Klantenservice-toon', scope: 'org', description: 'Vaste toon en duidelijke escalatieregels voor support-antwoorden.' },
  { id: 2, name: 'Sales follow-up', scope: 'org', description: 'Opent met de afgesproken volgende stap na een demo.' },
  { id: 3, name: 'Notulen samenvatten', scope: 'personal', description: 'Korte besluitenlijst met actiehouders.' },
  { id: 4, name: 'Juridische review', scope: 'org', description: 'Markeer risicovolle clausules en stel alternatieven voor.' },
  { id: 5, name: 'E-mail opschonen', scope: 'personal', description: 'Herschrijf naar bondige, vriendelijke taal.' },
  { id: 6, name: 'Productupdate', scope: 'org', description: 'Changelog-toon, gericht op de waarde voor de klant.' },
  { id: 7, name: 'Sollicitatiescreening', scope: 'org', description: 'Toets cv tegen de functievereisten zonder bias.' },
  { id: 8, name: 'Vertaal NL → EN', scope: 'personal', description: 'Behoud merkterminologie en formele aanspreekvorm.' },
  { id: 9, name: 'Bugrapport triëren', scope: 'org', description: 'Stel ernst en eerste reproductiestap voor.' },
  { id: 10, name: 'Blogpost-opzet', scope: 'personal', description: 'Kop, tussenkoppen en een pakkende intro.' },
  { id: 11, name: 'Onboarding-checklist', scope: 'org', description: 'Stappen voor een nieuwe medewerker in week één.' },
  { id: 12, name: 'Vergaderagenda', scope: 'personal', description: 'Doel, onderwerpen en tijdsindeling.' },
  { id: 13, name: 'Social caption', scope: 'org', description: 'Korte, merkconforme caption met één call-to-action.' },
  { id: 14, name: 'Code review-toon', scope: 'personal', description: 'Constructief, concreet en zonder jargon.' },
]

function UiCatalogPage() {
  const [confirming, setConfirming] = useState(false)
  const [tableDeleteConfirm, setTableDeleteConfirm] = useState(false)
  const [checked, setChecked] = useState(true)
  const [switchChecked, setSwitchChecked] = useState(false)
  const [wizardStep, setWizardStep] = useState(1)
  const [activeTab, setActiveTab] = useState('Details')
  const [multiValue, setMultiValue] = useState<string[]>(['kb'])
  // Inline edit catalog: a "component" row (name only) and a "subtest" row
  // (name + description), each with its own edit + delete-confirm state.
  const [compEditing, setCompEditing] = useState(false)
  const [compName, setCompName] = useState('Klai component')
  const [compDescription, setCompDescription] = useState(
    'Bewerk de naam of verwijder — alles inline, geen overlap.',
  )
  const [compDeleteConfirm, setCompDeleteConfirm] = useState(false)
  const [subEditing, setSubEditing] = useState(false)
  const [subName, setSubName] = useState('Toon van de afzender')
  const [subDescription, setSubDescription] = useState(
    'Controleert of het antwoord de juiste tone of voice aanhoudt.',
  )
  const [subDeleteConfirm, setSubDeleteConfirm] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [radioValue, setRadioValue] = useState('personal')
  const [demoPage, setDemoPage] = useState(5)
  const overview = useListControls(overviewItems, {
    pageSize: 10,
    filter: (item, query) => {
      const q = query.trim().toLowerCase()
      return (
        item.name.toLowerCase().includes(q) ||
        item.description.toLowerCase().includes(q)
      )
    },
  })

  if (!import.meta.env.DEV) {
    return (
      <div className="mx-auto max-w-3xl px-6 pt-4 pb-10">
        <p className="text-sm text-gray-400">UI catalog is alleen lokaal beschikbaar.</p>
      </div>
    )
  }

  return (
    <main className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-10">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          UI catalog
        </h1>
        <p className="text-sm text-gray-400">
          Lokale referentie voor list rows, row actions en basiscomponenten.
        </p>
      </div>

      <div className="divide-y divide-gray-200">
      <Section title="Volledig lijstoverzicht">
        <div className="space-y-6">
          <p className="text-sm text-gray-500">
            De volledige anatomie van een lijst-/overzichtspagina, achter
            elkaar: <strong>PageHeader</strong> (titel + korte subtitel +
            primaire actie) → <strong>PageIntro</strong> (uitleg) →{' '}
            <strong>SearchInput</strong> → lijst of tabel →{' '}
            <strong>Pagination</strong>. Header, subheader en uitleg volgen{' '}
            <code>/app/instructions</code>; de search volgt{' '}
            <code>/admin/users</code>.
          </p>

          {/* Header + subheader + uitleg — exact de /app/instructions copy. */}
          <PageHeader
            title="Instructies"
            count={overviewItems.length}
            description="Beheer instructies die je in chats kunt aanzetten."
            actions={
              <Button size="sm">
                <Plus className="h-4 w-4" />
                Nieuwe instructie
              </Button>
            }
          />
          <PageIntro>
            <p>
              Een instructie is een stukje tekst dat je voor een chat aanzet.
              Klai pakt het op als startpunt, zodat je niet elke keer dezelfde
              uitleg hoeft te typen.
            </p>
            <p>
              <span className="text-gray-500">Bijvoorbeeld:</span>{' '}
              klantenservice-antwoorden met een vaste toon en duidelijke
              escalatieregels, of een sales follow-up na een demo die opent met
              de afgesproken volgende stap.
            </p>
            <p>
              Aanzetten doe je via de Instructies-knop onderaan de chat. Je kunt
              er meerdere tegelijk aan hebben staan.
            </p>
          </PageIntro>

          {/* Search verschijnt alleen bij meer dan 10 items (useListControls). */}
          {overview.showSearch && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                placeholder="Zoek op naam of omschrijving..."
                value={overview.query}
                onChange={(e) => overview.setQuery(e.target.value)}
                aria-label="Zoek instructies"
              />
            </div>
          )}

          {overview.pageItems.length === 0 ? (
            <ListFrame>
              <ListEmptyState
                title="Geen resultaten"
                description="Pas je zoekopdracht aan."
              />
            </ListFrame>
          ) : (
            <ListFrame>
              {overview.pageItems.map((item) => (
                <ListRow
                  key={item.id}
                  interactive
                  className="grid items-center gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto]"
                >
                  <ListRowContent>
                    <div className="flex items-center gap-2 flex-wrap">
                      <ListRowTitle>{item.name}</ListRowTitle>
                      <Badge variant="secondary">
                        {item.scope === 'org' ? 'Organisatie' : 'Persoonlijk'}
                      </Badge>
                    </div>
                    <ListRowDescription>{item.description}</ListRowDescription>
                  </ListRowContent>
                  <ListRowActions
                    className="self-center justify-self-end"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <RowActionGroup>
                      <BorderedRowActionIconButton label="Bewerken" action="edit" />
                      <BorderedRowActionIconButton label="Verwijderen" action="delete" />
                    </RowActionGroup>
                  </ListRowActions>
                </ListRow>
              ))}
            </ListFrame>
          )}

          {/* Pagination verschijnt alleen als de gefilterde set > 10 is. */}
          {overview.showPagination && (
            <Pagination
              page={overview.page}
              pageCount={overview.pageCount}
              onPageChange={overview.setPage}
            />
          )}

          {/* Geschreven standaard naast de gerenderde proof hierboven. */}
          <div className="space-y-4 rounded-xl border border-gray-200 bg-gray-50/50 p-4 text-sm text-gray-600">
            <div className="space-y-2">
              <p className="font-medium text-gray-900">Lijst, lijst-met-header of tabel?</p>
              <ul className="space-y-2">
                <li>
                  <span className="font-medium text-gray-900">DividerList (geen header)</span>{' '}
                  — rij = titel (+ optioneel één regel omschrijving) + acties; de
                  taak is openen/bewerken. Voor dit soort overzichten (instructies,
                  navigatie, bronnen). Dit voorbeeld gebruikt deze variant.
                </li>
                <li>
                  <span className="font-medium text-gray-900">DividerList mét <code>ListHeader</code></span>{' '}
                  — rij draagt twee of meer korte metadata-attributen die je over
                  rijen scant (rol, type, status, datum), maar het blijft een
                  beheerscherm dat op mobiel naar een gestapelde kaart degradeert.
                  Header is <code>hidden … lg:grid</code> en deelt exact dezelfde
                  grid + <code>px-4</code> als de rijen. Referentie:{' '}
                  <code>/admin/users</code>.
                </li>
                <li>
                  <span className="font-medium text-gray-900">DataTable</span>{' '}
                  — dichte tabellaire data waar kolomvergelijking dé taak is en
                  echte <code>&lt;table&gt;</code>-semantiek telt; geen mobiele
                  kaart-stack nodig. Zie de aparte sectie "Data table".
                </li>
              </ul>
            </div>
            <div className="space-y-2">
              <p className="font-medium text-gray-900">Search + pagination: drempel = 10</p>
              <ul className="space-y-2">
                <li>10 items of minder → toon alles, géén search, géén pagination.</li>
                <li>Meer dan 10 → search boven, 10 per pagina, pagination eronder.</li>
                <li>
                  Search filtert de volledige set; pagination verschijnt zodra de
                  gefilterde set groter is dan 10; search blijft zichtbaar zolang
                  de ongefilterde set groter is dan 10 (zodat je kunt wissen).
                </li>
                <li>
                  Eén plek regelt dit: <code>useListControls(items, {'{'} pageSize: 10, filter {'}'})</code>{' '}
                  geeft <code>pageItems</code>, <code>showSearch</code> en{' '}
                  <code>showPagination</code> terug — geen losse <code>&gt; 10</code>-checks per pagina.
                </li>
              </ul>
            </div>
          </div>
        </div>
      </Section>
      <Section title="Page header">
        <div className="space-y-6">
          <PageHeader
            title="Groepen"
            count={12}
            description="Groepen bepalen welke kennisbanken een team mag gebruiken. Voor profielen ga je naar Profielen."
            actions={
              <Button size="sm">
                <Plus />
                Groep aanmaken
              </Button>
            }
          />
          <p className="text-xs text-gray-400">
            Subtitel is gecapt op <code>sm:max-w-[60%]</code> zodat hij nooit
            onder de primaire actie doorloopt. Houd de subtitel kort; langere
            uitleg hoort in een <code>PageIntro</code> hieronder.
          </p>
          <PageIntro>
            <p>
              Gebruik PageIntro voor de uitlegtekst boven een lijst: platte
              tekst, geen kader, iets donkerder (<code>text-gray-600</code>) dan
              de subtitel.
            </p>
            <p>
              <span className="text-gray-500">Bijvoorbeeld:</span> leg uit waar
              de feature voor dient voordat de gebruiker de lijst ziet.
            </p>
          </PageIntro>
        </div>
      </Section>
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

          <RowActionGroup className="justify-start">
            <BorderedRowActionIconButton label="Inhoud tonen" action="expand" />
            <BorderedRowActionIconButton label="Synchroniseren" action="sync" />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <BorderedRowActionIconButton label="Meer acties" action="more" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem>Openen</DropdownMenuItem>
                <DropdownMenuItem>Bewerken</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-[var(--color-destructive)] focus:text-[var(--color-destructive)]">
                  Verwijderen
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </RowActionGroup>
        </div>
      </Section>

      <Section title="Divider lists">
        <ListFrame>
          <ListHeader className={`hidden gap-x-3 ${dividerListGrid} lg:grid`}>
            <span>Naam</span>
            <span className="justify-self-stretch text-right">Acties</span>
          </ListHeader>

          <ListRow interactive className={`grid items-center gap-x-3 px-4 ${dividerListGrid}`}>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <ListRowTitle className="text-sm font-sans font-medium">Kennisbank bronnen</ListRowTitle>
                <Badge variant="secondary">12 bronnen</Badge>
              </div>
              <ListRowDescription>Rustige lijst met links padding en compacte acties.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center justify-self-end">
              <BorderedRowActionIconButton label="Openen" action="open" />
              <BorderedRowActionIconButton label="Bewerken" action="edit" />
              <BorderedRowActionIconButton label="Verwijderen" action="delete" />
            </ListRowActions>
          </ListRow>

          <ListRow interactive className={`grid items-center gap-x-3 px-4 ${dividerListGrid}`}>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <ListRowTitle className="text-sm font-sans font-medium">Persoonlijke kennis</ListRowTitle>
                <Badge variant="success">Gesynct</Badge>
              </div>
              <ListRowDescription>Meta-informatie blijft subtiel en truncatet op een regel.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center justify-self-end">
              <BorderedRowActionIconButton label="Synchroniseren" action="sync" />
              <BorderedRowActionIconButton label="Openen" action="open" />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <BorderedRowActionIconButton label="Meer acties" action="more" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem>Configureren</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="text-[var(--color-destructive)] focus:text-[var(--color-destructive)]">
                    Verwijderen
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </ListRowActions>
          </ListRow>

          <ListRow confirming={confirming} className={`grid items-center gap-x-3 px-4 ${dividerListGrid}`}>
            <ListRowContent>
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-gray-400" />
                <ListRowTitle className="text-sm font-sans font-medium">Inline delete confirm</ListRowTitle>
              </div>
              <ListRowDescription>De overlay houdt dezelfde action-cell breedte.</ListRowDescription>
            </ListRowContent>
            <ListRowActions className="self-center justify-self-end">
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
          {navListItems.map((item, index) => (
            <ListRow
              key={item.title}
              asChild
              interactive
              className={index === 0 ? 'bg-[var(--color-active)]' : undefined}
            >
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

      <Section title="Data table">
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>Naam</DataTableHead>
              <DataTableHead>Status</DataTableHead>
              <DataTableHead align="right">Acties</DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {['Admin users', 'Widgets', 'Sources'].map((name) => (
              <DataTableRow
                key={name}
                interactive
                confirming={name === 'Sources' && tableDeleteConfirm}
              >
                <DataTableCell className="font-medium">{name}</DataTableCell>
                <DataTableCell>
                  <Badge variant="secondary">Actief</Badge>
                </DataTableCell>
                <DataTableCell align="right" onClick={(e) => e.stopPropagation()}>
                  {name === 'Sources' ? (
                    <InlineDeleteConfirm
                      isConfirming={tableDeleteConfirm}
                      label="Sources verwijderen?"
                      cancelLabel="Annuleren"
                      onConfirm={() => setTableDeleteConfirm(false)}
                      onCancel={() => setTableDeleteConfirm(false)}
                    >
                      <RowActionGroup>
                        <BorderedRowActionIconButton label="Bewerken" action="edit" />
                        <BorderedRowActionIconButton
                          label="Verwijderen"
                          action="delete"
                          onClick={() => setTableDeleteConfirm(true)}
                        />
                      </RowActionGroup>
                    </InlineDeleteConfirm>
                  ) : (
                    <RowActionGroup>
                      <BorderedRowActionIconButton label="Bewerken" action="edit" />
                      <BorderedRowActionIconButton label="Meer acties" action="more" />
                    </RowActionGroup>
                  )}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      </Section>

      <Section title="List states">
        <div className="grid gap-6 sm:grid-cols-2">
          <div className="border-y border-gray-200">
            <ListLoadingState label="Laden..." />
          </div>
          <div className="border-y border-gray-200">
            <ListEmptyState
              icon={FileText}
              title="Geen resultaten"
              description="Gebruik deze rustige lege staat voor lijst- en tabeloverzichten."
            />
          </div>
        </div>
      </Section>

      <Section title="Pagination">
        <div className="space-y-4">
          <Pagination page={demoPage} pageCount={10} onPageChange={setDemoPage} />
          <p className="text-xs text-gray-400">
            Genummerde pagination (standaard): Vorige / klikbare paginanummers
            met <code>…</code> ellipsis / Volgende. Eerste en laatste pagina
            altijd zichtbaar, huidige pagina gemarkeerd en niet klikbaar,
            ellipsis nooit aan begin of eind. Een venster van{' '}
            <code>siblingCount</code> (standaard 1) rond de huidige pagina; bij
            7 of minder pagina's worden alle nummers getoond (geen ellipsis).
            Klik door de pagina's om de ellipsis aan beide kanten te zien
            verschijnen.
          </p>
        </div>
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
        <div className="space-y-8">
          <div className="space-y-2">
            <p className="text-xs text-gray-400">Standaard — alleen tekst (canon)</p>
            <Tabs tabs={tabItems} value={activeTab} onValueChange={setActiveTab} />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-gray-400">Met iconen (spaarzaam, voor detail/instellingen)</p>
            <Tabs tabs={tabItemsWithIcons} value={activeTab} onValueChange={setActiveTab} />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-gray-400">Met count-badge</p>
            <Tabs tabs={tabItemsWithCount} value={activeTab} onValueChange={setActiveTab} />
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
            <Select id="catalog-type" defaultValue="list" containerClassName="max-w-xs">
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
          <div className="flex items-center justify-between gap-4 sm:col-span-2">
            <Label htmlFor="catalog-switch" className="cursor-pointer">
              Automatisch accepteren
            </Label>
            <Switch
              id="catalog-switch"
              checked={switchChecked}
              onCheckedChange={setSwitchChecked}
            />
          </div>
        </div>
      </Section>

      <Section title="Badges">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {badgeVariants.map((variant) => (
              <Badge key={variant} variant={variant}>
                {variant}
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ActionTag state="open">Open</ActionTag>
            <ActionTag state="closed">Closed</ActionTag>
          </div>
        </div>
      </Section>

      <Section title="Alerts">
        <div className="max-w-xl space-y-4">
          <div className="space-y-2">
            {alertVariants.map((variant) => (
              <Alert key={variant} variant={variant}>
                <span className="font-medium capitalize">{variant}</span> — inline
                semantic callout met automatisch icoon en zachte tint.
              </Alert>
            ))}
          </div>
          <div className="space-y-2">
            {alertVariants.map((variant) => (
              <Alert key={variant} variant={variant} size="sm">
                Compacte variant (<code>size="sm"</code>): {variant}.
              </Alert>
            ))}
          </div>
        </div>
      </Section>

      <Section title="Stat cards">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Gebruikers" value={1284} sub="+38 deze maand" />
            <StatCard label="MRR" value="€4.210" sub="€50.520 ARR" />
            <StatCard label="Open feedback" value={3} tone="warning" alert sub="12 totaal" onClick={() => {}} />
            <StatCard label="Chat-fouten" value={0} tone="destructive" />
          </div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard size="sm" label="Bots" value={7} />
            <StatCard size="sm" label="Seats" value={25} />
            <StatCard size="sm" label="Kennisbanken" value={9} />
            <StatCard size="sm" label="Laden" value={undefined} loading />
          </div>
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

      <Section title="Inline edit and delete">
        {/* Canonical inline edit: the InlineEditRow primitive puts the input
            (flex-1) and Save/Cancel (shrink-0) in the same flex row, so they
            can never overlap regardless of how narrow the action cluster is.
            Two cases: a "component" row (name only) and a "subtest" row
            (name + description). Ported from the production CoverageNodeRow
            ("Categorieën & Dekking"). */}
        <ListFrame>
          {/* Component: edit the name, or delete — all inline, rounded fields. */}
          <ListRow confirming={compDeleteConfirm}>
            <InlineEditRow
              isEditing={compEditing}
              value={compName}
              description={compDescription}
              withDescription
              namePlaceholder="Componentnaam"
              descriptionPlaceholder="Korte omschrijving van het component"
              saveLabel="Opslaan"
              cancelLabel="Annuleren"
              onSubmit={({ name, description }) => {
                setCompName(name)
                setCompDescription(description)
                setCompEditing(false)
              }}
              onCancel={() => setCompEditing(false)}
              actions={
                <InlineDeleteConfirm
                  isConfirming={compDeleteConfirm}
                  isPending={false}
                  label="Verwijderen"
                  cancelLabel="Annuleren"
                  onConfirm={() => setCompDeleteConfirm(false)}
                  onCancel={() => setCompDeleteConfirm(false)}
                >
                  <RowActionGroup>
                    <BorderedRowActionIconButton
                      label="Bewerken"
                      action="edit"
                      onClick={() => {
                        setCompDeleteConfirm(false)
                        setCompEditing(true)
                      }}
                    />
                    <BorderedRowActionIconButton
                      label="Verwijderen"
                      action="delete"
                      onClick={() => {
                        setCompEditing(false)
                        setCompDeleteConfirm(true)
                      }}
                    />
                  </RowActionGroup>
                </InlineDeleteConfirm>
              }
            />
          </ListRow>

          {/* Subtest: edit the name AND the description (the second field). */}
          <ListRow confirming={subDeleteConfirm}>
            <InlineEditRow
              isEditing={subEditing}
              value={subName}
              description={subDescription}
              withDescription
              namePlaceholder="Naam van de subtest"
              descriptionPlaceholder="Wat controleert deze subtest?"
              saveLabel="Opslaan"
              cancelLabel="Annuleren"
              onSubmit={({ name, description }) => {
                setSubName(name)
                setSubDescription(description)
                setSubEditing(false)
              }}
              onCancel={() => setSubEditing(false)}
              actions={
                <InlineDeleteConfirm
                  isConfirming={subDeleteConfirm}
                  isPending={false}
                  label="Verwijderen"
                  cancelLabel="Annuleren"
                  onConfirm={() => setSubDeleteConfirm(false)}
                  onCancel={() => setSubDeleteConfirm(false)}
                >
                  <RowActionGroup>
                    <BorderedRowActionIconButton
                      label="Bewerken"
                      action="edit"
                      onClick={() => {
                        setSubDeleteConfirm(false)
                        setSubEditing(true)
                      }}
                    />
                    <BorderedRowActionIconButton
                      label="Verwijderen"
                      action="delete"
                      onClick={() => {
                        setSubEditing(false)
                        setSubDeleteConfirm(true)
                      }}
                    />
                  </RowActionGroup>
                </InlineDeleteConfirm>
              }
            />
          </ListRow>
        </ListFrame>
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

# Klai Portal UI Standards

This is the canonical UI/UX source of truth for `klai-portal/frontend`.
If another design document disagrees with this file, this file wins and the
other document must be updated in the same change.

This file is portal-only. It does not define website, landing-page, marketing,
or public web patterns. Keep those in the website-specific design guidance.

## Live Catalog

A running visual reference of these patterns lives at `/dev/ui`
(`src/routes/dev/ui.tsx`). It renders the owned components with every tone,
state, and layout this document describes. It is DEV-only (gated on
`import.meta.env.DEV`) and never ships to production.

Use it as the paired reference to this file: this document is the written
rule, `/dev/ui` is the rendered proof. When you add or change a shared UI
component, update both — a new tone, state, or variant must appear in the
catalog and be described here in the same change.

## Component Library Reference

All shared UI lives in `src/components/ui/`. The base is **shadcn/ui**
(see `components.json`: style `default`, base color `neutral`, lucide icons,
CSS variables), themed with Klai tokens from `index.css`. On top of the
standard shadcn primitives (`button`, `badge`, `card`, `dialog`,
`alert-dialog`, `dropdown-menu`, `popover`, `command`, `sheet`, `tooltip`,
form controls, `sonner`) sit Klai's own additions (`row-action`, `list`,
`list-state`, `pagination`, `inline-edit-row`, `inline-row-button`,
`inline-delete-confirm`, `step-indicator`, `inline-edit`, `multi-select`,
`query-error-state`, `alert`, `delete-*`) plus the `use-list-controls` hook that drives
overview search + pagination. The widget (`klai-widget`) and website
(`klai-website`) are separate systems with their own components — this
library is portal-only.

Build pages from these; never hand-roll a raw `<button>`, `<input>`,
`<select>`, list row, or delete confirmation with inline Tailwind.

| Component | Purpose | Canonical? |
|---|---|---|
| `button` | All buttons (variants: default/secondary/ghost/outline/destructive; sizes: default/sm/icon) | Yes |
| `page-header` | Page title, short subtitle/count, and right-aligned page action (`PageHeader`); longer explanatory copy below the header uses `PageIntro` | Yes |
| `badge` | Inline status labels (secondary/success/warning/destructive/outline) | Yes |
| `action-tag` | Compact open/closed action-state tag (`ActionTag`, states: `open`, `closed`) | Yes |
| `alert` | Inline semantic callout (`Alert`, variants: info/success/warning/destructive; sizes: default/sm). Soft tint + auto icon, for wizard/form feedback and inline warnings — not a toast, not a modal | Yes |
| `input` `select` `textarea` `label` `checkbox` `switch` | Form controls | Yes |
| `search-input` | Text input with a leading search icon (`SearchInput`) | Yes |
| `row-action` | List/table row actions: `RowActionIconButton`, `BorderedRowActionIconButton` (visible bordered hitbox — the default in tables), `RowActionButton`, `RowActionGroup` + the action→tone system | Yes |
| `data-table` | Admin table primitives: `DataTable`, `DataTableHeader`, `DataTableBody`, `DataTableRow` (`interactive`/`confirming`), `DataTableHead`, `DataTableCell` (`align`) | Yes |
| `list` | List primitives: `ListFrame`, `ListHeader`, `ListRow`, `ListRowContent`, `ListRowTitle`, `ListRowDescription`, `ListRowActions`, `ListRowIcon`, `ListRowChevron` | Yes |
| `list-state` | List/table loading and empty states: `ListLoadingState`, `ListEmptyState` | Yes |
| `pagination` | Numbered pager for overviews (`Pagination`): previous, clickable page numbers with `…` truncation, next; current page highlighted and not clickable. Controlled; pair with `useListControls` | Yes |
| `use-list-controls` | Hook (`useListControls`) encoding the search/pagination threshold + paging math for overviews | Yes |
| `inline-row-button` | The single source for small inline-row action pills (`InlineRowButton`): Save/Cancel, Delete/Cancel, Approve/Deny. Tones: success/destructive/neutral | Yes |
| `inline-edit-row` | Canonical inline edit for a list row (`InlineEditRow`): name + optional description, zero layout shift, owns Save/Cancel | Yes |
| `inline-delete-confirm` | Inline destructive confirmation inside a row (no layout shift) | Yes |
| `inline-edit` | Low-level single-field click-to-edit overlay, for custom row layouts that own their own buttons. Prefer `InlineEditRow` for new rows | Yes |
| `radio-card-group` | Selectable radio option cards (`RadioCardGroup`) | Yes |
| `step-indicator` | Wizard step progress (`StepIndicator`) | Yes |
| `tabs` | Underline tabs (`Tabs`): text + optional icon/count, strong `border-gray-900` active underline. For state/search-param tabs. Router-navigation tab bars (real sub-route links) use `Link` directly with the same look. | Yes |
| `alert-dialog` | Centered confirm dialog for destructive actions outside rows | Yes |
| `dialog` | Generic modal | Yes |
| `dropdown-menu` `popover` `command` | Menus, popovers, command/combobox | Yes |
| `multi-select` | Multi-value select | Yes |
| `tooltip` | Hover/focus tooltips (used by `row-action`) | Yes |
| `sonner` | Toasts (`toast()` feedback) | Yes |
| `card` | Framed repeated items / stat blocks | Yes |
| `stat-card` | Metric tile (`StatCard`): uppercase label + large tabular value + optional sub. Sizes default/sm, `tone` (default/warning/destructive), `alert` frame, optional `onClick` to navigate | Yes |
| `query-error-state` | Standard error block for failed queries | Yes |
| `sheet` | Slide-over. **Forbidden** for admin entity detail (see Detail And Edit) | Restricted |
| `delete-kb-modal` `delete-org-modal` | Feature-specific destructive modals (not generic) | Feature |

Tabs are the owned `tabs` component (see Tabs).

## Required Workflow

Before changing portal UI:

1. Read this file.
2. Find an existing screen in the same area with the same pattern.
3. Follow that pattern before inventing a new one.
4. State the reference screen in the work notes before editing.

Reference screens:

| Pattern | Reference |
|---|---|
| Admin create form | `src/routes/admin/users/invite.tsx` |
| Admin detail page with tabs | `src/routes/admin/widgets/$id.tsx` |
| Admin detail page with tabs | `src/routes/admin/api-keys/$id.tsx` |
| Account tabs with counters | `src/routes/app/account.tsx` |
| Platform list tables | `src/routes/admin/platform/-components/PlatformDashboardTabs.tsx` |

## Layout

| Page type | Container |
|---|---|
| List / overview | `mx-auto max-w-3xl px-6 pt-4 pb-10` |
| Form / create | `mx-auto max-w-lg px-6 pt-4 pb-10` |
| Admin detail with tabs | `mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-8` |
| Platform overview | `mx-auto max-w-6xl px-6 pt-4 pb-10 space-y-8` |

Do not use unscoped `p-6` for normal pages. Pages are centered unless the
existing parent surface is intentionally full-width. Authenticated page
containers use `pt-6 pb-10` so page headings sit in visual rhythm with the
sidebar navigation; full-width tool surfaces such as chat own their own layout.

## Headers And Page Actions

List and overview pages use `PageHeader`. The header action belongs in the
same content container as the list/table below it. It aligns to the right edge
of that content width, not the viewport, and sits on the title row so the
primary page action reads as part of the page heading.

```tsx
import { PageHeader, PageIntro } from '@/components/ui/page-header'

<PageHeader
  title={title}
  description={shortSubtitle}   // short — a count or one-line subtitle
  actions={
    <Button size="sm" onClick={onCreate}>
      {createLabel}
    </Button>
  }
/>
```

Do not hand-roll page headers with local `flex justify-between` unless the
page has a genuinely custom layout. If the action appears visually detached
from the title or from the right edge of the list/table, use `PageHeader` and
adjust the page container width instead of adding local offsets.

### Description length and `PageIntro`

The `description` is a SHORT subtitle: a count or a single line. It sits below
the title and is muted (`text-gray-400`). When a primary action is present,
`PageHeader` caps it at `sm:max-w-[60%]` of the header width so the subtitle
never runs under the right-aligned action — keep it short and this cap is
never reached.

When a list/overview page needs to actually explain the feature before the
list, do NOT stretch the subtitle. Put the explanation in a `PageIntro` block
below the header — plain text, no card, slightly more readable
(`text-gray-600`) than the subtitle, with `space-y-3` between paragraphs. This
is the `/app/instructions` pattern: title + short subtitle + action, then an
intro body.

```tsx
<PageHeader title={title} description={shortSubtitle} actions={action} />

<PageIntro>
  <p>{introBody}</p>
  <p>{introExamplesOrInvocation}</p>
</PageIntro>
```

`PageIntro` is **optional** — only add it when the page genuinely needs more
than a one-line subtitle. Pages with a self-explanatory list (e.g. groups,
users) use only the short subtitle.

## Back Actions

Back/cancel actions belong in the page header, on the right side, using
`Button variant="ghost" size="sm"`. Do not place a loose back link above the
page title.

```tsx
<div className="flex items-start gap-3">
  <div className="flex-1">
    <h1 className="page-title text-[26px] font-display-bold text-gray-900">
      {title}
    </h1>
    <p className="mt-1 text-sm text-gray-400">{description}</p>
  </div>
  <Button type="button" variant="ghost" size="sm" onClick={onBack}>
    <ArrowLeft className="h-4 w-4 mr-2" />
    {backLabel}
  </Button>
</div>
```

## Lists And Tables

### Choosing: divider list vs divider-list-with-header vs data table

A collection is one of three shapes. Pick by the user's job, not by habit.
The research backing this: start with a list for scanning a single stream;
move to a table only when comparing many aligned columns is the dominant task
(NN/G "Data Tables", uxpatterns.dev "Table vs List vs Cards").

| Shape | Use when | Header? | Reference |
|---|---|---|---|
| **Divider list, headerless** | Each row carries a primary title (+ optional one-line description) and trailing actions. The job is open/edit a single item. | No | `/app/instructions`, the `/app` navigation launcher, knowledge sources |
| **Divider list with `ListHeader`** | Each row carries two or more short metadata attributes the user scans across rows (role, type, status, date), but it is still a "manage these entities" surface that must degrade to a stacked card on mobile. | Yes — responsive grid, `hidden … lg:grid` | `/admin/users` (`UsersTable`) |
| **`DataTable`** | Dense tabular data where comparison across aligned columns is the dominant task and real `<table>` semantics matter; no mobile card-stack needed. | Yes (`DataTableHeader`) | admin profiles pages |

When does a divider list get a header? **Only** when its rows use a
multi-column grid with two or more metadata columns beyond the title. A plain
title/description list is always headerless. The header must share the exact
grid template and `px-4` padding as the rows (including the trailing action
column) and is `lg:`-only, because column labels only make sense at desktop
width — on mobile the row stacks and the labels would be noise.

### Search and pagination: the 10-item threshold

Overview controls appear only once a collection outgrows a single page. Short
lists get no search box and no pager — they are chrome that a 6-row list does
not need (NN/G: short lists do not warrant pagination; offer one default page
size). The rule, encoded once in `useListControls` so it never drifts into
copy-pasted `> 10` checks:

- Default `pageSize` is **10**.
- `items ≤ 10` → render everything, **no** search, **no** pagination.
- `items > 10` → `SearchInput` above (the `/admin/users` pattern), 10 rows per
  page, `Pagination` below.
- Search filters the full set. The pager shows when the **filtered** set is
  longer than one page; the search box stays visible while the **unfiltered**
  set is longer than one page (so a narrowed query can still be cleared).

```tsx
import { useListControls } from '@/components/ui/use-list-controls'
import { SearchInput } from '@/components/ui/search-input'
import { Pagination } from '@/components/ui/pagination'

const list = useListControls(items, {
  pageSize: 10,
  filter: (item, q) => item.name.toLowerCase().includes(q.trim().toLowerCase()),
})

{list.showSearch && (
  <div className="max-w-sm">
    <SearchInput value={list.query} onChange={(e) => list.setQuery(e.target.value)} />
  </div>
)}

<ListFrame>{list.pageItems.map(renderRow)}</ListFrame>

{list.showPagination && (
  <Pagination page={list.page} pageCount={list.pageCount} onPageChange={list.setPage} />
)}
```

`Pagination` is presentational and controlled — it owns no page state; pair it
with `useListControls`. It renders the canonical numbered pager (W3C / USWDS /
Carbon / MUI convention): a previous control, clickable page numbers, and a
next control. The first and last page are always shown, a window of
`siblingCount` pages (default 1) surrounds the current page, and skipped pages
collapse to a non-clickable `…` ellipsis that never sits at the first or last
slot. The current page is highlighted (`bg-gray-900 text-white`) and is not a
button. Previous/next disable at the bounds. The `/dev/ui` "Volledig
lijstoverzicht" section renders the full anatomy (PageHeader → PageIntro →
search → list → pager) as the proof.

### Building the rows

Use table/list views for admin collections. Never hand-roll a `<table>` with
manual `th`/`td` padding classes — use the `data-table` primitives so every
admin table shares the same `px-4` cell rhythm, header treatment, `klai-hover`,
and right-aligned action column.

```tsx
import {
  DataTable, DataTableHeader, DataTableBody,
  DataTableRow, DataTableHead, DataTableCell,
} from '@/components/ui/data-table'

<DataTable>
  <DataTableHeader>
    <DataTableRow>
      <DataTableHead>Naam</DataTableHead>
      <DataTableHead align="right">Acties</DataTableHead>
    </DataTableRow>
  </DataTableHeader>
  <DataTableBody>
    <DataTableRow interactive confirming={isConfirming} onClick={open}>
      <DataTableCell>{name}</DataTableCell>
      <DataTableCell align="right" onClick={(e) => e.stopPropagation()}>
        <RowActionGroup>…</RowActionGroup>
      </DataTableCell>
    </DataTableRow>
  </DataTableBody>
</DataTable>
```

- `DataTableRow interactive` adds `klai-hover` + pointer; `confirming` tints the
  row (`bg-[var(--color-hover)]`) so an inline delete-confirm overlay has no
  seam. Never re-add `hover:bg-gray-50` or a hand-written confirm tint class.
- `DataTableHead`/`DataTableCell` take `align="right"` for the action column.
  Put `onClick={(e) => e.stopPropagation()}` on the action cell when the row is
  clickable so action clicks don't trigger row navigation.
- Admin profiles pages are the reference implementations.

For a stack of titled rows with a description and trailing actions (not column
headers), prefer the `list` primitives instead (see List Primitives). Use
`DataTable` when you have columns with headers and aligned cells.

`ListHeader` is optional. Use it only when a divider list has column-like
metadata that needs labels. Put it as the first child of `ListFrame`. The
header and every row must share the same grid template and `px-4` horizontal
padding, including the trailing action column, so the top/header/row divider
lines span the full content width. The action column must be wider than the
widest visible action group (for example `144px` for three 32px icon buttons
with two `gap-1` gaps) so the buttons do not feel pressed into the page margin.
Keep the action group right-aligned inside that column with `justify-self-end`,
and right-align the header label inside the fixed action column. Plain
title/description lists stay headerless.

Row actions (edit, delete, sync, ...) use the `row-action` components, never
raw `<button>` icons. See Row Actions And Action Tones.

Loading and empty states for list/table collections use `list-state`, not
loose `<p className="py-8 text-sm text-gray-400">...` snippets:

```tsx
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'

{isLoading ? (
  <ListLoadingState label={m.admin_shared_loading()} />
) : rows.length === 0 ? (
  <ListEmptyState title={m.some_empty()} />
) : (
  <table>...</table>
)}
```

Failed queries use `QueryErrorState` with an explicit retry action where
available.

## Row Actions And Action Tones

Row-level actions use the owned `row-action` components. They encode a fixed
action→tone→color mapping so the same action looks identical everywhere.

Components:

- `RowActionIconButton` — icon-only action (the default in lists/tables).
- `RowActionButton` — icon + text label action.
- `RowActionGroup` — right-aligned flex container with `gap-1` for a row's
  actions.

Pass an `action` and the icon, tone, and tooltip default are derived for you:

```tsx
import { RowActionGroup, RowActionIconButton } from '@/components/ui/row-action'

<RowActionGroup>
  <RowActionIconButton label="Bewerken" action="edit" />
  <RowActionIconButton label="Synchroniseren" action="sync" />
  <RowActionIconButton label="Verwijderen" action="delete" />
</RowActionGroup>
```

Every `action` maps to one tone; never restyle the icon color by hand. The
tone is overridable via the `tone` prop only when a specific row genuinely
needs a different semantic.

| Tone | Color token | Meaning | Example actions |
|---|---|---|---|
| `neutral` | `text-gray-400/500` | Utility, navigation, low-risk | rename, configure, open, view, copy, more, cancel |
| `primary` | `var(--color-primary)` | Primary create / submit / send | add, send |
| `info` | `var(--color-info-text)` | Information, progress, system context | info |
| `success` | `var(--color-success)` | Positive status change or recovery | sync, save, reactivate |
| `warning` | `var(--color-warning)` | Caution / reversible risky action | edit, retry, suspend |
| `danger` | `var(--color-destructive)` | Destructive or high-impact | delete, stop, leave, offboard |

Note: `edit` is tone `warning` (amber) — editing is a reversible change that
deserves a caution cue, distinct from neutral navigation.

The full action→tone and action→icon maps are the single source of truth in
`src/components/ui/row-action.tsx` (`rowActionToneByAction`, `rowActionIcons`).
Add new actions there, not ad hoc per page.

### Row action order and overflow

Order row actions by user intent, left to right:

1. Row toggle or drill-down (`expand`/`collapse`).
2. Operational refresh/recovery (`sync`, `retry`, `reauth`).
3. Open/navigate actions (`open`, `external`, docs editor).
4. Edit actions (`edit`, `rename`, `configure`).
5. Destructive actions (`delete`, `leave`, `offboard`, `stop`).

Edit actions always come before delete actions. Delete is always the final
action in a visible row-action group or in an overflow menu.

Show at most three direct controls in a row action cell. If a row has four or
more actions, keep the highest-frequency two actions visible, then use a
`more` row action (`DropdownMenu`) for the rest. Preserve the same order inside
the menu and keep destructive actions last. Do not split old and new action
patterns side by side in the same row.

### Bordered action icons

When actions need a visible affordance (outlined icon buttons), the border
must match the icon color. Use `border border-current` so the border inherits
the tone's `currentColor` — never a hardcoded border color and never an inline
`style`. See Borders And Cascade Layers for why this works.

## List Primitives

`list` provides the standard "stack of rows" surface used across the portal.

```tsx
import {
  ListFrame, ListRow, ListRowContent, ListRowTitle,
  ListRowDescription, ListRowActions,
} from '@/components/ui/list'

<ListFrame>
  <ListRow interactive>
    <ListRowContent>
      <ListRowTitle>Kennisbank bronnen</ListRowTitle>
      <ListRowDescription>Rustige lijst met compacte acties.</ListRowDescription>
    </ListRowContent>
    <ListRowActions className="self-center">
      <RowActionIconButton label="Bewerken" action="edit" />
      <RowActionIconButton label="Verwijderen" action="delete" />
    </ListRowActions>
  </ListRow>
</ListFrame>
```

- `ListFrame` draws the `divide-y` separators and top/bottom border.
- `ListRow interactive` adds `klai-hover` + pointer; `confirming` tints the
  row while a destructive confirm is open.
- `ListRowTitle` truncates on one line; `ListRowDescription` is the muted
  secondary line.
- `ListRowActions` is the trailing action cell; put a `RowActionGroup` or
  loose `RowActionIconButton`s inside.
- For admin collection rows where the first cell needs a primary label plus
  secondary metadata (for example user name + email), use `ListFrame`/`ListRow`
  with a responsive grid inside the row. `UsersTable`
  (`admin/users/_components/UsersTable.tsx`) is the reference: name and email
  live in `ListRowTitle`/`ListRowDescription`, metadata is in compact grid
  cells, and actions use `ListRowActions` on the right.

### Navigation list

A list whose rows navigate somewhere (the `/app` tool launcher) is the same
primitives with the row as a link and a trailing `ListRowChevron` instead of
actions. Do not hand-roll this — reuse the primitives so the icon/title/
description rhythm stays identical to other lists.

```tsx
<ListFrame>
  <ListRow asChild interactive>
    <a href={tool.href}>
      <ListRowIcon><tool.icon className="h-4 w-4" /></ListRowIcon>
      <ListRowContent>
        <ListRowTitle>{tool.title}</ListRowTitle>
        <ListRowDescription>{tool.description}</ListRowDescription>
      </ListRowContent>
      <ListRowChevron />
    </a>
  </ListRow>
</ListFrame>
```

## Inline Edit (rows)

Editing a list row's name (and optionally a description) in place uses
`InlineEditRow`. It is the canonical pattern, ported from the production
`CoverageNodeRow` ("Categorieën & Dekking").

```tsx
import { InlineEditRow } from '@/components/ui/inline-edit-row'

<ListRow confirming={confirmingDelete}>
  <InlineEditRow
    isEditing={editingId === row.id}
    value={row.name}
    description={row.description}
    withDescription            // omit for a name-only row
    saveLabel={m.save()}
    cancelLabel={m.cancel()}
    onSubmit={({ name, description }) => saveMutation.mutate({ name, description })}
    onCancel={() => setEditingId(null)}
    actions={/* view-mode right cluster: edit/delete icons, InlineDeleteConfirm */}
  />
</ListRow>
```

Why it looks the way it does (do not regress these):

- **Zero layout shift.** Each field keeps its view text in the DOM as an
  invisible ghost that defines the box height; the `<input>` is painted
  `absolute inset-0` on top. Toggling edit never changes the row height.
- **No glyph jump.** The input cancels its own `px-1` with `-ml-1`, so the
  text starts at the exact same x as the ghost (the rounded field bleeds 4px
  into the row gutter instead of pushing the text right).
- **No overlap.** The content block is `flex-1` and the Save/Cancel cluster
  `shrink-0`; the inputs shrink to make room for the buttons rather than
  sitting underneath them.
- **Actions are vertically centred** against the whole (possibly two-line)
  block via the row's `items-center`.
- Buffer state is seeded only on the false → true edit transition (a `useRef`
  guard), so a query refetch cannot wipe typed input mid-edit.

### Inline row buttons (`InlineRowButton`)

Every small inline-row action pill — Save/Cancel, Delete/Cancel, Approve/Deny —
renders through `InlineRowButton`, the single source of truth for their size
and tone. Standard: `h-6 text-xs` with `size-3` icons. Tones: `success`
(green-filled), `destructive` (red-filled), `neutral` (ghost). Never hand-roll
a `h-6 text-[10px]`/`text-xs` Button pill again — it drifts.

```tsx
import { InlineRowButton } from '@/components/ui/inline-row-button'

<InlineRowButton tone="success" onClick={save}><Check /> {m.save()}</InlineRowButton>
<InlineRowButton onClick={cancel}><X /> {m.cancel()}</InlineRowButton>
```

## Inline Delete Confirmation

Destructive actions inside a row use `InlineDeleteConfirm` — the canonical
inline confirm. It keeps the original actions in the DOM as an invisible
ghost spacer and overlays the confirm/cancel controls absolutely, so the row
never shifts width when confirming.

```tsx
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'

<InlineDeleteConfirm
  isConfirming={confirmDeleteId === row.id}
  isPending={deleteMutation.isPending}
  label={m.source_delete_confirm({ name: row.name })}
  cancelLabel={m.cancel()}
  onConfirm={() => deleteMutation.mutate(row.id)}
  onCancel={() => setConfirmDeleteId(null)}
>
  <RowActionGroup>
    <RowActionIconButton label="Bewerken" action="edit" />
    <RowActionIconButton
      label="Verwijderen"
      action="delete"
      onClick={() => setConfirmDeleteId(row.id)}
    />
  </RowActionGroup>
</InlineDeleteConfirm>
```

Rules:

- Use this for row-level deletes. It is controlled — you own the
  `confirmDeleteId` / pending state.
- **Tint the row while confirming.** The confirm overlay paints
  `bg-[var(--color-hover)]` so it can cover the meta text behind it. The row
  it sits in MUST get the SAME background while confirming, or a hard seam
  shows where the overlay meets the untinted row. `ListRow confirming` does
  this for you; a raw `<tr>`/`<div>` row must add
  `bg-[var(--color-hover)]` on the confirming branch (see
  `TranscriptionTable`, `SourceRow`).
- The confirm button is destructive-filled with the action label; cancel is a
  ghost `X`. Both render through `InlineRowButton` (the shared inline-pill) —
  do not restyle.
- For destructive actions that are NOT in a row (e.g. a whole page or card),
  use `alert-dialog` instead.
- Never use `window.confirm`.

## Wizards

Multi-step flows (create knowledge base, add connector, new API key, new
widget) use `StepIndicator` for progress.

```tsx
import { StepIndicator } from '@/components/ui/step-indicator'

<StepIndicator
  steps={[
    { label: 'Details' },
    { label: 'Bron', onClick: () => setStep(1) },
    { label: 'Bevestigen' },
  ]}
  currentIndex={step}
/>
```

- Active step: solid `gray-900` pill with its number.
- Completed step: light pill with a check; clickable to jump back only when
  `onClick` is provided.
- Future step: muted pill, not clickable.
- Wizard steps containing a `type="password"` / secret field must be wrapped
  in a `<form>` with `onSubmit` advancing the step (browser autofill).

## Tabs

Underline tabs for authenticated app/admin surfaces use the owned `Tabs`
component (`components/ui/tabs.tsx`). It is the single source for the tab look —
do not hand-roll a tab row again.

```tsx
import { Tabs, type TabItem } from '@/components/ui/tabs'

const tabs: TabItem<TabId>[] = [
  { id: 'details', label: m.account_tab_settings() },
  { id: 'feedback', label: m.account_tab_feedback(), count: unreadCount },
]

<Tabs tabs={tabs} value={activeTab} onValueChange={setTab} />
```

- **Active state is a strong `border-gray-900` underline** — unmistakable
  against the gray-200 container divider. (The old `border-gray-200` active
  style was a defect: it blended into the divider.)
- **Text-only is the default, on-brand look.** `icon` and `count` are
  optional. Use icons sparingly (detail/settings surfaces); the `count` badge
  takes an optional `countTone` (`success` default, or `warning`/`destructive`/
  `info`).
- `Tabs` is **controlled and presentational** — it owns no state. Wire
  `value`/`onValueChange` to local state or to URL search state when the tab
  must survive navigation (the account/api-keys/widgets/platform pattern).
- Do not use pill tabs for admin/account/detail surfaces.
- For a tab bar with many tabs that must scroll on small screens, pass
  `className="overflow-x-auto"` (the platform dashboard pattern).

**Router-navigation tab bars** — where each tab is a real sub-route (different
pathname), not a search-param toggle — keep using TanStack `<Link>` directly
with the same underline classes, because they need true `<a>` semantics
(open-in-new-tab, prefetch). `Tabs` is intentionally router-agnostic and does
not render links. `app/knowledge/$kbSlug/route.tsx` is the reference for this
link-tab variant.

The live render of every variant is the `/dev/ui` "Tabs" section — that is the
canonical rendered reference.

## Detail And Edit

Detail/edit views are separate routes when they represent a real entity or a
review workflow. Do not introduce drawers, sheets, or inline detail panels in
admin surfaces unless that exact module already uses them.

Forbidden in admin detail/list work:

- New `Drawer` or `Sheet` imports.
- Inline detail forms opened under a table row.
- Loose back links above the header.
- `window.confirm`.

Use `AlertDialog` or the existing inline delete confirmation component for
destructive actions.

## Forms

Use owned UI components from `src/components/ui/`.

Every field needs `Label` plus matching `id`/`htmlFor`.

```tsx
<div className="space-y-1.5">
  <Label htmlFor="field-id">Label</Label>
  <Input id="field-id" value={value} onChange={...} />
</div>
```

- **Search fields** use `SearchInput` (leading magnifier icon), never a bare
  `Input` with a hand-placed icon.
- **`Select`** renders a custom chevron; its right padding is symmetric with
  the left text padding. Do not re-add a native arrow or a second icon.
- **Inline value edits** in a list row use `InlineEditRow` (see "Inline Edit
  (rows)" above): it edits a name + optional description with zero layout
  shift and owns the green Save + ghost Cancel via `InlineRowButton`. The
  amber edit trigger is a bordered action icon (`border border-current`),
  matching the row-action look. `InlineEdit` (the low-level single-field
  overlay) stays available for custom rows that own their own button layout.

### Selectable cards

A single-choice list where each option needs a label + description (role
picker, plan picker) uses `RadioCardGroup`. The selected card gets a dark
border + subtle fill (no amber on active states per the v1 spine).

```tsx
<RadioCardGroup
  options={options}            // { value, label, description }[]
  value={value}
  onChange={setValue}
  aria-label="Profiel"
/>
```

`ProfilePicker` is the role-ladder specialization of this pattern.

## Cards

Cards are individual repeated items, stats, or genuinely framed blocks. Do not
wrap ordinary detail sections inside decorative cards. Cards use `rounded-xl`
and `border-gray-200`.

Metric/stat tiles use the owned `StatCard` component (`components/ui/stat-card.tsx`)
— do not hand-roll a label+value card. It carries an uppercase label, a large
`tabular-nums` value, an optional sub-line, a `size` (default dashboard tile /
`sm` compact inline stat), a `tone` for warning/destructive metrics, an `alert`
frame for items needing action, a `loading` spinner, and an optional `onClick`
that turns the card into a button (clickable stat cards navigate to the matching
tab). Operational alert cards must show what needs action as the primary value.

## Form Controls

Use `components/ui/` controls for every form field and pair fields with
`Label`. For `Select`, width constraints belong on `containerClassName`, not
`className`, because the component owns a wrapper for the custom chevron.

```tsx
<Select id="settings-language" containerClassName="max-w-xs">
  <option value="nl">Nederlands</option>
</Select>
```

Use `Switch` for binary on/off settings. The switch itself only stages the
state when the setting has a save action; persist through the paired save
button so external mutations do not happen merely by toggling the control.

## Chat Disclosure Rows

Use this pattern when a chat answer needs secondary provenance below the
assistant text: sources, agent activity, retrieval metadata, citations, or
other debug/provenance details. These rows should feel available, compact, and
quiet, without competing with the answer.

```tsx
<div className="mt-4 space-y-0.5">
  <details className="group max-w-xl bg-transparent">
    <summary className="inline-flex min-h-7 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-[13px] text-[color:rgb(25_25_24_/_0.5)] hover:bg-[var(--color-muted)]/60 hover:text-gray-900 [&::-webkit-details-marker]:hidden">
      <ChevronRight className="h-3 w-3 shrink-0 text-[color:rgb(25_25_24_/_0.3)] transition-transform group-open:rotate-90" />
      <span className="min-w-0 flex-1 font-medium">Bronnen</span>
      <span className="shrink-0 text-xs font-normal text-[color:rgb(25_25_24_/_0.3)] before:mr-1.5 before:text-[color:rgb(25_25_24_/_0.2)] before:content-['·']">1 bron</span>
    </summary>
    <div className="pb-2 pl-4 pt-1 text-[13px] text-[color:rgb(25_25_24_/_0.5)]">
      ...
    </div>
  </details>
</div>
```

Rules:

- Closed by default. The answer stays primary; provenance is secondary.
- Use inline disclosure controls, not cards or bordered rows: `inline-flex rounded-md bg-transparent`.
- Use `mt-4 space-y-0.5` after answer prose.
- Summary layout is chevron left, title, then a muted inline count.
- Summary typography is `text-[13px] font-medium text-[color:rgb(25_25_24_/_0.5)]`; count is `text-xs font-normal text-[color:rgb(25_25_24_/_0.3)]`.
- Body content is unboxed, lightly indented, and compact: `pl-4 text-[13px] text-[color:rgb(25_25_24_/_0.5)]`.
- Use a small `ChevronRight` from Lucide, rotating with `group-open:rotate-90`.
- Do not use `klai-hover` or loud hover backgrounds for disclosure summaries.
- Do not render `Bronnen` or `Agent activiteit` as plain bold headings below the answer.

## Colors

Use:

- `text-gray-900` for primary text.
- `text-gray-600` for explanatory body copy below a header (`PageIntro`).
- `text-gray-400` for muted descriptions/metadata (the `PageHeader` subtitle).
- `border-gray-200` for borders.
- `klai-hover` for interactive hover states.
- `var(--color-success)`, `var(--color-warning)`, `var(--color-destructive)`
  for semantic states.

Do not use raw Tailwind semantic colors such as `text-green-*`, `text-red-*`,
`bg-amber-*`, or `hover:bg-gray-50` in new portal UI.

For row/action coloring, do not pick colors by hand — use the action tone
system (see Row Actions And Action Tones).

### Semantic badges

Status badges use the `Badge` component with a semantic variant. The semantic
variants (`info`, `success`, `warning`, `destructive`) derive from the SAME
primary tokens as the action tones — same hue, same meaning — kept soft via a
10% tint background with the solid token as text:

```
success  → bg var(--color-success)/10   text var(--color-success)
warning  → bg var(--color-warning)/10   text var(--color-warning)
destructive → bg var(--color-destructive)/10 text var(--color-destructive)
info     → bg var(--color-info)/10      text var(--color-info)
```

So a green status badge and a green sync icon are the same green, just softer.
Structural (non-semantic) variants stay neutral: `secondary` (gray fill with a
neutral border), `outline`, `default`/`accent` (dark). Do not hand-roll status
pills with ad-hoc `/10` tints — use `Badge`.

Use `ActionTag` for compact open/closed action-state tags, such as a row marker
that shows an item is currently active/open. `open` keeps the existing green
outline style; `closed` is neutral gray. Do not recreate these tags with
one-off `border-green-*` or gray pill classes.

Open/closed tags follow the common status-tag pattern: they are read-only
state markers, not actions. Use short adjective labels (`Open`, `Closed`,
`Actief`, `Gesloten`) and never verbs that imply clickability (`Openen`,
`Sluiten`). Show a single positive/open tag when the opposite state is implied
by absence; show both open and closed only in lists or tables where users scan
mixed states. Treat `open` as green only when it means available, active, or
currently usable. Treat `closed` as neutral when it means ended, inactive, or
not currently selected. Use a semantic `Badge` (`destructive`, `warning`) only
when the closed state is actually an error, failure, or risk.

External design-system references checked for this rule: [Scottish Government
Status tag](https://designsystem.gov.scot/components/status-tag), [Atlassian
Lozenge](https://atlassian.design/components/lozenge), [Ontario
Badges](https://designsystem.ontario.ca/components/detail/badges.html),
[Designsystemet Badge](https://designsystemet.no/en/components/docs/badge/overview),
and [CMS Badge](https://design.cms.gov/components/badge/). The shared pattern:
status indicators are non-interactive, use text labels in addition to colour,
keep labels short, use colours consistently with the user's mental model, and
avoid mixing clickable and non-clickable badge-like UI.

## Borders And Cascade Layers

`index.css` sets a default border color for every element so a bare `border`
utility renders in the neutral border color:

```css
@layer base {
  * {
    border-color: var(--color-border);
    outline-color: var(--color-ring);
  }
}
```

This rule MUST stay inside `@layer base`. CSS cascade layers, not specificity,
decide the winner here: unlayered declarations rank after every explicit
layer, so an unlayered `* { border-color }` would beat every Tailwind utility
(which live in `@layer utilities`) regardless of class specificity. When this
rule was unlayered, every colored border utility in the portal
(`border-[var(--color-destructive)]`, `border-current`,
`border-[var(--color-warning)]`, ...) was silently overridden to the neutral
grey — invisible because grey is
close to `gray-200`. Keeping it in `@layer base` lets utilities win, which is
Tailwind v4's intended preflight behaviour.

Consequences for component code:

- A colored border is just a utility: `border border-[var(--color-destructive)]`
  or, to match the current text color, `border border-current`.
- Never fix a border color with an inline `style={{ borderColor }}` — that
  only "works" because inline styles sit outside layers entirely. It is a
  workaround, not the pattern.
- Never add a second unlayered `* { border-color }` or an `@utility` override
  to "win" — fix the layer, not the specificity.

## Overlays, Menus And Feedback

| Need | Component | Notes |
|---|---|---|
| Confirm a destructive action outside a row | `alert-dialog` | Centered, focus-trapped. Row-level deletes use `inline-delete-confirm` instead. |
| Generic modal (form, detail that is genuinely modal) | `dialog` | Not for admin entity detail — those are separate routes. |
| Action menu on a trigger | `dropdown-menu` | Use for "more actions" overflow. |
| Floating content on a trigger | `popover` | Non-menu floating panels. |
| Searchable list / combobox | `command` | Command palette and filterable pickers. |
| Multi-value selection | `multi-select` | |
| Edit a row's name/description in place | `inline-edit-row` | Canonical: zero shift, owns Save/Cancel. `inline-edit` is the low-level single-field overlay for custom rows. |
| Hover/focus hint | `tooltip` | `RowActionIconButton` wires this automatically via `label`. |
| Transient feedback after an action | `sonner` (`toast`) | Success/error confirmations; not for validation errors. |
| A query failed | `query-error-state` | Standard error block with retry. |
| Inline semantic feedback in a form/wizard/page | `alert` | Soft tinted callout (info/success/warning/destructive) with an auto icon. Not a toast (`sonner`) and not a modal (`dialog`). Use `size="sm"` for compact wizard-step feedback. |
| Slide-over panel | `sheet` | **Restricted**: never for admin entity detail (see Detail And Edit). |

Compose these instead of building bespoke overlays. If a needed variant is
missing, add it to the owned component, not to a page.

## Copy And I18n

All user-visible strings go through Paraglide messages:

```tsx
import * as m from '@/paraglide/messages'
```

Do not hardcode Dutch or English labels in page/components code.

## Current Deprecated Patterns

These patterns must not be copied:

- Drawers/sheets for admin entity detail.
- Inline row-expanded edit forms for admin entities.
- Loose page-level back links above titles.
- `window.confirm`.
- `hover:bg-gray-50` on interactive rows.
- Raw semantic Tailwind colors for status states.
- Raw `<button>` icon actions in rows. Use `row-action` components.
- Inline `style={{ borderColor }}` to color a border. Use a `border-*` utility.
- An unlayered `* { border-color }` or `@utility` override. Keep the reset in
  `@layer base` (see Borders And Cascade Layers).
- Hand-picked icon colors for row actions. Use the action tone system.
- Hand-rolled search inputs (`Input` + manually placed icon). Use `SearchInput`.
- Hand-rolled status pills with ad-hoc `/10` tints. Use `Badge` semantic variants.
- Hand-rolled semantic callouts with raw `amber-*`/`red-*`/`green-*` Tailwind
  (icon + message in a tinted box). Use `Alert` semantic variants.
- A delete-confirm overlay on an untinted row. Tint the row while confirming.

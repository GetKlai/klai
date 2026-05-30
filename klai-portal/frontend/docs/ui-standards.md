# Klai Portal UI Standards

This is the canonical UI/UX source of truth for `klai-portal/frontend`.
If another design document disagrees with this file, this file wins and the
other document must be updated in the same change.

This file is portal-only. It does not define website, landing-page, marketing,
or public web patterns. Keep those in the website-specific design guidance.

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
| List / overview | `mx-auto max-w-3xl px-6 py-10` |
| Form / create | `mx-auto max-w-lg px-6 py-10` |
| Admin detail with tabs | `mx-auto max-w-4xl px-6 py-10 space-y-8` |
| Platform overview | `mx-auto max-w-6xl px-6 py-10 space-y-8` |

Do not use unscoped `p-6` for normal pages. Pages are centered unless the
existing parent surface is intentionally full-width.

## Headers And Back Actions

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

Use table/list views for admin collections. Rows use `klai-hover`; never use
`hover:bg-gray-50` for interactive rows.

```tsx
<table className="w-full text-sm border-t border-b border-gray-200">
  <tbody>
    <tr className="border-b border-gray-200 klai-hover cursor-pointer">
      ...
    </tr>
  </tbody>
</table>
```

List actions use familiar icon buttons with tooltips/labels where needed.
Edit/delete must follow the existing admin action style.

## Tabs

Use underline tabs for authenticated app/admin surfaces. Persist the active tab
in URL search state when the tab affects navigation or when detail pages need
to return to the same section.

Do not use pill tabs for admin/account/detail surfaces unless the surrounding
module already does.

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

## Cards

Cards are individual repeated items, stats, or genuinely framed blocks. Do not
wrap ordinary detail sections inside decorative cards. Cards use `rounded-xl`
and `border-gray-200`.

Platform stat cards may be clickable when they navigate to the matching tab.
Operational alert cards use semantic warning/destructive tokens and must show
what needs action as the primary value.

## Chat Disclosure Rows

Use this pattern when a chat answer needs secondary provenance below the
assistant text: sources, agent activity, retrieval metadata, citations, or
other debug/provenance details. These rows should feel available, compact, and
quiet, without competing with the answer.

```tsx
<div className="mt-5 space-y-2">
  <details className="group max-w-xl rounded-lg border border-[color:rgb(232_230_222_/_0.6)] bg-transparent">
    <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 px-2.5 py-1.5 text-[13px] text-[color:rgb(25_25_24_/_0.5)] [&::-webkit-details-marker]:hidden">
      <ChevronRight className="h-3 w-3 shrink-0 text-[color:rgb(25_25_24_/_0.3)] transition-transform group-open:rotate-90" />
      <span className="min-w-0 flex-1 font-medium">Bronnen</span>
      <span className="shrink-0 text-xs font-normal text-[color:rgb(25_25_24_/_0.3)]">1 bron</span>
    </summary>
    <div className="border-t border-[color:rgb(232_230_222_/_0.5)] px-2.5 pb-2 pt-1.5 text-[13px] text-[color:rgb(25_25_24_/_0.5)]">
      ...
    </div>
  </details>
</div>
```

Rules:

- Closed by default. The answer stays primary; provenance is secondary.
- Use standalone rows, not cards inside cards: `rounded-lg border border-[color:rgb(232_230_222_/_0.6)] bg-transparent`.
- Use `mt-5 space-y-2` after answer prose.
- Summary layout is chevron left, title middle, muted count right.
- Summary typography is `text-[13px] font-medium text-[color:rgb(25_25_24_/_0.5)]`; count is `text-xs font-normal text-[color:rgb(25_25_24_/_0.3)]`.
- Body content starts below a 50% token-derived border and stays compact: `text-[13px] text-[color:rgb(25_25_24_/_0.5)]`.
- Use a small `ChevronRight` from Lucide, rotating with `group-open:rotate-90`.
- Do not use `klai-hover` or loud hover backgrounds for disclosure summaries.
- Do not render `Bronnen` or `Agent activiteit` as plain bold headings below the answer.

## Colors

Use:

- `text-gray-900` for primary text.
- `text-gray-400` for muted descriptions/metadata.
- `border-gray-200` for borders.
- `klai-hover` for interactive hover states.
- `var(--color-success)`, `var(--color-warning)`, `var(--color-destructive)`
  for semantic states.

Do not use raw Tailwind semantic colors such as `text-green-*`, `text-red-*`,
`bg-amber-*`, or `hover:bg-gray-50` in new portal UI.

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

import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/app/docs/$kbSlug')({
  validateSearch: (search: Record<string, unknown>) => ({
    page: typeof search.page === 'string' ? search.page : undefined,
  }),
  // component moved to $kbSlug.lazy.tsx
})

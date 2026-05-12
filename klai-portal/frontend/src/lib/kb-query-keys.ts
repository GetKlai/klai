import type { QueryClient } from '@tanstack/react-query'

export const kbQueryKeys = {
  knowledgeBase: (kbSlug: string) => ['app-knowledge-base', kbSlug] as const,
  sources: (kbSlug: string) => ['kb-sources', kbSlug] as const,
  sourceContent: (kbSlug: string, kind: string, id: string) =>
    ['source-content', kbSlug, kind, id] as const,
  kbItems: (kbSlug: string) => ['kb-items', kbSlug] as const,
  personalKnowledge: (kbSlug: string) => ['personal-knowledge', kbSlug] as const,
  statsSummary: () => ['app-knowledge-bases-stats-summary'] as const,
  docsTree: (orgSlug: string | undefined, kbSlug: string) =>
    ['docs-tree', orgSlug, kbSlug] as const,
  connectorsPortal: (kbSlug: string) => ['kb-connectors-portal', kbSlug] as const,
}

export function invalidateKnowledgeSourceLists(
  queryClient: Pick<QueryClient, 'invalidateQueries'>,
  kbSlug: string,
) {
  void queryClient.invalidateQueries({ queryKey: kbQueryKeys.sources(kbSlug) })
  void queryClient.invalidateQueries({ queryKey: kbQueryKeys.kbItems(kbSlug) })
  void queryClient.invalidateQueries({ queryKey: kbQueryKeys.personalKnowledge(kbSlug) })
  void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
}

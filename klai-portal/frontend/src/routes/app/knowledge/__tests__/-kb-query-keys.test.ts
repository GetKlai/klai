import { describe, expect, it, vi } from 'vitest'
import { invalidateKnowledgeSourceLists, kbQueryKeys } from '../$kbSlug/-kb-query-keys'

describe('kbQueryKeys', () => {
  it('invalidates the sources query after source mutations', () => {
    const queryClient = { invalidateQueries: vi.fn() }

    invalidateKnowledgeSourceLists(queryClient, 'klai-web-demo')

    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: kbQueryKeys.sources('klai-web-demo'),
    })
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: kbQueryKeys.kbItems('klai-web-demo'),
    })
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: kbQueryKeys.personalKnowledge('klai-web-demo'),
    })
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: kbQueryKeys.statsSummary(),
    })
  })
})

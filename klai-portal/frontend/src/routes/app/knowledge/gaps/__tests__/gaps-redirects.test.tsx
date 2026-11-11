/**
 * The Knowledge gaps screen replaced three earlier addresses: the old inbox at
 * /app/gaps, and the weekly list's list and group pages under
 * /app/knowledge/gaps/clusters. Each must send a bookmark to the new address,
 * a group keeping its key.
 */
import { describe, it, expect, vi } from 'vitest'

// The redirect config itself is under test, so the route factory hands it back.
vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>('@tanstack/react-router')
  return { ...actual, createFileRoute: () => (cfg: unknown) => cfg }
})

import { Route as LegacyGapsRoute } from '../../../gaps'
import { Route as ClustersListRoute } from '../clusters.index'
import { Route as ClusterGroupRoute } from '../clusters.$clusterKey'

type Redirecting = { beforeLoad: (ctx: { params?: Record<string, string> }) => never }

function redirectOf(route: unknown, ctx: { params?: Record<string, string> } = {}) {
  try {
    ;(route as Redirecting).beforeLoad(ctx)
  } catch (thrown) {
    expect(thrown).toBeInstanceOf(Response)
    const response = thrown as Response & { options?: { to?: string; params?: Record<string, string> } }
    expect(response.status).toBe(307)
    return response.options
  }
  throw new Error('route did not redirect')
}

describe('Knowledge gaps redirects', () => {
  it('sends the old inbox address to the Knowledge gaps screen', () => {
    expect(redirectOf(LegacyGapsRoute)?.to).toBe('/app/knowledge/gaps')
  })

  it('sends the weekly list address to the Knowledge gaps screen', () => {
    expect(redirectOf(ClustersListRoute)?.to).toBe('/app/knowledge/gaps')
  })

  it('sends a weekly list group to the same group under Knowledge gaps', () => {
    const options = redirectOf(ClusterGroupRoute, { params: { clusterKey: 't0123456789abcdef0123' } })
    expect(options?.to).toBe('/app/knowledge/gaps/$clusterKey')
    expect(options?.params).toEqual({ clusterKey: 't0123456789abcdef0123' })
  })
})

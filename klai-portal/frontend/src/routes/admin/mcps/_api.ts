import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'

/**
 * Single source of truth for the MCP server model. Mirrors `McpServerOut`
 * from klai-portal/backend/app/api/mcp_servers.py.
 */
export interface McpServer {
  id: string
  display_name: string
  description: string
  enabled: boolean
  managed: boolean
  required_env_vars: string[]
  configured_env_vars: string[]
}

interface McpServersResponse {
  servers: McpServer[]
}

const QUERY_KEY = ['mcp-servers'] as const

/**
 * Fetches the MCP catalog with per-tenant enable/configure state.
 * Shared across the list, picker and edit routes — TanStack Query dedupes
 * concurrent requests by queryKey.
 */
export function useMcpServers(token: string) {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => apiFetch<McpServersResponse>('/api/mcp-servers', token),
    enabled: !!token,
  })
}

export const mcpServersQueryKey = QUERY_KEY

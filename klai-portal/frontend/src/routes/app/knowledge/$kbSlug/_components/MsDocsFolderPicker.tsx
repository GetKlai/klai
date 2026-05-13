import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Folder, FolderOpen, Loader2 } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { Button } from '@/components/ui/button'

interface MsFolder {
  id: string
  name: string
  child_count: number
}

interface FoldersResponse {
  folders: MsFolder[]
}

interface NodeProps {
  kbSlug: string
  connectorId: string
  folder: MsFolder
  depth: number
  selectedId: string
  onSelect: (id: string) => void
  onSeeName: (id: string, name: string) => void
}

function FolderNode({ kbSlug, connectorId, folder, depth, selectedId, onSelect, onSeeName }: NodeProps) {
  const [expanded, setExpanded] = useState(false)
  const hasChildren = folder.child_count > 0
  const isSelected = selectedId === folder.id

  // Lazy load children only after the user expands this node.
  const { data, isLoading } = useQuery<FoldersResponse>({
    queryKey: ['ms-docs-folders', kbSlug, connectorId, folder.id],
    queryFn: async () =>
      apiFetch<FoldersResponse>(
        `/api/app/knowledge-bases/${encodeURIComponent(kbSlug)}/connectors/${connectorId}/ms-docs/folders?parent=${encodeURIComponent(folder.id)}`,
      ),
    enabled: expanded && hasChildren,
    staleTime: 60_000,
  })

  // Surface every child folder's id+name to the parent so a later
  // ``onConfirm`` can echo the chosen name without a fresh API call.
  useEffect(() => {
    onSeeName(folder.id, folder.name)
    if (data?.folders) {
      for (const child of data.folders) onSeeName(child.id, child.name)
    }
  }, [data, folder.id, folder.name, onSeeName])

  return (
    <div>
      <div
        className={[
          'group flex items-center gap-1 rounded-md px-1 py-1 transition-colors cursor-pointer',
          isSelected ? 'bg-gray-100' : 'hover:bg-gray-50',
        ].join(' ')}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
        onClick={() => onSelect(folder.id)}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (hasChildren) setExpanded((p) => !p)
          }}
          disabled={!hasChildren}
          className="inline-flex h-5 w-5 items-center justify-center text-gray-400 disabled:opacity-30"
          aria-label={expanded ? 'Collapse' : 'Expand'}
        >
          {hasChildren ? (
            expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <span className="h-3.5 w-3.5" />
          )}
        </button>
        {expanded ? (
          <FolderOpen className="h-3.5 w-3.5 text-gray-400 shrink-0" />
        ) : (
          <Folder className="h-3.5 w-3.5 text-gray-400 shrink-0" />
        )}
        <span className="text-sm text-gray-900 truncate flex-1">{folder.name}</span>
        {folder.child_count > 0 && (
          <span className="text-[11px] text-gray-400 shrink-0">{folder.child_count}</span>
        )}
      </div>
      {expanded && (
        <div>
          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-gray-400" style={{ paddingLeft: `${(depth + 1) * 16 + 4}px` }}>
              <Loader2 className="h-3 w-3 animate-spin" />
              Loading…
            </div>
          )}
          {data?.folders.map((child) => (
            <FolderNode
              key={child.id}
              kbSlug={kbSlug}
              connectorId={connectorId}
              folder={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              onSeeName={onSeeName}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface PickerProps {
  kbSlug: string
  connectorId: string
  initialFolderId: string
  /**
   * Called with the chosen folder id + name. Empty id (and empty name)
   * means "whole drive — clear the filter".
   */
  onConfirm: (folderId: string, folderName: string) => void
  onCancel: () => void
}

/**
 * Lazy folder-tree picker for Microsoft 365 connectors.
 *
 * Hits `/api/app/knowledge-bases/{kb}/connectors/{id}/ms-docs/folders` —
 * top-level on mount, deeper levels per-expand. The user can select any
 * folder (including a nested one) or pick "Whole drive" to clear the
 * filter and sync the entire OneDrive / SharePoint library root.
 */
export function MsDocsFolderPicker({ kbSlug, connectorId, initialFolderId, onConfirm, onCancel }: PickerProps) {
  const [selectedId, setSelectedId] = useState<string>(initialFolderId)
  // Track the name of every folder we've rendered so the parent can show
  // the chosen folder name without an extra Graph call. Keyed by id.
  const [nameById, setNameById] = useState<Record<string, string>>({})

  useEffect(() => {
    setSelectedId(initialFolderId)
  }, [initialFolderId])

  function rememberName(id: string, name: string) {
    setNameById((prev) => (prev[id] === name ? prev : { ...prev, [id]: name }))
  }

  const { data, isLoading, error } = useQuery<FoldersResponse>({
    queryKey: ['ms-docs-folders', kbSlug, connectorId, 'root'],
    queryFn: async () =>
      apiFetch<FoldersResponse>(
        `/api/app/knowledge-bases/${encodeURIComponent(kbSlug)}/connectors/${connectorId}/ms-docs/folders`,
      ),
  })

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-3 py-2">
        <p className="text-sm font-medium text-gray-900">Kies een map om te syncen</p>
        <p className="text-xs text-gray-400 mt-0.5">
          Klik op de pijltjes om submappen uit te klappen. Selecteer een map of kies &quot;hele drive&quot;.
        </p>
      </div>

      <div className="max-h-72 overflow-y-auto px-2 py-2 space-y-0.5">
        <div
          className={[
            'flex items-center gap-2 rounded-md px-2 py-1.5 cursor-pointer',
            selectedId === '' ? 'bg-gray-100' : 'hover:bg-gray-50',
          ].join(' ')}
          onClick={() => setSelectedId('')}
        >
          <Folder className="h-3.5 w-3.5 text-gray-400" />
          <span className="text-sm text-gray-900">Hele drive (alles)</span>
        </div>

        {isLoading && (
          <div className="flex items-center gap-2 px-2 py-2 text-xs text-gray-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            Mappen laden…
          </div>
        )}
        {error && (
          <div className="px-2 py-2 text-xs text-[var(--color-destructive)]">
            {error instanceof Error ? error.message : 'Kon mappen niet ophalen'}
          </div>
        )}
        {data?.folders.length === 0 && !isLoading && (
          <div className="px-2 py-2 text-xs text-gray-400">Geen mappen gevonden in deze drive.</div>
        )}
        {data?.folders.map((folder) => (
          <FolderNode
            key={folder.id}
            kbSlug={kbSlug}
            connectorId={connectorId}
            folder={folder}
            depth={0}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onSeeName={rememberName}
          />
        ))}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-3 py-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Annuleren
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => onConfirm(selectedId, selectedId ? (nameById[selectedId] ?? '') : '')}
        >
          Gebruik deze keuze
        </Button>
      </div>
    </div>
  )
}

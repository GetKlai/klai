import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, File, Folder, FolderOpen, Loader2 } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { Button } from '@/components/ui/button'

interface MsItem {
  id: string
  name: string
  kind: 'folder' | 'file'
  child_count: number
}

interface FoldersResponse {
  // Endpoint name kept as ``folders`` for backwards-compat; payload
  // includes files too (kind: 'file', not selectable in v1).
  folders: MsItem[]
}

interface NodeProps {
  kbSlug: string
  connectorId: string
  item: MsItem
  depth: number
  selectedId: string
  onSelect: (id: string) => void
  onSeeName: (id: string, name: string) => void
}

function ItemNode({ kbSlug, connectorId, item, depth, selectedId, onSelect, onSeeName }: NodeProps) {
  const isFolder = item.kind === 'folder'
  const [expanded, setExpanded] = useState(false)
  const isSelected = isFolder && selectedId === item.id

  // Lazy load children only after the user expands this folder.
  // We always allow expansion — Graph's ``childCount`` is unreliable for
  // some special folders, and showing an empty state is more useful than
  // a disabled chevron the user can't try.
  const { data, isLoading } = useQuery<FoldersResponse>({
    queryKey: ['ms-docs-folders', kbSlug, connectorId, item.id],
    queryFn: async () =>
      apiFetch<FoldersResponse>(
        `/api/app/knowledge-bases/${encodeURIComponent(kbSlug)}/connectors/${connectorId}/ms-docs/folders?parent=${encodeURIComponent(item.id)}`,
      ),
    enabled: expanded && isFolder,
    staleTime: 60_000,
  })

  // Surface every visible folder's id+name to the parent so a later
  // ``onConfirm`` can echo the chosen name without a fresh API call.
  useEffect(() => {
    if (!isFolder) return
    onSeeName(item.id, item.name)
    if (data?.folders) {
      for (const child of data.folders) {
        if (child.kind === 'folder') onSeeName(child.id, child.name)
      }
    }
  }, [data, item.id, item.name, isFolder, onSeeName])

  return (
    <div>
      <div
        className={[
          'group flex items-center gap-1 rounded-md px-1 py-1 transition-colors',
          isFolder ? 'cursor-pointer' : 'cursor-default',
          isSelected ? 'bg-gray-100' : isFolder ? 'hover:bg-gray-50' : '',
        ].join(' ')}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
        onClick={() => isFolder && onSelect(item.id)}
      >
        {/* Chevron: folders only, always enabled (lazy fetch verifies). */}
        {isFolder ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setExpanded((p) => !p)
            }}
            className="inline-flex h-5 w-5 items-center justify-center text-gray-400 hover:text-gray-900"
            aria-label={expanded ? 'Inklappen' : 'Uitklappen'}
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="inline-flex h-5 w-5" />
        )}
        {isFolder ? (
          expanded ? (
            <FolderOpen className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          ) : (
            <Folder className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          )
        ) : (
          <File className="h-3.5 w-3.5 text-gray-300 shrink-0" />
        )}
        <span
          className={[
            'text-sm truncate flex-1',
            isFolder ? 'text-gray-900' : 'text-gray-400',
          ].join(' ')}
        >
          {item.name}
        </span>
        {isFolder && item.child_count > 0 && (
          <span className="text-[11px] text-gray-400 shrink-0">{item.child_count}</span>
        )}
      </div>
      {expanded && isFolder && (
        <div>
          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-gray-400 py-1" style={{ paddingLeft: `${(depth + 1) * 16 + 4}px` }}>
              <Loader2 className="h-3 w-3 animate-spin" />
              Laden…
            </div>
          )}
          {data && data.folders.length === 0 && !isLoading && (
            <div className="text-xs text-gray-400 italic py-1" style={{ paddingLeft: `${(depth + 1) * 16 + 4}px` }}>
              Leeg
            </div>
          )}
          {data?.folders.map((child) => (
            <ItemNode
              key={child.id}
              kbSlug={kbSlug}
              connectorId={connectorId}
              item={child}
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
          Klik op &#9656; om uit te klappen. Bestanden zie je grijs — die worden meegenomen
          als je de bovenliggende map kiest. Alleen mappen zijn selecteerbaar.
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
          <div className="px-2 py-2 text-xs text-gray-400">Deze drive is leeg.</div>
        )}
        {data?.folders.map((item) => (
          <ItemNode
            key={item.id}
            kbSlug={kbSlug}
            connectorId={connectorId}
            item={item}
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

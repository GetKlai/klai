import { useEffect, useMemo, useState } from 'react'
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
  // includes files too (kind: 'file').
  folders: MsItem[]
}

/**
 * Picker can return ONE of three scope shapes:
 *   1. ``{ folderId: '', fileIds: [] }`` — whole drive
 *   2. ``{ folderId: <id>, fileIds: [] }`` — single folder subtree
 *   3. ``{ folderId: '', fileIds: [...] }`` — specific files
 *
 * Folder + files are mutually exclusive: clicking a folder clears file
 * selection, clicking a file clears folder selection.
 */
export interface MsDocsPickerResult {
  folderId: string
  folderName: string
  fileIds: string[]
}

interface NodeProps {
  kbSlug: string
  connectorId: string
  item: MsItem
  depth: number
  selectedFolderId: string
  selectedFileIds: Set<string>
  onSelectFolder: (id: string) => void
  onToggleFile: (id: string) => void
  onSeeName: (id: string, name: string) => void
}

function ItemNode({
  kbSlug,
  connectorId,
  item,
  depth,
  selectedFolderId,
  selectedFileIds,
  onSelectFolder,
  onToggleFile,
  onSeeName,
}: NodeProps) {
  const isFolder = item.kind === 'folder'
  const [expanded, setExpanded] = useState(false)
  const isFolderSelected = isFolder && selectedFolderId === item.id
  const isFileSelected = !isFolder && selectedFileIds.has(item.id)

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
          'group flex items-center gap-1 rounded-md px-1 py-1 transition-colors cursor-pointer',
          isFolderSelected || isFileSelected ? 'bg-gray-100' : 'hover:bg-[var(--color-hover)]',
        ].join(' ')}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
        onClick={() => (isFolder ? onSelectFolder(item.id) : onToggleFile(item.id))}
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
          // Checkbox for files. Native input so keyboard / accessibility
          // are free. Click bubbles up to the parent row, which calls
          // onToggleFile — no stopPropagation here or the toggle never
          // fires when the user clicks the checkbox itself.
          <input
            type="checkbox"
            checked={isFileSelected}
            readOnly
            tabIndex={-1}
            aria-label={`Selecteer ${item.name}`}
            className="h-3.5 w-3.5 accent-gray-900 ml-1 mr-1 cursor-pointer"
          />
        )}
        {isFolder ? (
          expanded ? (
            <FolderOpen className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          ) : (
            <Folder className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          )
        ) : (
          <File className="h-3.5 w-3.5 text-gray-400 shrink-0" />
        )}
        <span
          className={[
            'text-sm truncate flex-1',
            isFolder ? 'text-gray-900' : isFileSelected ? 'text-gray-900' : 'text-gray-700',
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
            <div
              className="flex items-center gap-2 text-xs text-gray-400 py-1"
              style={{ paddingLeft: `${(depth + 1) * 16 + 4}px` }}
            >
              <Loader2 className="h-3 w-3 animate-spin" />
              Laden…
            </div>
          )}
          {data && data.folders.length === 0 && !isLoading && (
            <div
              className="text-xs text-gray-400 italic py-1"
              style={{ paddingLeft: `${(depth + 1) * 16 + 4}px` }}
            >
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
              selectedFolderId={selectedFolderId}
              selectedFileIds={selectedFileIds}
              onSelectFolder={onSelectFolder}
              onToggleFile={onToggleFile}
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
  initialFileIds: string[]
  onConfirm: (result: MsDocsPickerResult) => void
  onCancel: () => void
}

/**
 * Picker for Microsoft 365 connectors — pick a folder OR specific files.
 *
 * The two modes are mutually exclusive: clicking a folder clears any
 * checked files, ticking a file clears the folder selection. "Hele drive"
 * means neither — sync everything.
 */
export function MsDocsFolderPicker({
  kbSlug,
  connectorId,
  initialFolderId,
  initialFileIds,
  onConfirm,
  onCancel,
}: PickerProps) {
  const [selectedFolderId, setSelectedFolderId] = useState<string>(initialFolderId)
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(
    () => new Set(initialFileIds),
  )
  const [nameById, setNameById] = useState<Record<string, string>>({})

  useEffect(() => {
    setSelectedFolderId(initialFolderId)
    setSelectedFileIds(new Set(initialFileIds))
  }, [initialFolderId, initialFileIds])

  function rememberName(id: string, name: string) {
    setNameById((prev) => (prev[id] === name ? prev : { ...prev, [id]: name }))
  }

  function selectFolder(id: string) {
    setSelectedFolderId(id)
    setSelectedFileIds(new Set()) // mutually exclusive
  }

  function toggleFile(id: string) {
    setSelectedFolderId('') // mutually exclusive
    setSelectedFileIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function selectWholeDrive() {
    setSelectedFolderId('')
    setSelectedFileIds(new Set())
  }

  const { data, isLoading, error } = useQuery<FoldersResponse>({
    queryKey: ['ms-docs-folders', kbSlug, connectorId, 'root'],
    queryFn: async () =>
      apiFetch<FoldersResponse>(
        `/api/app/knowledge-bases/${encodeURIComponent(kbSlug)}/connectors/${connectorId}/ms-docs/folders`,
      ),
  })

  const fileCount = selectedFileIds.size
  const summary = useMemo(() => {
    if (selectedFolderId) return `Map: ${nameById[selectedFolderId] ?? 'geselecteerd'}`
    if (fileCount > 0) return `${fileCount} bestand${fileCount === 1 ? '' : 'en'} geselecteerd`
    return 'Hele drive (alles)'
  }, [selectedFolderId, fileCount, nameById])

  const isWholeDriveSelected = selectedFolderId === '' && fileCount === 0

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-3 py-2">
        <p className="text-sm font-medium text-gray-900">Kies wat je wilt syncen</p>
        <p className="text-xs text-gray-400 mt-0.5">
          Klik op &#9656; om een map uit te klappen. Klik op een map om de hele map
          te syncen, of vink losse bestanden aan voor alleen die bestanden.
        </p>
      </div>

      <div className="max-h-72 overflow-y-auto px-2 py-2 space-y-0.5">
        <div
          className={[
            'flex items-center gap-2 rounded-md px-2 py-1.5 cursor-pointer',
            isWholeDriveSelected ? 'bg-gray-100' : 'hover:bg-[var(--color-hover)]',
          ].join(' ')}
          onClick={selectWholeDrive}
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
            selectedFolderId={selectedFolderId}
            selectedFileIds={selectedFileIds}
            onSelectFolder={selectFolder}
            onToggleFile={toggleFile}
            onSeeName={rememberName}
          />
        ))}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-gray-200 px-3 py-2">
        <p className="text-xs text-gray-400 truncate">{summary}</p>
        <div className="flex items-center gap-2 shrink-0">
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Annuleren
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() =>
              onConfirm({
                folderId: selectedFolderId,
                folderName: selectedFolderId ? (nameById[selectedFolderId] ?? '') : '',
                fileIds: Array.from(selectedFileIds),
              })
            }
          >
            Gebruik deze keuze
          </Button>
        </div>
      </div>
    </div>
  )
}

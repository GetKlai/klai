import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  ArrowLeft, Search, RefreshCw, Globe, Layers, GitBranch,
  Database, Cpu, Trash2, ChevronDown, ChevronRight, Tag,
  Calendar, CheckCircle, XCircle, Bug, Lightbulb, Repeat,
  FileText, Settings, Loader2, Undo2,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ObservationType =
  | 'learning'
  | 'preference'
  | 'do'
  | 'dont'
  | 'decision'
  | 'bug'
  | 'pattern'
  | 'note';

interface Observation {
  uid: string;
  name: string;
  type: ObservationType;
  content: string;
  tags: string[];
  project: string;
  createdAt: string;
  updatedAt: string;
  archived: boolean;
}

interface RepoInfo {
  name: string;
  path: string;
  indexedAt: string;
  stats: {
    nodes?: number;
    edges?: number;
    communities?: number;
    processes?: number;
  };
}

interface MemoryStats {
  total: number;
  byType: Partial<Record<ObservationType, number>>;
}

interface ProjectDashboardProps {
  projectName: string;
  serverUrl: string;
  onBack: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TYPE_CONFIG: Record<
  ObservationType,
  { icon: typeof Lightbulb; label: string; colorClass: string; bgClass: string; borderClass: string }
> = {
  do:         { icon: CheckCircle, label: 'DO',         colorClass: 'text-green-400',  bgClass: 'bg-green-500/15',  borderClass: 'border-l-green-500' },
  dont:       { icon: XCircle,     label: "DON'T",      colorClass: 'text-red-400',    bgClass: 'bg-red-500/15',    borderClass: 'border-l-red-500' },
  decision:   { icon: GitBranch,   label: 'DECISION',   colorClass: 'text-purple-400', bgClass: 'bg-purple-500/15', borderClass: 'border-l-purple-500' },
  bug:        { icon: Bug,         label: 'BUG',        colorClass: 'text-orange-400', bgClass: 'bg-orange-500/15', borderClass: 'border-l-orange-500' },
  learning:   { icon: Lightbulb,   label: 'LEARNING',   colorClass: 'text-yellow-400', bgClass: 'bg-yellow-500/15', borderClass: 'border-l-yellow-500' },
  pattern:    { icon: Repeat,      label: 'PATTERN',    colorClass: 'text-blue-400',   bgClass: 'bg-blue-500/15',   borderClass: 'border-l-blue-500' },
  note:       { icon: FileText,    label: 'NOTE',       colorClass: 'text-gray-400',   bgClass: 'bg-gray-500/15',   borderClass: 'border-l-gray-400' },
  preference: { icon: Settings,    label: 'PREFERENCE', colorClass: 'text-cyan-400',   bgClass: 'bg-cyan-500/15',   borderClass: 'border-l-cyan-500' },
};

const ALL_TYPES: ObservationType[] = [
  'do', 'dont', 'decision', 'bug', 'learning', 'pattern', 'note', 'preference',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return `${diffDay}d ago`;
    const diffWeek = Math.floor(diffDay / 7);
    if (diffWeek < 5) return `${diffWeek}w ago`;
    const diffMonth = Math.floor(diffDay / 30);
    return `${diffMonth}mo ago`;
  } catch {
    return 'unknown';
  }
}

function formatNumber(n?: number): string {
  if (n === undefined || n === null) return '-';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const TypeBadge = ({ type }: { type: ObservationType }) => {
  const config = TYPE_CONFIG[type] || TYPE_CONFIG.note;
  const Icon = config.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${config.bgClass} ${config.colorClass}`}
    >
      <Icon className="w-3 h-3" />
      {config.label}
    </span>
  );
};

interface TimelineCardProps {
  observation: Observation;
  isExpanded: boolean;
  onToggle: () => void;
  onDelete: (uid: string) => void;
}

const TimelineCard = ({ observation, isExpanded, onToggle, onDelete }: TimelineCardProps) => {
  const content = observation.content || '';
  const lines = content.split('\n');
  const isTruncatable = lines.length > 3;
  const displayContent = isExpanded
    ? content
    : lines.slice(0, 3).join('\n') + (isTruncatable ? '...' : '');

  return (
    <div
      className={`group bg-surface border border-border-subtle rounded-xl p-4 hover:border-border-default transition-all duration-200 animate-fade-in border-l-3 ${TYPE_CONFIG[observation.type]?.borderClass || 'border-l-gray-400'}`}
    >
      {/* Header row: badge + title + delete */}
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <TypeBadge type={observation.type} />
          <h4 className="text-sm font-medium text-white leading-snug truncate">
            {observation.name}
          </h4>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete(observation.uid);
          }}
          className="p-1 rounded text-text-muted opacity-0 group-hover:opacity-100 hover:text-red-400 hover:bg-red-500/10 transition-all flex-shrink-0"
          title="Delete observation"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Content */}
      <div onClick={onToggle} className="cursor-pointer">
        <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap break-words mb-3">
          {displayContent}
        </p>

        {isTruncatable && (
          <button className="flex items-center gap-1 text-[11px] text-accent hover:text-purple-300 transition-colors mb-3">
            {isExpanded ? (
              <><ChevronRight className="w-3 h-3" /> Show less</>
            ) : (
              <><ChevronDown className="w-3 h-3" /> Show more</>
            )}
          </button>
        )}
      </div>

      {/* Footer: timestamp + tags */}
      <div className="flex items-center gap-3 flex-wrap text-[11px]">
        <span className="flex items-center gap-1 text-text-muted">
          <Calendar className="w-3 h-3" />
          {relativeTime(observation.updatedAt || observation.createdAt)}
        </span>
        {observation.tags?.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            {observation.tags.map((tag, i) => (
              <span
                key={`${tag}-${i}`}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-elevated text-text-muted border border-border-subtle"
              >
                <Tag className="w-2.5 h-2.5" />
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export const ProjectDashboard = ({ projectName, serverUrl, onBack }: ProjectDashboardProps) => {
  const [repoInfo, setRepoInfo] = useState<RepoInfo | null>(null);
  const [memoryStats, setMemoryStats] = useState<MemoryStats | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeType, setActiveType] = useState<ObservationType | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isGlobal, setIsGlobal] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Observation | null>(null);
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch repo info
  useEffect(() => {
    const fetchRepo = async () => {
      try {
        const res = await fetch(`${serverUrl}/api/repo?repo=${encodeURIComponent(projectName)}`);
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        setRepoInfo(data);
      } catch {
        // Non-critical, header stats will show dashes
      }
    };
    fetchRepo();
  }, [serverUrl, projectName]);

  // Fetch memory stats
  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(
        `${serverUrl}/api/memory/stats?repo=${encodeURIComponent(projectName)}`,
      );
      if (!res.ok) return;
      const data = await res.json();
      setMemoryStats(data);
    } catch {
      // Non-critical
    }
  }, [serverUrl, projectName]);

  // Fetch observations
  const fetchObservations = useCallback(
    async (typeFilter?: ObservationType | null) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (!isGlobal) params.set('repo', projectName);
        if (typeFilter) params.set('type', typeFilter);
        params.set('limit', '50');

        const endpoint = isGlobal ? '/api/memory/global' : '/api/memory';
        const url = `${serverUrl}${endpoint}?${params.toString()}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`);
        const data = await res.json();
        const raw = data.results ?? (Array.isArray(data) ? data : []);
        setObservations(raw.map((r: any) => r.observation ?? r));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load memories');
        setObservations([]);
      } finally {
        setLoading(false);
      }
    },
    [serverUrl, projectName, isGlobal],
  );

  // Search observations
  const searchObservations = useCallback(
    async (query: string) => {
      if (!query.trim()) {
        fetchObservations(activeType);
        return;
      }
      setIsSearching(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (!isGlobal) params.set('repo', projectName);

        const res = await fetch(`${serverUrl}/api/memory/search?${params.toString()}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query,
            type: activeType || undefined,
            limit: 50,
          }),
        });
        if (!res.ok) throw new Error(`Search failed: ${res.status}`);
        const data = await res.json();
        const raw = data.results ?? (Array.isArray(data) ? data : []);
        setObservations(raw.map((r: any) => r.observation ?? r));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Search failed');
        setObservations([]);
      } finally {
        setIsSearching(false);
      }
    },
    [serverUrl, projectName, isGlobal, activeType, fetchObservations],
  );

  // Execute the actual delete API call
  const executeDelete = useCallback(
    async (uid: string) => {
      try {
        await fetch(
          `${serverUrl}/api/memory/${uid}?repo=${encodeURIComponent(projectName)}`,
          { method: 'DELETE' },
        );
        fetchStats();
      } catch {
        // Silently fail on delete errors
      }
    },
    [serverUrl, projectName, fetchStats],
  );

  // Delete with undo: remove from UI immediately, delay actual deletion 5s
  const handleDelete = useCallback(
    (uid: string) => {
      const obs = observations.find((o) => o.uid === uid);
      if (!obs) return;

      // If there's already a pending delete, execute it immediately
      if (pendingDelete && deleteTimerRef.current) {
        clearTimeout(deleteTimerRef.current);
        executeDelete(pendingDelete.uid);
      }

      // Remove from UI optimistically
      setObservations((prev) => prev.filter((o) => o.uid !== uid));
      setPendingDelete(obs);

      // Schedule actual deletion after 5 seconds
      deleteTimerRef.current = setTimeout(() => {
        executeDelete(uid);
        setPendingDelete(null);
        deleteTimerRef.current = null;
      }, 5000);
    },
    [observations, pendingDelete, executeDelete],
  );

  // Undo: restore the observation to the list
  const handleUndo = useCallback(() => {
    if (!pendingDelete) return;
    if (deleteTimerRef.current) {
      clearTimeout(deleteTimerRef.current);
      deleteTimerRef.current = null;
    }
    setObservations((prev) => [pendingDelete, ...prev]);
    setPendingDelete(null);
    fetchStats();
  }, [pendingDelete, fetchStats]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    };
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchObservations(activeType);
    fetchStats();
  }, [isGlobal]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refetch on type filter change
  useEffect(() => {
    if (!searchQuery.trim()) {
      fetchObservations(activeType);
    }
  }, [activeType]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced search
  useEffect(() => {
    if (!searchQuery.trim()) {
      fetchObservations(activeType);
      return;
    }
    const timer = setTimeout(() => {
      searchObservations(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]); // eslint-disable-line react-hooks/exhaustive-deps

  // Compute per-type counts
  const typeCounts = useMemo(() => {
    if (memoryStats?.byType) return memoryStats.byType;
    const counts: Partial<Record<ObservationType, number>> = {};
    for (const obs of observations) {
      counts[obs.type] = (counts[obs.type] || 0) + 1;
    }
    return counts;
  }, [memoryStats, observations]);

  const totalCount = memoryStats?.total ?? observations.length;

  const handleRefresh = useCallback(() => {
    setSearchQuery('');
    setActiveType(null);
    fetchObservations(null);
    fetchStats();
  }, [fetchObservations, fetchStats]);

  return (
    <div className="min-h-screen bg-void">
      {/* Header */}
      <div className="border-b border-border-default bg-deep/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-6 py-5">
          {/* Back + title row */}
          <div className="flex items-center gap-3 mb-3">
            <button
              onClick={onBack}
              className="p-1.5 rounded-lg text-text-muted hover:text-white hover:bg-hover transition-colors"
              title="Back to projects"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-white truncate">{projectName}</h1>
              {repoInfo?.path && (
                <p className="text-xs text-text-muted font-mono truncate">{repoInfo.path}</p>
              )}
            </div>
          </div>

          {/* Stats row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 ml-10">
            <div className="flex items-center gap-2 bg-indigo-500/10 rounded-lg px-3 py-2 border border-indigo-500/20">
              <Layers className="w-4 h-4 text-indigo-400" />
              <div>
                <p className="text-xs text-indigo-300/70">Nodes</p>
                <p className="text-sm font-medium text-white">{formatNumber(repoInfo?.stats?.nodes)}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 bg-emerald-500/10 rounded-lg px-3 py-2 border border-emerald-500/20">
              <GitBranch className="w-4 h-4 text-emerald-400" />
              <div>
                <p className="text-xs text-emerald-300/70">Edges</p>
                <p className="text-sm font-medium text-white">{formatNumber(repoInfo?.stats?.edges)}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 bg-amber-500/10 rounded-lg px-3 py-2 border border-amber-500/20">
              <Database className="w-4 h-4 text-amber-400" />
              <div>
                <p className="text-xs text-amber-300/70">Clusters</p>
                <p className="text-sm font-medium text-white">{formatNumber(repoInfo?.stats?.communities)}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 bg-rose-500/10 rounded-lg px-3 py-2 border border-rose-500/20">
              <Cpu className="w-4 h-4 text-rose-400" />
              <div>
                <p className="text-xs text-rose-300/70">Flows</p>
                <p className="text-sm font-medium text-white">{formatNumber(repoInfo?.stats?.processes)}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-5xl mx-auto px-6 py-6">
        {/* Toolbar: Global toggle + Search + Refresh */}
        <div className="flex items-center gap-3 mb-5">
          {/* Global toggle */}
          <button
            onClick={() => setIsGlobal((prev) => !prev)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-colors shrink-0 ${
              isGlobal
                ? 'bg-purple-500/15 border-purple-500/30 text-purple-400'
                : 'bg-surface border-border-subtle text-text-muted hover:text-white hover:border-purple-500/30'
            }`}
            title={isGlobal ? 'Showing global memories' : 'Showing project memories'}
          >
            <Globe className="w-3.5 h-3.5" />
            {isGlobal ? 'Global' : 'Project'}
          </button>

          {/* Search */}
          <div className="relative flex-1">
            {isSearching ? (
              <Loader2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted animate-spin" />
            ) : (
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            )}
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search memories..."
              className="w-full bg-surface border border-border-subtle rounded-lg pl-10 pr-4 py-2 text-sm text-white placeholder-text-muted focus:outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/20 transition-colors"
            />
          </div>

          {/* Refresh */}
          <button
            onClick={handleRefresh}
            className="p-2 rounded-lg bg-surface border border-border-subtle text-text-muted hover:text-white hover:border-purple-500/30 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {/* Filter tabs */}
        <div className="flex items-center gap-1.5 mb-6 overflow-x-auto scrollbar-thin pb-1">
          <button
            onClick={() => setActiveType(null)}
            className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors shrink-0 ${
              activeType === null
                ? 'bg-accent/15 text-accent'
                : 'text-text-muted hover:text-white hover:bg-hover'
            }`}
          >
            All
            {totalCount > 0 && (
              <span className="ml-1 text-[10px] opacity-70">{totalCount}</span>
            )}
          </button>
          {ALL_TYPES.map((type) => {
            const config = TYPE_CONFIG[type];
            const Icon = config.icon;
            const count = typeCounts[type] || 0;
            return (
              <button
                key={type}
                onClick={() => setActiveType(activeType === type ? null : type)}
                className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors shrink-0 ${
                  activeType === type
                    ? `${config.bgClass} ${config.colorClass}`
                    : 'text-text-muted hover:text-white hover:bg-hover'
                }`}
              >
                <Icon className="w-3 h-3" />
                {config.label}
                {count > 0 && (
                  <span className="ml-1 text-[10px] opacity-70">{count}</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Tag filter bar */}
        {(() => {
          const allTags = new Map<string, number>();
          for (const obs of observations) {
            for (const tag of obs.tags || []) {
              allTags.set(tag, (allTags.get(tag) || 0) + 1);
            }
          }
          if (allTags.size === 0) return null;
          const sorted = [...allTags.entries()].sort((a, b) => b[1] - a[1]);
          return (
            <div className="flex flex-wrap items-center gap-1.5 mb-4">
              <Tag className="w-3.5 h-3.5 text-text-muted shrink-0 mr-1" />
              {sorted.map(([tag, count]) => {
                const isActive = activeTags.has(tag);
                return (
                  <button
                    key={tag}
                    onClick={() => {
                      setActiveTags(prev => {
                        const next = new Set(prev);
                        if (next.has(tag)) next.delete(tag);
                        else next.add(tag);
                        return next;
                      });
                    }}
                    className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-purple-500/25 text-purple-200 border border-purple-400/40'
                        : 'text-text-secondary hover:text-white hover:bg-hover border border-border-subtle'
                    }`}
                  >
                    {tag}
                    <span className="text-[10px] opacity-60">{count}</span>
                  </button>
                );
              })}
              {activeTags.size > 0 && (
                <button
                  onClick={() => setActiveTags(new Set())}
                  className="px-2 py-1 rounded-md text-[10px] text-text-muted hover:text-white transition-colors shrink-0"
                >
                  clear
                </button>
              )}
            </div>
          );
        })()}

        {/* Loading */}
        {loading && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin mb-4" />
            <p className="text-text-muted text-sm">Loading memories...</p>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
              <Database className="w-6 h-6 text-red-400" />
            </div>
            <h3 className="text-sm font-medium text-white mb-2">Failed to load memories</h3>
            <p className="text-xs text-text-muted mb-4 max-w-sm">{error}</p>
            <button
              onClick={handleRefresh}
              className="px-4 py-2 text-xs font-medium text-accent bg-accent/10 rounded-lg hover:bg-accent/20 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {/* Filtered observations */}
        {(() => {
          const filtered = activeTags.size > 0
            ? observations.filter(obs => obs.tags?.some(t => activeTags.has(t)))
            : observations;

          return (<>
        {/* Empty state */}
        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-14 h-14 rounded-xl bg-surface flex items-center justify-center mb-4">
              <Lightbulb className="w-7 h-7 text-text-muted" />
            </div>
            <h3 className="text-base font-medium text-white mb-2">No memories found</h3>
            <p className="text-sm text-text-muted max-w-sm">
              {searchQuery
                ? `No observations matching "${searchQuery}"`
                : isGlobal
                  ? 'No global memories recorded yet.'
                  : `No memories recorded for ${projectName} yet.`}
            </p>
          </div>
        )}

        {/* Timeline */}
        {!loading && !error && filtered.length > 0 && (
          <div className="flex flex-col gap-3">
            {filtered.map((obs) => (
              <TimelineCard
                key={obs.uid}
                observation={obs}
                isExpanded={expandedId === obs.uid}
                onToggle={() => setExpandedId(expandedId === obs.uid ? null : obs.uid)}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
          </>);
        })()}
      </div>

      {/* Undo Toast */}
      {pendingDelete && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 animate-fade-in">
          <div className="flex items-center gap-3 bg-elevated border border-border-default rounded-lg px-4 py-3 shadow-lg">
            <span className="text-sm text-text-secondary truncate max-w-xs">
              Deleted "{pendingDelete.name}"
            </span>
            <button
              onClick={handleUndo}
              className="flex items-center gap-1.5 px-3 py-1 rounded-md text-sm font-medium text-accent hover:bg-accent/10 transition-colors whitespace-nowrap"
            >
              <Undo2 className="w-3.5 h-3.5" />
              Undo
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

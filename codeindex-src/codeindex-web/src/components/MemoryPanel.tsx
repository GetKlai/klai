import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Search, Loader2, AlertTriangle, Brain,
  Lightbulb, Settings, CheckCircle, XCircle,
  GitBranch, Bug, Repeat, FileText,
  ChevronDown, ChevronRight, Tag, Calendar,
  FolderOpen, RefreshCw,
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

interface MemoryStats {
  total: number;
  byType: Partial<Record<ObservationType, number>>;
}

interface MemoryPanelProps {
  serverBaseUrl?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TYPE_CONFIG: Record<
  ObservationType,
  { icon: typeof Lightbulb; label: string; color: string; bgColor: string; textColor: string }
> = {
  learning: {
    icon: Lightbulb,
    label: 'Learning',
    color: 'emerald',
    bgColor: 'bg-emerald-500/15',
    textColor: 'text-emerald-400',
  },
  preference: {
    icon: Settings,
    label: 'Preference',
    color: 'blue',
    bgColor: 'bg-blue-500/15',
    textColor: 'text-blue-400',
  },
  do: {
    icon: CheckCircle,
    label: 'Do',
    color: 'green',
    bgColor: 'bg-green-500/15',
    textColor: 'text-green-400',
  },
  dont: {
    icon: XCircle,
    label: "Don't",
    color: 'red',
    bgColor: 'bg-red-500/15',
    textColor: 'text-red-400',
  },
  decision: {
    icon: GitBranch,
    label: 'Decision',
    color: 'purple',
    bgColor: 'bg-purple-500/15',
    textColor: 'text-purple-400',
  },
  bug: {
    icon: Bug,
    label: 'Bug',
    color: 'amber',
    bgColor: 'bg-amber-500/15',
    textColor: 'text-amber-400',
  },
  pattern: {
    icon: Repeat,
    label: 'Pattern',
    color: 'cyan',
    bgColor: 'bg-cyan-500/15',
    textColor: 'text-cyan-400',
  },
  note: {
    icon: FileText,
    label: 'Note',
    color: 'slate',
    bgColor: 'bg-slate-500/15',
    textColor: 'text-slate-400',
  },
};

const ALL_TYPES: ObservationType[] = [
  'learning',
  'preference',
  'do',
  'dont',
  'decision',
  'bug',
  'pattern',
  'note',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);
  const diffWeek = Math.floor(diffDay / 7);
  const diffMonth = Math.floor(diffDay / 30);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;
  if (diffWeek < 5) return `${diffWeek}w ago`;
  return `${diffMonth}mo ago`;
}

function truncateContent(content: string, maxLines = 2): string {
  const lines = content.split('\n');
  if (lines.length <= maxLines) return content;
  return lines.slice(0, maxLines).join('\n') + '...';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const TypeBadge = ({ type }: { type: ObservationType }) => {
  const config = TYPE_CONFIG[type];
  const Icon = config.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${config.bgColor} ${config.textColor}`}
    >
      <Icon className="w-3 h-3" />
      {config.label}
    </span>
  );
};

interface ObservationCardProps {
  observation: Observation;
  isExpanded: boolean;
  onToggle: () => void;
}

const ObservationCard = ({ observation, isExpanded, onToggle }: ObservationCardProps) => {
  return (
    <div
      onClick={onToggle}
      className="px-3 py-2.5 hover:bg-hover rounded-md cursor-pointer transition-colors group"
    >
      {/* Top row: type badge + date */}
      <div className="flex items-center justify-between mb-1.5">
        <TypeBadge type={observation.type} />
        <span className="text-[11px] text-text-muted flex items-center gap-1">
          <Calendar className="w-3 h-3" />
          {relativeTime(observation.updatedAt || observation.createdAt)}
        </span>
      </div>

      {/* Name */}
      <h4 className="text-sm font-medium text-text-primary mb-1 leading-snug">
        {observation.name}
      </h4>

      {/* Content preview or full */}
      <p className="text-xs text-text-muted leading-relaxed whitespace-pre-wrap break-words">
        {isExpanded ? observation.content : truncateContent(observation.content)}
      </p>

      {/* Tags + project */}
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        {observation.project && (
          <span className="inline-flex items-center gap-1 text-[10px] text-text-muted">
            <FolderOpen className="w-3 h-3" />
            {observation.project}
          </span>
        )}
        {observation.tags?.length > 0 && (
          <>
            {observation.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-surface text-text-muted border border-border-subtle"
              >
                <Tag className="w-2.5 h-2.5" />
                {tag}
              </span>
            ))}
          </>
        )}
      </div>

      {/* Expand indicator */}
      {!isExpanded && observation.content.split('\n').length > 2 && (
        <div className="mt-1.5 flex items-center gap-1 text-[11px] text-accent opacity-0 group-hover:opacity-100 transition-opacity">
          <ChevronDown className="w-3 h-3" />
          Show more
        </div>
      )}
      {isExpanded && (
        <div className="mt-1.5 flex items-center gap-1 text-[11px] text-accent">
          <ChevronRight className="w-3 h-3" />
          Show less
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export const MemoryPanel = ({ serverBaseUrl: serverBaseUrlProp }: MemoryPanelProps) => {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeType, setActiveType] = useState<ObservationType | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  const baseUrl = serverBaseUrlProp || 'http://localhost:4747';

  // Fetch stats
  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${baseUrl}/memory/stats`);
      if (!res.ok) throw new Error(`Stats fetch failed: ${res.status}`);
      const data = await res.json();
      setStats(data);
    } catch {
      // Stats are non-critical, silently fail
    }
  }, [baseUrl]);

  // Fetch observations list
  const fetchObservations = useCallback(
    async (typeFilter?: ObservationType | null) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (typeFilter) params.set('type', typeFilter);
        const url = `${baseUrl}/memory${params.toString() ? '?' + params.toString() : ''}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`);
        const data = await res.json();
        setObservations(Array.isArray(data) ? data : data.observations || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load observations');
        setObservations([]);
      } finally {
        setLoading(false);
      }
    },
    [baseUrl],
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
        const res = await fetch(`${baseUrl}/memory/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query }),
        });
        if (!res.ok) throw new Error(`Search failed: ${res.status}`);
        const data = await res.json();
        setObservations(Array.isArray(data) ? data : data.observations || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Search failed');
        setObservations([]);
      } finally {
        setIsSearching(false);
      }
    },
    [baseUrl, activeType, fetchObservations],
  );

  // Initial fetch + refetch on serverBaseUrl change
  useEffect(() => {
    fetchObservations(activeType);
    fetchStats();
  }, [baseUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refetch when type filter changes
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

  // Compute per-type counts from stats or observations
  const typeCounts = useMemo(() => {
    if (stats?.byType) return stats.byType;
    const counts: Partial<Record<ObservationType, number>> = {};
    for (const obs of observations) {
      counts[obs.type] = (counts[obs.type] || 0) + 1;
    }
    return counts;
  }, [stats, observations]);

  const totalCount = stats?.total ?? observations.length;

  const handleRefresh = () => {
    setSearchQuery('');
    setActiveType(null);
    fetchObservations(null);
    fetchStats();
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-surface border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-accent" />
          <span className="text-sm font-medium text-text-primary">Memory</span>
          <span className="text-[11px] px-1.5 py-0.5 bg-elevated text-text-muted rounded-full">
            {totalCount}
          </span>
        </div>
        <button
          onClick={handleRefresh}
          className="p-1.5 text-text-muted hover:text-text-primary hover:bg-hover rounded transition-colors"
          title="Refresh"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Stats pills */}
      {totalCount > 0 && (
        <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border-subtle overflow-x-auto scrollbar-thin">
          {ALL_TYPES.map((type) => {
            const count = typeCounts[type] || 0;
            if (count === 0) return null;
            const config = TYPE_CONFIG[type];
            const Icon = config.icon;
            return (
              <span
                key={type}
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] shrink-0 ${config.bgColor} ${config.textColor}`}
              >
                <Icon className="w-2.5 h-2.5" />
                {count}
              </span>
            );
          })}
        </div>
      )}

      {/* Search */}
      <div className="px-3 pt-3 pb-2">
        <div className="flex items-center gap-2 px-3 py-2 bg-elevated border border-border-subtle rounded-lg focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20">
          {isSearching ? (
            <Loader2 className="w-4 h-4 text-text-muted animate-spin" />
          ) : (
            <Search className="w-4 h-4 text-text-muted" />
          )}
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search observations..."
            className="flex-1 bg-transparent border-none outline-none text-sm text-text-primary placeholder:text-text-muted"
          />
        </div>
      </div>

      {/* Type filter tabs */}
      <div className="flex items-center gap-1 px-3 pb-2 overflow-x-auto scrollbar-thin">
        <button
          onClick={() => setActiveType(null)}
          className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors shrink-0 ${
            activeType === null
              ? 'bg-accent/15 text-accent'
              : 'text-text-muted hover:text-text-primary hover:bg-hover'
          }`}
        >
          All
        </button>
        {ALL_TYPES.map((type) => {
          const config = TYPE_CONFIG[type];
          const Icon = config.icon;
          return (
            <button
              key={type}
              onClick={() => setActiveType(activeType === type ? null : type)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors shrink-0 ${
                activeType === type
                  ? `${config.bgColor} ${config.textColor}`
                  : 'text-text-muted hover:text-text-primary hover:bg-hover'
              }`}
            >
              <Icon className="w-3 h-3" />
              {config.label}
            </button>
          );
        })}
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {/* Loading state */}
        {loading && (
          <div className="flex flex-col items-center justify-center h-full p-6">
            <Loader2 className="w-6 h-6 text-accent animate-spin mb-3" />
            <span className="text-sm text-text-muted">Loading observations...</span>
          </div>
        )}

        {/* Error state */}
        {!loading && error && (
          <div className="flex flex-col items-center justify-center h-full p-6 text-center">
            <div className="w-12 h-12 mb-3 flex items-center justify-center bg-red-500/10 rounded-xl">
              <AlertTriangle className="w-6 h-6 text-red-400" />
            </div>
            <h3 className="text-sm font-medium text-text-primary mb-1">
              Failed to load
            </h3>
            <p className="text-xs text-text-muted max-w-xs mb-3">{error}</p>
            <button
              onClick={handleRefresh}
              className="px-3 py-1.5 text-xs font-medium text-accent bg-accent/10 rounded-md hover:bg-accent/20 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && observations.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full p-6 text-center">
            <div className="w-14 h-14 mb-4 flex items-center justify-center bg-surface rounded-xl">
              <Brain className="w-7 h-7 text-text-muted" />
            </div>
            <h3 className="text-base font-medium text-text-primary mb-2">
              No memories found
            </h3>
            <p className="text-sm text-text-muted max-w-xs">
              {searchQuery
                ? `No observations matching "${searchQuery}"`
                : 'Observations will appear here as CodeIndex learns about your project.'}
            </p>
          </div>
        )}

        {/* Observation list */}
        {!loading && !error && observations.length > 0 && (
          <div className="px-1 pb-2">
            {observations.map((obs) => (
              <ObservationCard
                key={obs.uid}
                observation={obs}
                isExpanded={expandedId === obs.uid}
                onToggle={() =>
                  setExpandedId(expandedId === obs.uid ? null : obs.uid)
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

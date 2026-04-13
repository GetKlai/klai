import { useState, useEffect, useMemo } from 'react';
import { Search, Database, GitBranch, Clock, FolderOpen, Layers, ArrowRight, Cpu, ArrowUpDown } from 'lucide-react';

type SortMode = 'alpha' | 'recent' | 'size';

interface RepoSummary {
  name: string;
  path: string;
  indexedAt: string;
  lastCommit?: string;
  stats?: {
    nodes?: number;
    edges?: number;
    communities?: number;
    processes?: number;
  };
}

interface ProjectPickerProps {
  onSelectProject: (repoName: string, serverUrl: string) => void;
  serverUrl: string;
}

export const ProjectPicker = ({ onSelectProject, serverUrl }: ProjectPickerProps) => {
  const [repos, setRepos] = useState<RepoSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState<SortMode>('alpha');

  useEffect(() => {
    const fetchRepos = async () => {
      try {
        setLoading(true);
        const res = await fetch(`${serverUrl}/api/repos`);
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        setRepos(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to connect to server');
      } finally {
        setLoading(false);
      }
    };
    fetchRepos();
  }, [serverUrl]);

  const filtered = useMemo(() => {
    let list = [...repos];
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(r =>
        r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q)
      );
    }
    list.sort((a, b) => {
      if (sortMode === 'alpha') return a.name.localeCompare(b.name);
      if (sortMode === 'recent') return new Date(b.indexedAt).getTime() - new Date(a.indexedAt).getTime();
      return (b.stats?.nodes ?? 0) - (a.stats?.nodes ?? 0);
    });
    return list;
  }, [repos, searchQuery, sortMode]);

  const formatDate = (iso: string): string => {
    try {
      const d = new Date(iso);
      const now = new Date();
      const diffMs = now.getTime() - d.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      const diffDays = Math.floor(diffHours / 24);
      if (diffDays < 7) return `${diffDays}d ago`;
      return d.toLocaleDateString();
    } catch {
      return 'unknown';
    }
  };

  const formatNumber = (n?: number): string => {
    if (n === undefined || n === null) return '-';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-void flex items-center justify-center">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-dim text-sm">Connecting to CodeIndex server...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-void flex items-center justify-center p-8">
        <div className="max-w-md text-center">
          <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-4">
            <Database className="w-6 h-6 text-red-400" />
          </div>
          <h2 className="text-lg font-medium text-white mb-2">Connection Failed</h2>
          <p className="text-ghost text-sm mb-6">{error}</p>
          <p className="text-ghost text-xs">
            Make sure the CodeIndex server is running on {serverUrl}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-void">
      {/* Header */}
      <div className="border-b border-glow/30 bg-deep/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-6 py-6">
          <div className="flex items-center gap-3 mb-1">
            <Database className="w-6 h-6 text-purple-400" />
            <h1 className="text-xl font-semibold text-white">CodeIndex</h1>
          </div>
          <p className="text-ghost text-sm ml-9">Select a project to explore</p>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Search + Sort */}
        {repos.length > 0 && (
          <div className="flex gap-3 mb-6">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ghost" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search projects..."
                className="w-full bg-surface border border-glow/30 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder-ghost focus:outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/20 transition-colors"
              />
            </div>
            <button
              onClick={() => setSortMode(m => m === 'alpha' ? 'recent' : m === 'recent' ? 'size' : 'alpha')}
              className="flex items-center gap-1.5 px-3 bg-surface border border-glow/30 rounded-lg text-xs text-dim hover:text-white hover:border-purple-500/40 transition-colors whitespace-nowrap"
              title={`Sort: ${sortMode === 'alpha' ? 'A-Z' : sortMode === 'recent' ? 'Recent first' : 'Largest first'}`}
            >
              <ArrowUpDown className="w-3.5 h-3.5" />
              {sortMode === 'alpha' ? 'A-Z' : sortMode === 'recent' ? 'Recent' : 'Size'}
            </button>
          </div>
        )}

        {/* Empty state */}
        {repos.length === 0 && (
          <div className="text-center py-20">
            <FolderOpen className="w-12 h-12 text-ghost/50 mx-auto mb-4" />
            <h2 className="text-lg font-medium text-white mb-2">No projects indexed</h2>
            <p className="text-ghost text-sm mb-6 max-w-sm mx-auto">
              Index a repository to get started with code intelligence.
            </p>
            <code className="bg-surface border border-glow/30 rounded-lg px-4 py-2 text-sm text-purple-300 font-mono">
              codeindex analyze MyProject ~/path/to/repo
            </code>
          </div>
        )}

        {/* Project grid */}
        {filtered.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((repo) => (
              <button
                key={repo.name}
                onClick={() => onSelectProject(repo.name, serverUrl)}
                className="group text-left bg-surface border border-glow/30 rounded-xl p-5 hover:border-purple-500/40 hover:bg-surface/80 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-purple-500/30"
              >
                {/* Name + arrow */}
                <div className="flex items-start justify-between mb-3">
                  <h3 className="text-base font-medium text-white group-hover:text-purple-300 transition-colors truncate pr-2">
                    {repo.name}
                  </h3>
                  <ArrowRight className="w-4 h-4 text-ghost group-hover:text-purple-400 transition-colors flex-shrink-0 mt-1" />
                </div>

                {/* Path */}
                <p className="text-xs text-ghost truncate mb-4 font-mono">
                  {repo.path}
                </p>

                {/* Stats grid */}
                <div className="grid grid-cols-2 gap-2 mb-3">
                  <div className="flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-purple-400/60" />
                    <span className="text-xs text-dim">{formatNumber(repo.stats?.nodes)} nodes</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <GitBranch className="w-3.5 h-3.5 text-purple-400/60" />
                    <span className="text-xs text-dim">{formatNumber(repo.stats?.edges)} edges</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Database className="w-3.5 h-3.5 text-purple-400/60" />
                    <span className="text-xs text-dim">{formatNumber(repo.stats?.communities)} clusters</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Cpu className="w-3.5 h-3.5 text-purple-400/60" />
                    <span className="text-xs text-dim">{formatNumber(repo.stats?.processes)} flows</span>
                  </div>
                </div>

                {/* Last indexed */}
                <div className="flex items-center gap-1.5 pt-3 border-t border-glow/20">
                  <Clock className="w-3 h-3 text-ghost" />
                  <span className="text-xs text-ghost">Indexed {formatDate(repo.indexedAt)}</span>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* No search results */}
        {repos.length > 0 && filtered.length === 0 && searchQuery && (
          <div className="text-center py-12">
            <Search className="w-8 h-8 text-ghost/50 mx-auto mb-3" />
            <p className="text-ghost text-sm">No projects match "{searchQuery}"</p>
          </div>
        )}
      </div>
    </div>
  );
};

import { useState } from 'react'
import { ChevronRight, File, Folder, GitBranch, Loader2, Search } from 'lucide-react'
import { api, type RepoTreeNode } from '@/api/client'
import { useSessionStore } from '@/store/session'

export default function Repository() {
  const { repoPath, repoUrl, setRepoPath, setRepoUrl } = useSessionStore()
  const [cloning, setCloning] = useState(false)
  const [cloneMsg, setCloneMsg] = useState('')
  const [cloneError, setCloneError] = useState('')

  const [loading, setLoading] = useState(false)
  const [tree, setTree] = useState<RepoTreeNode[]>([])
  const [treeError, setTreeError] = useState('')

  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<RepoTreeNode[]>([])
  const [searching, setSearching] = useState(false)

  const [archSummary, setArchSummary] = useState<Record<string, unknown> | null>(null)
  const [analyzing, setAnalyzing] = useState(false)

  async function handleClone() {
    if (!repoUrl) return
    setCloning(true); setCloneMsg(''); setCloneError('')
    try {
      const r = await api.cloneRepo({ repo_url: repoUrl })
      if (r.success) {
        setCloneMsg(`Cloned → ${r.dest_path} (${r.branch} @ ${r.commit_hash?.slice(0, 7)})`)
        setRepoPath(r.dest_path)
      } else {
        setCloneError('Clone returned success=false')
      }
    } catch (e: unknown) {
      setCloneError(e instanceof Error ? e.message : String(e))
    } finally { setCloning(false) }
  }

  async function handleBrowse() {
    if (!repoPath) return
    setLoading(true); setTreeError('')
    try {
      const nodes = await api.repoTree(repoPath)
      setTree(nodes.filter(n => !n.is_dir).slice(0, 200))
    } catch (e: unknown) {
      setTreeError(e instanceof Error ? e.message : String(e))
    } finally { setLoading(false) }
  }

  async function handleSearch() {
    if (!repoPath || !query) return
    setSearching(true)
    try {
      const r = await api.searchFiles(repoPath, query)
      setSearchResults(r)
    } finally { setSearching(false) }
  }

  async function handleAnalyze() {
    if (!repoPath) return
    setAnalyzing(true)
    try {
      const r = await api.analyzeRepo(repoPath)
      setArchSummary(r)
    } finally { setAnalyzing(false) }
  }

  const displayTree = query && searchResults.length > 0 ? searchResults : tree

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Repository</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>Clone and explore GitHub repositories.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', marginBottom: '1.25rem' }}>
        {/* Clone */}
        <div className="card">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Clone Repository
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input
              className="input"
              placeholder="https://github.com/owner/repo"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleClone()}
            />
            <input
              className="input"
              placeholder="Local path (optional, e.g. /tmp/myrepo)"
              value={repoPath}
              onChange={e => setRepoPath(e.target.value)}
            />
            <button className="btn btn-primary" onClick={handleClone} disabled={cloning || !repoUrl}>
              {cloning ? <><Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> Cloning…</> : <><GitBranch size={13} /> Clone</>}
            </button>
            {cloneMsg && <div style={{ fontSize: 12, color: 'var(--color-success)', fontFamily: 'var(--font-mono)' }}>{cloneMsg}</div>}
            {cloneError && <div style={{ fontSize: 12, color: 'var(--color-danger)' }}>{cloneError}</div>}
          </div>
        </div>

        {/* Arch summary */}
        <div className="card">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Architecture Summary
          </div>
          <button className="btn btn-ghost" onClick={handleAnalyze} disabled={analyzing || !repoPath} style={{ marginBottom: 12, width: '100%', justifyContent: 'center' }}>
            {analyzing ? <><Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> Analyzing…</> : 'Analyze Repository'}
          </button>
          {archSummary && (
            <div className="code-block" style={{ fontSize: 11, maxHeight: 180, overflow: 'auto' }}>
              {JSON.stringify(archSummary, null, 2)}
            </div>
          )}
          {!archSummary && !analyzing && (
            <div style={{ textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 12, padding: '1.5rem 0' }}>
              Set a local path and click Analyze.
            </div>
          )}
        </div>
      </div>

      {/* File browser */}
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            File Browser
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-ghost" onClick={handleBrowse} disabled={loading || !repoPath} style={{ fontSize: 12 }}>
              {loading ? <Loader2 size={12} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Folder size={12} />}
              Browse
            </button>
          </div>
        </div>

        {/* Search */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-muted)' }} />
            <input
              className="input"
              style={{ paddingLeft: 30 }}
              placeholder="Search files…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
            />
          </div>
          <button className="btn btn-ghost" onClick={handleSearch} disabled={searching || !repoPath}>
            {searching ? <Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> : 'Search'}
          </button>
        </div>

        {treeError && <div style={{ color: 'var(--color-danger)', fontSize: 12, marginBottom: 8 }}>{treeError}</div>}

        {displayTree.length > 0 ? (
          <div style={{ maxHeight: 400, overflowY: 'auto' }}>
            {displayTree.map(node => (
              <div key={node.path} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 4px', borderBottom: '1px solid var(--color-border)', fontSize: 12 }}>
                {node.is_dir ? <Folder size={12} color="#f59e0b" /> : <File size={12} color="var(--color-text-muted)" />}
                <span style={{ fontFamily: 'var(--font-mono)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.path}</span>
                {node.size_bytes != null && <span style={{ color: 'var(--color-text-dim)', fontSize: 11 }}>{(node.size_bytes / 1024).toFixed(1)}KB</span>}
                <ChevronRight size={12} color="var(--color-text-dim)" />
              </div>
            ))}
          </div>
        ) : (
          <div style={{ textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 12, padding: '2rem 0' }}>
            {loading ? 'Loading…' : 'Enter a repo path and click Browse.'}
          </div>
        )}
      </div>
    </div>
  )
}

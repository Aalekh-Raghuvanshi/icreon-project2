import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, Cpu, DollarSign, GitBranch, Hash, Play, Trash2, Zap } from 'lucide-react'
import { api } from '@/api/client'
import { useSessionStore } from '@/store/session'
import ProgressBar from '@/components/ProgressBar'
import clsx from 'clsx'

const STATUS_BADGE: Record<string, string> = {
  done:      'badge-done',
  failed:    'badge-failed',
  executing: 'badge-executing',
  coding:    'badge-coding',
  planning:  'badge-planning',
  reviewing: 'badge-reviewing',
  pending:   'badge-pending',
}

const PIPELINE_STAGES = [
  { key: 'planning',  label: 'Planning',    progress: 0.10 },
  { key: 'coding',    label: 'Code Gen',    progress: 0.40 },
  { key: 'executing', label: 'Testing',     progress: 0.65 },
  { key: 'reviewing', label: 'Review',      progress: 0.85 },
  { key: 'done',      label: 'PR Creation', progress: 1.00 },
]

function fmtTime(secs: number | null | undefined): string {
  if (!secs) return '–'
  if (secs < 60) return `${Math.round(secs)}s`
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '–'
  return new Date(iso).toLocaleString()
}

export default function Dashboard() {
  const { sessions, activeSession, setSessions, setActiveSession, setActiveSessionId } = useSessionStore()
  const navigate = useNavigate()

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const list = await api.listSessions()
        if (mounted) setSessions(list)
      } catch { /* backend not running */ }
    }
    load()
    const iv = setInterval(load, 4000)
    return () => { mounted = false; clearInterval(iv) }
  }, [setSessions])

  const handleSelect = async (id: string) => {
    try {
      const detail = await api.getSession(id)
      setActiveSession(detail)
      setActiveSessionId(id)
    } catch { /* ignore */ }
  }

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    await api.deleteSession(id).catch(() => {})
    setSessions(sessions.filter(s => s.session_id !== id))
    if (activeSession?.session_id === id) setActiveSession(null)
  }

  const running = sessions.filter(s => !['done', 'failed'].includes(s.status))
  const done    = sessions.filter(s => s.status === 'done')
  const failed  = sessions.filter(s => s.status === 'failed')

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Dashboard</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>
          Monitor all pipeline runs in real time.
        </p>
      </div>

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '1rem', marginBottom: '1.75rem' }}>
        {[
          { icon: Zap,         label: 'Total Runs',  value: sessions.length,  color: '#6366f1' },
          { icon: Play,        label: 'Running',     value: running.length,   color: '#10b981' },
          { icon: GitBranch,   label: 'Completed',   value: done.length,      color: '#34d399' },
          { icon: Hash,        label: 'Failed',      value: failed.length,    color: '#ef4444' },
        ].map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="card" style={{ display: 'flex', alignItems: 'center', gap: '0.875rem' }}>
            <div style={{ width: 40, height: 40, borderRadius: 10, background: `${color}18`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Icon size={18} color={color} />
            </div>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1 }}>{value}</div>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>{label}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: '1.25rem' }}>
        {/* Session list */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Recent Runs</h2>
            <button className="btn btn-primary" style={{ fontSize: 12, padding: '0.35rem 0.75rem' }} onClick={() => navigate('/task')}>
              + New Run
            </button>
          </div>

          {sessions.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '2.5rem', color: 'var(--color-text-muted)' }}>
              <Zap size={28} style={{ margin: '0 auto 0.75rem', opacity: 0.4 }} />
              <p style={{ margin: 0 }}>No runs yet. Start your first task.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {sessions.map(s => (
                <div
                  key={s.session_id}
                  className={clsx('card', activeSession?.session_id === s.session_id && 'glow-border')}
                  style={{ cursor: 'pointer', padding: '0.875rem 1rem' }}
                  onClick={() => handleSelect(s.session_id)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, flex: 1, marginRight: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.task}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className={clsx('badge', STATUS_BADGE[s.status] || 'badge-pending')}>
                        {s.status}
                      </span>
                      <button onClick={(e) => handleDelete(e, s.session_id)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-dim)', padding: 2 }}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                  <ProgressBar value={s.progress} showPercent={false} />
                  <div style={{ display: 'flex', gap: '0.875rem', marginTop: 6, fontSize: 11, color: 'var(--color-text-muted)' }}>
                    {s.repo_path && <span><GitBranch size={10} style={{ display: 'inline', marginRight: 3 }} />{s.repo_path.split('/').pop()}</span>}
                    {s.current_agent && <span><Cpu size={10} style={{ display: 'inline', marginRight: 3 }} />{s.current_agent}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Active session detail */}
        <div>
          <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 0.75rem' }}>Run Detail</h2>
          {activeSession ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              {/* Header */}
              <div className="card" style={{ background: 'linear-gradient(135deg, #1e1b4b22, #1c191700)' }}>
                <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{activeSession.task}</div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: 12 }}>
                  <span className={clsx('badge', STATUS_BADGE[activeSession.status] || 'badge-pending')}>{activeSession.status}</span>
                  {activeSession.current_agent && (
                    <span className="badge badge-pending"><Cpu size={9} /> {activeSession.current_agent}</span>
                  )}
                </div>
                <ProgressBar value={activeSession.progress} label="Overall Progress" />
              </div>

              {/* Pipeline stages */}
              <div className="card">
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Pipeline Stages
                </div>
                {PIPELINE_STAGES.map((stage, idx) => {
                  const reached = activeSession.progress >= stage.progress - 0.01
                  const current = activeSession.current_agent === stage.key || activeSession.status === stage.key
                  return (
                    <div key={stage.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: idx < PIPELINE_STAGES.length - 1 ? '1px solid var(--color-border)' : 'none' }}>
                      <span className={clsx('status-dot', current ? 'running' : reached ? 'done' : 'idle')} />
                      <span style={{ flex: 1, fontSize: 13, color: reached ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{stage.label}</span>
                      {current && <span className="spinner" style={{ width: 12, height: 12 }} />}
                      {reached && !current && <span style={{ fontSize: 11, color: 'var(--color-success)' }}>✓</span>}
                    </div>
                  )
                })}
              </div>

              {/* Stats */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                {[
                  { icon: Clock,       label: 'Elapsed',      value: fmtTime(activeSession.elapsed_seconds) },
                  { icon: Cpu,         label: 'Fix Attempts', value: `${activeSession.fix_attempts}` },
                  { icon: Hash,        label: 'Tokens',       value: (activeSession.total_input_tokens + activeSession.total_output_tokens).toLocaleString() },
                  { icon: DollarSign,  label: 'Est. Cost',    value: `$${activeSession.estimated_cost_usd.toFixed(4)}` },
                ].map(({ icon: Icon, label, value }) => (
                  <div key={label} className="card card-sm" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Icon size={14} color="var(--color-text-muted)" />
                    <div>
                      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{value}</div>
                      <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{label}</div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Error */}
              {activeSession.error && (
                <div className="card" style={{ borderColor: '#7f1d1d', background: '#45090910' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#f87171', marginBottom: 4 }}>Error</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#fca5a5' }}>{activeSession.error}</div>
                </div>
              )}

              {/* PR */}
              {activeSession.pr_url && (
                <a href={activeSession.pr_url} target="_blank" rel="noreferrer" className="btn btn-success" style={{ justifyContent: 'center' }}>
                  <GitBranch size={14} /> View Pull Request
                </a>
              )}

              {/* Quick nav */}
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={() => navigate('/workflow')}>Workflow →</button>
                <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={() => navigate('/diff')}>Diff →</button>
                <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={() => navigate('/logs')}>Logs →</button>
              </div>
            </div>
          ) : (
            <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
              <p style={{ margin: 0 }}>Select a run from the left to see details.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

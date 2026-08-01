import { useEffect, useState } from 'react'
import { Clock, Loader2, RefreshCw, ScrollText } from 'lucide-react'
import { api } from '@/api/client'
import { useSessionStore } from '@/store/session'

interface InteractionLog {
  timestamp: string
  session_id: string
  agent: string
  message: string
  level: string
  input_summary?: string
  decision?: string
  output_summary?: string
  execution_time_ms?: number
  input_tokens?: number
  output_tokens?: number
  estimated_cost_usd?: number
}

const AGENT_COLORS: Record<string, string> = {
  planner:   '#818cf8',
  coder:     '#f59e0b',
  executor:  '#34d399',
  reviewer:  '#a78bfa',
  publisher: '#fb923c',
}

const LEVEL_COLORS: Record<string, string> = {
  info:    'var(--color-text-muted)',
  warning: '#f59e0b',
  error:   '#ef4444',
  debug:   '#475569',
}

export default function Logs() {
  const { activeSession } = useSessionStore()
  const [logs, setLogs] = useState<InteractionLog[]>([])
  const [agentLogs, setAgentLogs] = useState<InteractionLog[]>([])  // from state.logs
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [tab, setTab] = useState<'interactions' | 'agent'>('interactions')

  const load = async () => {
    if (!activeSession) return
    setLoading(true)
    try {
      const [interactionLogs] = await Promise.all([
        api.getSessionLogs(activeSession.session_id),
      ])
      setLogs(interactionLogs as unknown as InteractionLog[])
      setAgentLogs(activeSession.logs as unknown as InteractionLog[])
    } finally { setLoading(false) }
  }

  useEffect(() => {
    load()
  }, [activeSession?.session_id])

  const toggleExpand = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  const displayLogs = tab === 'interactions' ? logs : agentLogs

  if (!activeSession) {
    return (
      <div className="fade-in">
        <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: '3rem' }}>Logs & Reasoning</h1>
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
          <ScrollText size={32} style={{ margin: '0 auto 0.75rem', opacity: 0.4 }} />
          <p style={{ margin: 0 }}>Select a session from the Dashboard first.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Logs & Reasoning</h1>
          <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>
            Transparent view of every agent decision in chronological order.
          </p>
        </div>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: '1.25rem', background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, padding: 4, width: 'fit-content' }}>
        {(['interactions', 'agent'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: '0.4rem 1rem', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 500,
              background: tab === t ? 'var(--color-primary)' : 'transparent',
              color: tab === t ? '#fff' : 'var(--color-text-muted)',
              transition: 'all 0.15s',
            }}
          >
            {t === 'interactions' ? `Interaction Logs (${logs.length})` : `Agent Logs (${agentLogs.length})`}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-text-muted)' }}>
          <Loader2 size={16} style={{ animation: 'spin 0.7s linear infinite' }} /> Loading…
        </div>
      ) : displayLogs.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '2.5rem', color: 'var(--color-text-muted)' }}>
          <p style={{ margin: 0 }}>No logs yet for this session.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {displayLogs.map((log, i) => {
            const agentColor = AGENT_COLORS[log.agent?.toLowerCase()] || '#94a3b8'
            const isOpen = expanded.has(i)
            const hasDetail = log.input_summary || log.decision || log.output_summary

            return (
              <div
                key={i}
                className="card"
                style={{ padding: 0, cursor: hasDetail ? 'pointer' : 'default' }}
                onClick={() => hasDetail && toggleExpand(i)}
              >
                {/* Header row */}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '0.75rem 1rem' }}>
                  {/* Agent pill */}
                  <div style={{
                    flexShrink: 0, padding: '2px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                    background: `${agentColor}22`, color: agentColor, border: `1px solid ${agentColor}44`,
                    textTransform: 'capitalize',
                  }}>
                    {log.agent}
                  </div>

                  {/* Message */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: LEVEL_COLORS[log.level] || 'var(--color-text)', marginBottom: hasDetail ? 2 : 0 }}>
                      {log.message}
                    </div>
                    {hasDetail && (
                      <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                        {isOpen ? '▲ collapse' : '▼ show reasoning'}
                      </div>
                    )}
                  </div>

                  {/* Meta */}
                  <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, color: 'var(--color-text-dim)' }}>
                    {log.execution_time_ms != null && (
                      <span><Clock size={10} style={{ display: 'inline', marginRight: 2 }} />{log.execution_time_ms}ms</span>
                    )}
                    {log.estimated_cost_usd ? <span>${log.estimated_cost_usd.toFixed(4)}</span> : null}
                    <span>{new Date(log.timestamp).toLocaleTimeString()}</span>
                  </div>
                </div>

                {/* Expanded detail */}
                {isOpen && hasDetail && (
                  <div style={{ padding: '0 1rem 1rem', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {log.input_summary && (
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase', marginBottom: 4 }}>Input</div>
                        <div className="code-block" style={{ fontSize: 11 }}>{log.input_summary}</div>
                      </div>
                    )}
                    {log.decision && (
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase', marginBottom: 4 }}>Decision</div>
                        <div style={{ fontSize: 12, padding: '0.5rem 0.75rem', background: '#1e1b4b22', border: '1px solid #312e81', borderRadius: 6, color: '#a5b4fc' }}>
                          {log.decision}
                        </div>
                      </div>
                    )}
                    {log.output_summary && (
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase', marginBottom: 4 }}>Output</div>
                        <div className="code-block" style={{ fontSize: 11 }}>{log.output_summary}</div>
                      </div>
                    )}
                    {(log.input_tokens || log.output_tokens) ? (
                      <div style={{ fontSize: 11, color: 'var(--color-text-dim)', display: 'flex', gap: 16 }}>
                        <span>↑ {log.input_tokens?.toLocaleString()} input tokens</span>
                        <span>↓ {log.output_tokens?.toLocaleString()} output tokens</span>
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

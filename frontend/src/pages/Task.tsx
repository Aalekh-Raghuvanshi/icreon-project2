import { useEffect, useRef, useState } from 'react'
import { Loader2, Play, Sparkles } from 'lucide-react'
import { api, type SessionDetail } from '@/api/client'
import { SessionWebSocket } from '@/api/ws'
import { useSessionStore } from '@/store/session'
import ProgressBar from '@/components/ProgressBar'

const PROMPTS = [
  'Add JWT Authentication',
  'Fix login bug',
  'Improve API performance',
  'Refactor database layer',
  'Add rate limiting middleware',
  'Write unit tests for user service',
  'Add input validation to signup form',
]

export default function Task() {
  const { repoPath, repoUrl, setActiveSession, setActiveSessionId, upsertSession, updateProgress } = useSessionStore()

  const [task, setTask] = useState('')
  const [localPath, setLocalPath] = useState(repoPath)
  const [openPr, setOpenPr] = useState(false)
  const [baseBranch, setBaseBranch] = useState('main')

  const [running, setRunning] = useState(false)
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [liveLog, setLiveLog] = useState<string[]>([])
  const logEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<SessionWebSocket | null>(null)

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [liveLog])

  useEffect(() => {
    setLocalPath(repoPath)
  }, [repoPath])

  useEffect(() => {
    return () => { wsRef.current?.close() }
  }, [])

  async function handleStart() {
    if (!task || !localPath) return
    setRunning(true)
    setLiveLog([])
    setSession(null)

    try {
      const summary = await api.startSession({
        task,
        repo_path: localPath,
        repo_url: repoUrl || undefined,
        open_pr: openPr,
        base_branch: baseBranch,
      })
      upsertSession(summary)
      setActiveSessionId(summary.session_id)

      // Poll for full detail once
      const detail = await api.getSession(summary.session_id)
      setSession(detail)
      setActiveSession(detail)

      // Connect WebSocket for live events
      const ws = new SessionWebSocket(summary.session_id)
      wsRef.current = ws

      ws.onEvent((evt) => {
        const ts = new Date(evt.timestamp).toLocaleTimeString()
        if (evt.message) {
          setLiveLog(prev => [...prev, `[${ts}] ${evt.agent ? `[${evt.agent}] ` : ''}${evt.message}`])
        }
        if (evt.progress != null && evt.status) {
          updateProgress(summary.session_id, evt.progress, evt.status, evt.agent || undefined)
        }
        if (evt.event === 'done' || evt.event === 'error') {
          setRunning(false)
          api.getSession(summary.session_id).then(d => {
            setSession(d)
            setActiveSession(d)
          }).catch(() => {})
        }
      })

      ws.connect()
    } catch (e: unknown) {
      setLiveLog([`Error: ${e instanceof Error ? e.message : String(e)}`])
      setRunning(false)
    }
  }

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>New Task</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>Describe what you want the AI to build or fix.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '1.25rem' }}>
        {/* Left: form */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* Prompt chips */}
          <div className="card">
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 8 }}>
              <Sparkles size={12} style={{ display: 'inline', marginRight: 4 }} />Quick prompts
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {PROMPTS.map(p => (
                <button key={p} className="btn btn-ghost" style={{ fontSize: 11, padding: '0.25rem 0.625rem' }} onClick={() => setTask(p)}>
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* Task input */}
          <div className="card">
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', display: 'block', marginBottom: 8 }}>Task Description</label>
            <textarea
              className="input"
              style={{ minHeight: 120, resize: 'vertical' }}
              placeholder="Describe the task in natural language…&#10;e.g. 'Add JWT authentication with refresh tokens'"
              value={task}
              onChange={e => setTask(e.target.value)}
            />
          </div>

          {/* Config */}
          <div className="card">
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 12 }}>Configuration</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 12, display: 'block', marginBottom: 4, color: 'var(--color-text-muted)' }}>Repository Path</label>
                <input className="input" value={localPath} onChange={e => setLocalPath(e.target.value)} placeholder="/path/to/local/repo" />
              </div>
              <div>
                <label style={{ fontSize: 12, display: 'block', marginBottom: 4, color: 'var(--color-text-muted)' }}>Base Branch</label>
                <input className="input" value={baseBranch} onChange={e => setBaseBranch(e.target.value)} placeholder="main" />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                <input type="checkbox" checked={openPr} onChange={e => setOpenPr(e.target.checked)} />
                Open Pull Request after success
              </label>
            </div>
          </div>

          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', padding: '0.625rem' }} onClick={handleStart} disabled={running || !task || !localPath}>
            {running
              ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Running Pipeline…</>
              : <><Play size={14} /> Launch Agent Pipeline</>}
          </button>
        </div>

        {/* Right: live output */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {session?.plan.summary && (
            <div className="card" style={{ borderColor: '#4338ca44' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#818cf8', marginBottom: 8 }}>Implementation Plan</div>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6 }}>{session.plan.summary}</p>
              {session.plan.steps.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  {session.plan.steps.map((s, i) => (
                    <div key={s.id} style={{ display: 'flex', gap: 8, padding: '6px 0', borderTop: i > 0 ? '1px solid var(--color-border)' : 'none', fontSize: 12 }}>
                      <span style={{ color: s.done ? 'var(--color-success)' : 'var(--color-text-muted)', width: 18, flexShrink: 0, textAlign: 'center' }}>
                        {s.done ? '✓' : `${i + 1}.`}
                      </span>
                      <span style={{ color: s.done ? 'var(--color-text-muted)' : 'var(--color-text)', textDecoration: s.done ? 'line-through' : 'none' }}>
                        {s.description}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Progress */}
          {session && (
            <div className="card">
              <ProgressBar value={session.progress} label={`${session.status}${session.current_agent ? ` · ${session.current_agent}` : ''}`} />
            </div>
          )}

          {/* Live log */}
          <div className="card" style={{ flex: 1 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 8 }}>Live Output</div>
            <div className="code-block" style={{ minHeight: 240, maxHeight: 480 }}>
              {liveLog.length === 0 ? <span style={{ color: 'var(--color-text-dim)' }}>Waiting for pipeline to start…</span> : null}
              {liveLog.map((line, i) => (
                <div key={i} style={{ color: line.startsWith('[') ? 'var(--color-text)' : '#f87171' }}>{line}</div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

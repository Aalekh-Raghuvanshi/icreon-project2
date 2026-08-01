import { useEffect, useState } from 'react'
import { FileCode2, Loader2 } from 'lucide-react'
import { api, type Patch } from '@/api/client'
import { useSessionStore } from '@/store/session'

function parseDiff(diff: string) {
  return diff.split('\n').map((line, i) => ({
    content: line,
    type: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : 'context',
    key: i,
  }))
}

function DiffLine({ line }: { line: { content: string; type: string; key: number } }) {
  const colors: Record<string, { bg: string; color: string }> = {
    add:     { bg: '#052e16', color: '#34d399' },
    remove:  { bg: '#450a0a', color: '#f87171' },
    context: { bg: 'transparent', color: '#64748b' },
  }
  const c = colors[line.type]
  return (
    <div style={{ background: c.bg, color: c.color, padding: '1px 8px', fontFamily: 'var(--font-mono)', fontSize: 12, whiteSpace: 'pre', lineHeight: 1.6 }}>
      {line.content || ' '}
    </div>
  )
}

export default function CodeDiff() {
  const { activeSession } = useSessionStore()
  const [patches, setPatches] = useState<Patch[]>([])
  const [selected, setSelected] = useState<Patch | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!activeSession) return
    setLoading(true)
    api.getSessionDiff(activeSession.session_id)
      .then(p => { setPatches(p); if (p.length > 0) setSelected(p[0]) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [activeSession?.session_id])

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Code Diff</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>Review all code changes produced by the Coder agent.</p>
      </div>

      {!activeSession ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
          <FileCode2 size={32} style={{ margin: '0 auto 0.75rem', opacity: 0.4 }} />
          <p style={{ margin: 0 }}>Select a session from the Dashboard first.</p>
        </div>
      ) : loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-text-muted)' }}>
          <Loader2 size={16} style={{ animation: 'spin 0.7s linear infinite' }} /> Loading patches…
        </div>
      ) : patches.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
          <p style={{ margin: 0 }}>No patches generated yet.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: '1.25rem' }}>
          {/* File list */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '0.75rem 1rem', borderBottom: '1px solid var(--color-border)', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>
              Files Changed ({patches.length})
            </div>
            {patches.map(p => (
              <button
                key={p.file_path}
                onClick={() => setSelected(p)}
                style={{
                  width: '100%',
                  padding: '0.625rem 1rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  background: selected?.file_path === p.file_path ? 'var(--color-surface-2)' : 'transparent',
                  border: 'none',
                  borderBottom: '1px solid var(--color-border)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: selected?.file_path === p.file_path ? 'var(--color-text)' : 'var(--color-text-muted)',
                  fontSize: 12,
                }}
              >
                <FileCode2 size={12} />
                <span style={{ fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.file_path.split('/').pop()}
                </span>
              </button>
            ))}
          </div>

          {/* Diff viewer */}
          <div>
            {selected && (
              <>
                <div className="card" style={{ marginBottom: '0.75rem', padding: '0.75rem 1rem' }}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                    {selected.file_path}
                  </div>
                  {selected.description && (
                    <div style={{ fontSize: 13, color: 'var(--color-text)' }}>{selected.description}</div>
                  )}
                  <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11 }}>
                    <span style={{ color: '#34d399' }}>
                      +{selected.diff.split('\n').filter(l => l.startsWith('+')).length} additions
                    </span>
                    <span style={{ color: '#f87171' }}>
                      -{selected.diff.split('\n').filter(l => l.startsWith('-')).length} deletions
                    </span>
                  </div>
                </div>

                <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                  <div style={{ background: 'var(--color-surface-2)', padding: '0.5rem 1rem', borderBottom: '1px solid var(--color-border)', fontSize: 11, color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)' }}>
                    diff --git {selected.file_path}
                  </div>
                  <div style={{ maxHeight: 600, overflowY: 'auto', background: '#0d0d14' }}>
                    {parseDiff(selected.diff).map(line => (
                      <DiffLine key={line.key} line={line} />
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

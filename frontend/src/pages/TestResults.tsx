import { useEffect, useState } from 'react'
import { CheckCircle, Clock, FlaskConical, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { api, type TestResult } from '@/api/client'
import { useSessionStore } from '@/store/session'

export default function TestResults() {
  const { activeSession } = useSessionStore()
  const [results, setResults] = useState<TestResult[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<TestResult | null>(null)

  const load = async () => {
    if (!activeSession) return
    setLoading(true)
    try {
      const r = await api.getSessionTests(activeSession.session_id)
      setResults(r)
      if (r.length > 0 && !selected) setSelected(r[0])
    } finally { setLoading(false) }
  }

  useEffect(() => {
    load()
  }, [activeSession?.session_id])

  const passed = results.filter(r => r.passed).length
  const failed = results.filter(r => !r.passed).length

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Test Results</h1>
          <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>Build logs, test outcomes, and stack traces.</p>
        </div>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {!activeSession ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
          <FlaskConical size={32} style={{ margin: '0 auto 0.75rem', opacity: 0.4 }} />
          <p style={{ margin: 0 }}>Select a session from the Dashboard first.</p>
        </div>
      ) : (
        <>
          {/* Summary KPIs */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.875rem', marginBottom: '1.25rem' }}>
            {[
              { icon: FlaskConical, label: 'Total',      value: results.length,                    color: '#6366f1' },
              { icon: CheckCircle,  label: 'Passed',     value: passed,                             color: '#10b981' },
              { icon: XCircle,      label: 'Failed',     value: failed,                             color: '#ef4444' },
              { icon: Clock,        label: 'Fix Loops',  value: activeSession.fix_attempts,         color: '#f59e0b' },
            ].map(({ icon: Icon, label, value, color }) => (
              <div key={label} className="card" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Icon size={16} color={color} />
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
                  <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{label}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Pass/fail bar */}
          {results.length > 0 && (
            <div className="card" style={{ marginBottom: '1.25rem' }}>
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                Pass Rate: <strong>{Math.round((passed / results.length) * 100)}%</strong>
              </div>
              <div style={{ display: 'flex', height: 10, borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ width: `${(passed / results.length) * 100}%`, background: '#10b981', transition: 'width 0.4s' }} />
                <div style={{ flex: 1, background: '#ef4444' }} />
              </div>
            </div>
          )}

          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-text-muted)' }}>
              <Loader2 size={16} style={{ animation: 'spin 0.7s linear infinite' }} /> Loading…
            </div>
          ) : results.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '2.5rem', color: 'var(--color-text-muted)' }}>
              <p style={{ margin: 0 }}>No test results yet.</p>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: '1.25rem' }}>
              {/* Test list */}
              <div className="card" style={{ padding: 0 }}>
                <div style={{ padding: '0.75rem 1rem', borderBottom: '1px solid var(--color-border)', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>
                  Test Suites
                </div>
                {results.map(r => (
                  <button
                    key={r.name}
                    onClick={() => setSelected(r)}
                    style={{
                      width: '100%', padding: '0.625rem 1rem', display: 'flex', alignItems: 'center', gap: 10,
                      background: selected?.name === r.name ? 'var(--color-surface-2)' : 'transparent',
                      border: 'none', borderBottom: '1px solid var(--color-border)', cursor: 'pointer', textAlign: 'left',
                    }}
                  >
                    {r.passed
                      ? <CheckCircle size={14} color="#10b981" />
                      : <XCircle size={14} color="#ef4444" />}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--color-text)' }}>
                      {r.name}
                    </span>
                  </button>
                ))}
              </div>

              {/* Selected test detail */}
              {selected && (
                <div>
                  <div className="card" style={{ marginBottom: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                      {selected.passed
                        ? <CheckCircle size={18} color="#10b981" />
                        : <XCircle size={18} color="#ef4444" />}
                      <span style={{ fontWeight: 600, fontSize: 15, fontFamily: 'var(--font-mono)' }}>{selected.name}</span>
                      <span className={selected.passed ? 'badge badge-done' : 'badge badge-failed'}>
                        {selected.passed ? 'PASSED' : 'FAILED'}
                      </span>
                    </div>
                    {!selected.passed && (
                      <div style={{ fontSize: 12, color: 'var(--color-danger)', background: '#45090a22', padding: '0.5rem 0.75rem', borderRadius: 6, fontFamily: 'var(--font-mono)' }}>
                        Test failed — see output below.
                      </div>
                    )}
                  </div>

                  {selected.output && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>Output / Stack Trace</div>
                      <div className="code-block" style={{ maxHeight: 480, overflow: 'auto' }}>
                        {selected.output}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

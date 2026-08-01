import { useEffect, useState } from 'react'
import { CheckCircle, ExternalLink, FileCode2, GitPullRequest, GitBranch, Loader2, XCircle } from 'lucide-react'
import { api } from '@/api/client'
import { useSessionStore } from '@/store/session'

interface PRData {
  branch_name: string | null
  pr_url: string | null
  ci_result: { passed: boolean; output: string } | null
  patches_count: number
  test_results_summary: { passed: number; failed: number }
}

export default function PullRequestPage() {
  const { activeSession } = useSessionStore()
  const [pr, setPR] = useState<PRData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!activeSession) return
    setLoading(true)
    api.getSessionPR(activeSession.session_id)
      .then(d => setPR(d as unknown as PRData))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [activeSession?.session_id])

  if (!activeSession) {
    return (
      <div className="fade-in">
        <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: '3rem' }}>Pull Request</h1>
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--color-text-muted)' }}>
          <GitPullRequest size={32} style={{ margin: '0 auto 0.75rem', opacity: 0.4 }} />
          <p style={{ margin: 0 }}>Select a session from the Dashboard first.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Pull Request</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>Branch, commit, and PR details for the active session.</p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-text-muted)' }}>
          <Loader2 size={16} style={{ animation: 'spin 0.7s linear infinite' }} /> Loading…
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
          {/* Left: PR info */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Branch */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 }}>Branch</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <GitBranch size={16} color="#6366f1" />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}>
                  {pr?.branch_name || activeSession.branch_name || '–'}
                </span>
              </div>
            </div>

            {/* PR title */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 }}>PR Title</div>
              <div style={{ fontSize: 14, fontWeight: 500 }}>
                {activeSession.task}
              </div>
            </div>

            {/* PR description */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 }}>Description</div>
              <div style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--color-text-muted)' }}>
                {activeSession.plan.summary || 'No plan summary available.'}
              </div>
            </div>

            {/* Open PR button */}
            {(pr?.pr_url || activeSession.pr_url) ? (
              <a
                href={pr?.pr_url || activeSession.pr_url || '#'}
                target="_blank"
                rel="noreferrer"
                className="btn btn-success"
                style={{ justifyContent: 'center', textDecoration: 'none', padding: '0.625rem' }}
              >
                <ExternalLink size={14} /> Open Pull Request on GitHub
              </a>
            ) : (
              <div className="card" style={{ textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                PR not yet created. Run with <code>--open-pr</code> to auto-open.
              </div>
            )}
          </div>

          {/* Right: stats + CI */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Files changed */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 }}>Files Modified</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <FileCode2 size={24} color="#6366f1" />
                <div>
                  <div style={{ fontSize: 28, fontWeight: 700 }}>{pr?.patches_count ?? activeSession.patches.length}</div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>files changed</div>
                </div>
              </div>
            </div>

            {/* Test summary */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 12 }}>Test Summary</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700, color: '#10b981' }}>
                    {pr?.test_results_summary.passed ?? activeSession.test_results.filter(t => t.passed).length}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Passed</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700, color: '#ef4444' }}>
                    {pr?.test_results_summary.failed ?? activeSession.test_results.filter(t => !t.passed).length}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Failed</div>
                </div>
              </div>
            </div>

            {/* CI gate */}
            <div className="card">
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 }}>CI Gate</div>
              {pr?.ci_result || activeSession.ci_result ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    {(pr?.ci_result?.passed || activeSession.ci_result?.passed)
                      ? <CheckCircle size={18} color="#10b981" />
                      : <XCircle size={18} color="#ef4444" />}
                    <span style={{ fontWeight: 600 }}>
                      {(pr?.ci_result?.passed || activeSession.ci_result?.passed) ? 'CI Passed' : 'CI Failed'}
                    </span>
                  </div>
                  {(pr?.ci_result?.output || activeSession.ci_result?.output) && (
                    <div className="code-block" style={{ fontSize: 11, maxHeight: 200, overflow: 'auto' }}>
                      {pr?.ci_result?.output || activeSession.ci_result?.output}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: 'var(--color-text-dim)', fontSize: 13 }}>CI not yet run.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, GitBranch, ClipboardList, Network,
  FileCode2, FlaskConical, GitPullRequest, ScrollText, Zap,
} from 'lucide-react'
import { useSessionStore } from '@/store/session'
import clsx from 'clsx'

const NAV = [
  { to: '/',           icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/repository', icon: GitBranch,       label: 'Repository' },
  { to: '/task',       icon: ClipboardList,   label: 'Task' },
  { to: '/workflow',   icon: Network,         label: 'Workflow' },
  { to: '/diff',       icon: FileCode2,       label: 'Code Diff' },
  { to: '/tests',      icon: FlaskConical,    label: 'Test Results' },
  { to: '/pr',         icon: GitPullRequest,  label: 'Pull Request' },
  { to: '/logs',       icon: ScrollText,      label: 'Logs & Reasoning' },
]

const STATUS_COLOR: Record<string, string> = {
  done:      'badge-done',
  failed:    'badge-failed',
  executing: 'badge-executing',
  coding:    'badge-coding',
  planning:  'badge-planning',
  reviewing: 'badge-reviewing',
  pending:   'badge-pending',
}

export default function Sidebar() {
  const { activeSession } = useSessionStore()
  const location = useLocation()

  return (
    <aside style={{
      width: 220,
      flexShrink: 0,
      background: 'var(--color-surface)',
      borderRight: '1px solid var(--color-border)',
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      position: 'sticky',
      top: 0,
    }}>
      {/* Logo */}
      <div style={{ padding: '1.25rem 1rem', borderBottom: '1px solid var(--color-border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div style={{
            width: 32, height: 32,
            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            borderRadius: 8,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Zap size={16} color="#fff" />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--color-text)' }}>AI SWE</div>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)', lineHeight: 1 }}>Agent</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '0.75rem 0.5rem', overflowY: 'auto' }}>
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => clsx('nav-item', isActive && 'active')}
            style={{ marginBottom: 2 }}
          >
            <Icon size={15} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Active session mini-status */}
      {activeSession && (
        <div style={{ padding: '0.875rem 1rem', borderTop: '1px solid var(--color-border)' }}>
          <div style={{ fontSize: 10, color: 'var(--color-text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Active Run
          </div>
          <div style={{
            fontSize: 12, color: 'var(--color-text)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            marginBottom: 6,
          }}>
            {activeSession.task}
          </div>
          <span className={clsx('badge', STATUS_COLOR[activeSession.status] || 'badge-pending')}>
            {activeSession.status}
          </span>
          {/* Mini progress */}
          <div className="progress-track" style={{ marginTop: 8 }}>
            <div className="progress-fill" style={{ width: `${Math.round(activeSession.progress * 100)}%` }} />
          </div>
        </div>
      )}

      {/* Footer */}
      <div style={{ padding: '0.75rem 1rem', borderTop: '1px solid var(--color-border)', fontSize: 11, color: 'var(--color-text-dim)' }}>
        v0.1.0 · MCP + LangGraph
      </div>
    </aside>
  )
}

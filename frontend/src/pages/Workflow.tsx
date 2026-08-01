import { useCallback, useEffect } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap,
  type Node, type Edge,
  Handle, Position, BackgroundVariant,
  useNodesState, useEdgesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useSessionStore } from '@/store/session'
import clsx from 'clsx'

// ── Agent node statuses ───────────────────────────────────────────

type NodeStatus = 'idle' | 'running' | 'done' | 'failed' | 'waiting'

function statusFromPipeline(agentKey: string, status: string, currentAgent: string | null): NodeStatus {
  const order = ['planner', 'coder', 'executor', 'reviewer', 'publisher']
  const statusMap: Record<string, string> = {
    planning: 'planner', coding: 'coder', executing: 'executor', reviewing: 'reviewer', done: 'publisher',
  }
  const activeAgent = statusMap[status] || currentAgent || ''
  const idx = order.indexOf(agentKey)
  const activeIdx = order.indexOf(activeAgent)

  if (status === 'failed' && currentAgent === agentKey) return 'failed'
  if (agentKey === activeAgent) return 'running'
  if (idx < activeIdx) return 'done'
  return 'idle'
}

// ── Custom node (using plain function with typed props object) ────

function AgentNode({ data }: { data: { label: string; description: string; status: NodeStatus } }) {
  const colors: Record<NodeStatus, { border: string; bg: string; dot: string; label: string }> = {
    idle:    { border: '#1e1e2e', bg: '#111118', dot: '#334155', label: '#64748b' },
    waiting: { border: '#44357a', bg: '#1e1b4b22', dot: '#f59e0b', label: '#f59e0b' },
    running: { border: '#6366f1', bg: '#1e1b4b44', dot: '#10b981', label: '#a5b4fc' },
    done:    { border: '#065f46', bg: '#05180e',   dot: '#10b981', label: '#34d399' },
    failed:  { border: '#7f1d1d', bg: '#450a0a22', dot: '#ef4444', label: '#f87171' },
  }
  const c = colors[data.status]

  return (
    <div style={{
      background: c.bg,
      border: `2px solid ${c.border}`,
      borderRadius: 12,
      padding: '14px 20px',
      minWidth: 160,
      boxShadow: data.status === 'running' ? `0 0 20px ${c.border}66` : 'none',
      transition: 'all 0.3s',
    }}>
      <Handle type="target" position={Position.Top} style={{ background: c.border }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span className={clsx('status-dot', data.status)} style={{ background: c.dot }} />
        <span style={{ fontWeight: 600, fontSize: 14, color: c.label }}>{data.label}</span>
        {data.status === 'running' && <span className="spinner" style={{ width: 12, height: 12, marginLeft: 'auto' }} />}
      </div>
      <div style={{ fontSize: 11, color: '#475569', lineHeight: 1.4 }}>{data.description}</div>
      <Handle type="source" position={Position.Bottom} style={{ background: c.border }} />
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes: Record<string, any> = { agentNode: AgentNode }

// ── Agent definitions ─────────────────────────────────────────────

const AGENTS = [
  { id: 'planner',   label: 'Planner',   description: 'Analyses codebase, generates implementation plan', x: 280, y: 0 },
  { id: 'coder',     label: 'Coder',     description: 'Generates patches using EditEngine',                x: 280, y: 140 },
  { id: 'executor',  label: 'Executor',  description: 'Runs test suite in Sandbox (Docker/local)',         x: 280, y: 280 },
  { id: 'reviewer',  label: 'Reviewer',  description: 'Triages failures, triggers Coder fix loop',         x: 280, y: 420 },
  { id: 'publisher', label: 'Publisher', description: 'CI gate → branch → commit → push → PR',            x: 280, y: 560 },
]

const EDGES_BASE: Edge[] = [
  { id: 'e1', source: 'planner',  target: 'coder',     type: 'smoothstep' },
  { id: 'e2', source: 'coder',    target: 'executor',  type: 'smoothstep' },
  { id: 'e3', source: 'executor', target: 'reviewer',  type: 'smoothstep' },
  { id: 'e4', source: 'reviewer', target: 'publisher', type: 'smoothstep' },
  { id: 'e5', source: 'reviewer', target: 'coder',     type: 'smoothstep',
    label: 'retry', style: { stroke: '#f59e0b', strokeDasharray: '5,3' }, labelStyle: { fill: '#f59e0b', fontSize: 11 } },
]

// ── Page ──────────────────────────────────────────────────────────

export default function Workflow() {
  const { activeSession } = useSessionStore()
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  const status = activeSession?.status || 'pending'
  const currentAgent = activeSession?.current_agent || null

  const buildNodes = useCallback((): Node[] => {
    return AGENTS.map(a => ({
      id: a.id,
      type: 'agentNode',
      position: { x: a.x, y: a.y },
      data: {
        label: a.label,
        description: a.description,
        status: statusFromPipeline(a.id, status, currentAgent),
      },
      draggable: true,
    }))
  }, [status, currentAgent])

  const buildEdges = useCallback((): Edge[] => {
    return EDGES_BASE.map(e => ({
      ...e,
      animated: (
        (e.source === 'planner' && status === 'planning') ||
        (e.source === 'coder' && status === 'coding') ||
        (e.source === 'executor' && status === 'executing') ||
        (e.source === 'reviewer' && (status === 'reviewing' || status === 'coding'))
      ),
    }))
  }, [status])

  useEffect(() => {
    setNodes(buildNodes())
    setEdges(buildEdges())
  }, [buildNodes, buildEdges, setNodes, setEdges])

  const legend: Array<{ status: NodeStatus; label: string }> = [
    { status: 'idle', label: 'Idle / Not started' },
    { status: 'running', label: 'Currently executing' },
    { status: 'done', label: 'Completed' },
    { status: 'failed', label: 'Failed' },
  ]

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 3.5rem)' }}>
      <div style={{ marginBottom: '1.25rem' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, marginBottom: 4 }}>Multi-Agent Workflow</h1>
        <p style={{ color: 'var(--color-text-muted)', margin: 0 }}>
          Real-time visualization of the agent pipeline.
          {activeSession ? <span> Session: <code style={{ fontSize: 11 }}>{activeSession.session_id.slice(0, 8)}</code></span> : ' No active session.'}
        </p>
      </div>

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 220px', gap: '1rem', minHeight: 0 }}>
        <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius)', overflow: 'hidden', background: 'var(--color-bg)' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            minZoom={0.4}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1e1e2e" />
            <Controls style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }} />
            <MiniMap
              style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
              nodeColor="#6366f130"
            />
          </ReactFlow>
        </div>

        {/* Side panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
          <div className="card">
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>Pipeline Status</div>
            {activeSession ? (
              <>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                  {activeSession.task.length > 50 ? activeSession.task.slice(0, 50) + '…' : activeSession.task}
                </div>
                <span className={`badge badge-${activeSession.status}`}>{activeSession.status}</span>
                <div className="progress-track" style={{ marginTop: 10 }}>
                  <div className="progress-fill" style={{ width: `${Math.round(activeSession.progress * 100)}%` }} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 6 }}>
                  {Math.round(activeSession.progress * 100)}% complete
                </div>
              </>
            ) : (
              <div style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>No active session</div>
            )}
          </div>

          <div className="card">
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>Agents</div>
            {AGENTS.map(a => {
              const s = statusFromPipeline(a.id, status, currentAgent)
              return (
                <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--color-border)', fontSize: 12 }}>
                  <span className={clsx('status-dot', s)} />
                  <span style={{ flex: 1, color: s === 'idle' ? 'var(--color-text-muted)' : 'var(--color-text)' }}>{a.label}</span>
                  {s === 'running' && <span className="spinner" style={{ width: 10, height: 10 }} />}
                  {s === 'done'    && <span style={{ fontSize: 10, color: 'var(--color-success)' }}>✓</span>}
                  {s === 'failed'  && <span style={{ fontSize: 10, color: 'var(--color-danger)' }}>✗</span>}
                </div>
              )
            })}
          </div>

          <div className="card">
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>Legend</div>
            {legend.map(({ status: s, label }) => (
              <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontSize: 11, color: 'var(--color-text-muted)' }}>
                <span className={clsx('status-dot', s)} />
                {label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

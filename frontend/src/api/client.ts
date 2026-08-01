// REST API client — all calls proxy through Vite to http://localhost:8000/api

const BASE = '/api'

export interface SessionSummary {
  session_id: string
  task: string
  status: string
  repo_path: string | null
  repo_url: string | null
  started_at: string | null
  finished_at: string | null
  progress: number
  current_agent: string | null
  estimated_cost_usd: number
  total_tokens: number
  pr_url: string | null
}

export interface PlanStep {
  id: string
  description: string
  done: boolean
  files_involved: string[]
  reasoning: string
  risk_level: string
}

export interface Plan {
  summary: string | null
  steps: PlanStep[]
  files_to_create: string[]
  files_to_modify: string[]
  architecture_impact: string
  testing_strategy: string
}

export interface Patch {
  file_path: string
  diff: string
  description: string | null
}

export interface TestResult {
  name: string
  passed: boolean
  output: string | null
}

export interface LogEntry {
  timestamp: string
  agent: string
  message: string
  level: string
  input_summary: string | null
  decision: string | null
  output_summary: string | null
  execution_time_ms: number | null
}

export interface CIResult {
  passed: boolean
  output: string
}

export interface SessionDetail extends SessionSummary {
  elapsed_seconds: number | null
  plan: Plan
  patches: Patch[]
  test_results: TestResult[]
  logs: LogEntry[]
  error: string | null
  fix_attempts: number
  ci_result: CIResult | null
  branch_name: string | null
  total_input_tokens: number
  total_output_tokens: number
  retry_count: number
}

export interface StartRunRequest {
  task: string
  repo_path: string
  repo_url?: string
  open_pr?: boolean
  base_branch?: string
}

export interface CloneRequest {
  repo_url: string
  dest_path?: string
}

export interface CloneResult {
  success: boolean
  dest_path: string
  branch: string | null
  commit_hash: string | null
  message: string
}

export interface RepoTreeNode {
  path: string
  is_dir: boolean
  size_bytes: number | null
}

async function req<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`API error ${res.status}: ${err}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  health: () => req<{ status: string; version: string }>('/health'),

  // Sessions
  listSessions: () => req<SessionSummary[]>('/sessions'),
  getSession:   (id: string) => req<SessionDetail>(`/sessions/${id}`),
  startSession: (body: StartRunRequest) => req<SessionSummary>('/sessions', { method: 'POST', body: JSON.stringify(body) }),
  deleteSession: (id: string) => req<void>(`/sessions/${id}`, { method: 'DELETE' }),

  // Sub-resources
  getSessionLogs:    (id: string) => req<Record<string, unknown>[]>(`/sessions/${id}/logs`),
  getSessionDiff:    (id: string) => req<Patch[]>(`/sessions/${id}/diff`),
  getSessionTests:   (id: string) => req<TestResult[]>(`/sessions/${id}/test-results`),
  getSessionPR:      (id: string) => req<Record<string, unknown>>(`/sessions/${id}/pr`),

  // Repository
  cloneRepo:    (body: CloneRequest) => req<CloneResult>('/repo/clone', { method: 'POST', body: JSON.stringify(body) }),
  repoTree:     (path: string) => req<RepoTreeNode[]>(`/repo/tree?path=${encodeURIComponent(path)}`),
  searchFiles:  (path: string, q: string) => req<RepoTreeNode[]>(`/repo/search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}`),
  analyzeRepo:  (path: string) => req<Record<string, unknown>>(`/repo/analyze?path=${encodeURIComponent(path)}`),
}

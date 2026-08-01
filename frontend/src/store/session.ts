import { create } from 'zustand'
import type { SessionDetail, SessionSummary } from '@/api/client'

interface SessionStore {
  sessions: SessionSummary[]
  activeSession: SessionDetail | null
  activeSessionId: string | null

  // Repo state (for Repository page)
  repoPath: string
  repoUrl: string

  setSessions: (s: SessionSummary[]) => void
  upsertSession: (s: SessionSummary) => void
  setActiveSession: (s: SessionDetail | null) => void
  setActiveSessionId: (id: string | null) => void
  setRepoPath: (p: string) => void
  setRepoUrl: (u: string) => void

  // Optimistic progress update from WS events
  updateProgress: (sessionId: string, progress: number, status: string, agent?: string) => void
}

export const useSessionStore = create<SessionStore>((set) => ({
  sessions: [],
  activeSession: null,
  activeSessionId: null,
  repoPath: '',
  repoUrl: '',

  setSessions: (sessions) => set({ sessions }),
  upsertSession: (s) =>
    set((state) => {
      const idx = state.sessions.findIndex((x) => x.session_id === s.session_id)
      const next = idx >= 0
        ? state.sessions.map((x, i) => (i === idx ? s : x))
        : [s, ...state.sessions]
      return { sessions: next }
    }),
  setActiveSession: (activeSession) => set({ activeSession }),
  setActiveSessionId: (activeSessionId) => set({ activeSessionId }),
  setRepoPath: (repoPath) => set({ repoPath }),
  setRepoUrl: (repoUrl) => set({ repoUrl }),

  updateProgress: (sessionId, progress, status, agent) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId
          ? { ...s, progress, status, current_agent: agent ?? s.current_agent }
          : s
      ),
      activeSession:
        state.activeSession?.session_id === sessionId
          ? { ...state.activeSession, progress, status, current_agent: agent ?? state.activeSession.current_agent }
          : state.activeSession,
    })),
}))

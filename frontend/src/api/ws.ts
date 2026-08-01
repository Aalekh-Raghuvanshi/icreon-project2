// WebSocket client for real-time agent event streaming

export interface AgentEvent {
  event: 'progress' | 'agent_started' | 'agent_finished' | 'log' | 'error' | 'done' | 'ping'
  session_id: string
  timestamp: string
  agent?: string
  message?: string
  status?: string
  progress?: number
  data?: Record<string, unknown>
}

export type EventHandler = (event: AgentEvent) => void

export class SessionWebSocket {
  private ws: WebSocket | null = null
  private handlers: Set<EventHandler> = new Set()
  private sessionId: string
  private reconnectAttempts = 0
  private maxReconnects = 3

  constructor(sessionId: string) {
    this.sessionId = sessionId
  }

  connect(): void {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/${this.sessionId}`

    this.ws = new WebSocket(url)

    this.ws.onmessage = (e) => {
      try {
        const evt: AgentEvent = JSON.parse(e.data)
        if (evt.event === 'ping') return
        this.handlers.forEach((h) => h(evt))
      } catch {
        // ignore parse errors
      }
    }

    this.ws.onclose = () => {
      if (this.reconnectAttempts < this.maxReconnects) {
        this.reconnectAttempts++
        setTimeout(() => this.connect(), 1500 * this.reconnectAttempts)
      }
    }

    this.ws.onerror = () => {
      // onerror always precedes onclose; let onclose handle reconnect
    }
  }

  onEvent(handler: EventHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  close(): void {
    this.maxReconnects = 0 // prevent auto-reconnect
    this.ws?.close()
    this.handlers.clear()
  }
}

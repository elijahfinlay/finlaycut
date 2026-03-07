import { useState, useEffect } from 'react'
import { getBaseUrl } from './useApi'
import { useOperationStore } from '../stores/operationStore'

export interface WSMessage {
  type: 'progress' | 'complete' | 'error' | 'resolve_status' | 'timeline_changed'
  operation?: string
  progress?: number
  message?: string
  result?: any
  error?: string
  timeline_signature?: string
  timeline_name?: string
}

// ---- Singleton WebSocket manager ----
// One connection shared by all components. Fixes bug where each panel
// opened its own WebSocket and progress was lost when switching panels.

type Listener = (msg: WSMessage) => void

const listeners = new Set<Listener>()
let ws: WebSocket | null = null
let wsConnected = false
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let interruptTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
let pendingMessage: Record<string, unknown> | null = null

function interruptRunningOperations() {
  const store = useOperationStore.getState()
  const running = Object.entries(store.operations)
    .filter(([, op]) => op.status === 'running')
    .map(([name]) => name)

  if (running.length === 0) return

  for (const operation of running) {
    store.failOperation(operation, 'Connection to the backend was lost. The operation was interrupted.')
  }
}

function connectWs() {
  try {
    const wsUrl = getBaseUrl().replace('http', 'ws') + '/ws'
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      wsConnected = true
      reconnectDelay = 1000 // reset on successful connect
      if (interruptTimer) {
        clearTimeout(interruptTimer)
        interruptTimer = null
      }
      if (pendingMessage) {
        ws!.send(JSON.stringify(pendingMessage))
        pendingMessage = null
      }
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSMessage

        // Write directly to operationStore BEFORE fan-out to listeners.
        // This ensures progress/complete/error state survives component unmounts.
        if (msg.operation) {
          const store = useOperationStore.getState()
          switch (msg.type) {
            case 'progress':
              store.updateProgress(msg.operation, msg.progress || 0, msg.message || '')
              break
            case 'complete':
              store.completeOperation(msg.operation, msg.result)
              break
            case 'error':
              store.failOperation(msg.operation, msg.error || 'Unknown error')
              break
          }
        }

        listeners.forEach((fn) => fn(msg))
      } catch {
        // ignore malformed
      }
    }

    ws.onclose = () => {
      wsConnected = false
      ws = null
      if (interruptTimer) clearTimeout(interruptTimer)
      interruptTimer = setTimeout(() => {
        interruptTimer = null
        if (!wsConnected) {
          interruptRunningOperations()
        }
      }, 1500)
      reconnectTimer = setTimeout(connectWs, reconnectDelay)
      reconnectDelay = Math.min(reconnectDelay * 2, 30000) // backoff: 1→2→4→8→...→30s
    }

    ws.onerror = () => {
      ws?.close()
    }
  } catch {
    reconnectTimer = setTimeout(connectWs, reconnectDelay)
    reconnectDelay = Math.min(reconnectDelay * 2, 30000)
  }
}

// Start the singleton connection once
connectWs()

export function subscribe(fn: Listener) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function sendWsMessage(msg: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg))
  } else {
    pendingMessage = msg
  }
}

// ---- Hooks ----

export function useWebSocket() {
  const [connected, setConnected] = useState(wsConnected)
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null)

  useEffect(() => {
    const unsub = subscribe((msg) => {
      setLastMessage(msg)
      setConnected(true)
    })
    return unsub
  }, [])

  return { connected, lastMessage }
}

/**
 * Read operation progress from the global operationStore (survives unmount).
 * Drop-in replacement for the old useState-based useProgress.
 */
export function useProgress(operation: string) {
  const op = useOperationStore((s) => s.operations[operation])
  const resetOperation = useOperationStore((s) => s.resetOperation)

  return {
    progress: op?.progress ?? 0,
    message: op?.message ?? '',
    isComplete: op?.status === 'complete',
    error: op?.error ?? null,
    elapsed: op?.elapsed ?? 0,
    eta: op?.eta ?? null,
    reset: () => resetOperation(operation),
  }
}

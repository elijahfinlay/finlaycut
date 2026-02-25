import { useState, useEffect, useRef, useCallback } from 'react'
import { getBaseUrl } from './useApi'

export interface WSMessage {
  type: 'progress' | 'complete' | 'error' | 'resolve_status'
  operation?: string
  progress?: number
  message?: string
  result?: any
  error?: string
}

// ---- Singleton WebSocket manager ----
// One connection shared by all components. Fixes bug where each panel
// opened its own WebSocket and progress was lost when switching panels.

type Listener = (msg: WSMessage) => void

const listeners = new Set<Listener>()
let ws: WebSocket | null = null
let wsConnected = false
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

function connectWs() {
  try {
    const wsUrl = getBaseUrl().replace('http', 'ws') + '/ws'
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      wsConnected = true
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSMessage
        listeners.forEach((fn) => fn(msg))
      } catch {
        // ignore malformed
      }
    }

    ws.onclose = () => {
      wsConnected = false
      ws = null
      reconnectTimer = setTimeout(connectWs, 3000)
    }

    ws.onerror = () => {
      ws?.close()
    }
  } catch {
    reconnectTimer = setTimeout(connectWs, 3000)
  }
}

// Start the singleton connection once
connectWs()

function subscribe(fn: Listener) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
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

export function useProgress(operation: string) {
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [isComplete, setIsComplete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const unsub = subscribe((msg) => {
      if (msg.operation !== operation) return

      switch (msg.type) {
        case 'progress':
          setProgress(msg.progress || 0)
          setMessage(msg.message || '')
          setIsComplete(false)
          setError(null)
          break
        case 'complete':
          setProgress(1)
          setIsComplete(true)
          setError(null)
          break
        case 'error':
          setError(msg.error || 'Unknown error')
          break
      }
    })
    return unsub
  }, [operation])

  const reset = useCallback(() => {
    setProgress(0)
    setMessage('')
    setIsComplete(false)
    setError(null)
  }, [])

  return { progress, message, isComplete, error, reset }
}

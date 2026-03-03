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
let reconnectDelay = 1000

function connectWs() {
  try {
    const wsUrl = getBaseUrl().replace('http', 'ws') + '/ws'
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      wsConnected = true
      reconnectDelay = 1000 // reset on successful connect
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
  const [startTime, setStartTime] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [eta, setEta] = useState<number | null>(null)
  const stalledCountRef = useRef(0)
  const lastProgressRef = useRef(0)

  useEffect(() => {
    const unsub = subscribe((msg) => {
      if (msg.operation !== operation) return

      switch (msg.type) {
        case 'progress': {
          const p = msg.progress || 0
          const now = Date.now()

          // Detect stalled progress (same value for 3+ consecutive updates)
          if (Math.abs(p - lastProgressRef.current) < 0.001) {
            stalledCountRef.current++
          } else {
            stalledCountRef.current = 0
          }
          lastProgressRef.current = p
          const isStalled = stalledCountRef.current >= 3

          if (startTime === null || p < 0.01) {
            setStartTime(now)
            setElapsed(0)
            setEta(null)
            stalledCountRef.current = 0
          } else {
            const elapsedMs = now - (startTime ?? now)
            setElapsed(elapsedMs)
            if (isStalled) {
              // Progress isn't advancing — hide ETA, show only elapsed
              setEta(null)
            } else if (p > 0.05) {
              setEta((elapsedMs / p) * (1 - p))
            }
          }

          setProgress(p)
          setMessage(msg.message || '')
          setIsComplete(false)
          setError(null)
          break
        }
        case 'complete':
          setProgress(1)
          setIsComplete(true)
          setError(null)
          setEta(null)
          break
        case 'error':
          setError(msg.error || 'Unknown error')
          setEta(null)
          break
      }
    })
    return unsub
  }, [operation, startTime])

  const reset = useCallback(() => {
    setProgress(0)
    setMessage('')
    setIsComplete(false)
    setError(null)
    setStartTime(null)
    setElapsed(0)
    setEta(null)
  }, [])

  return { progress, message, isComplete, error, reset, elapsed, eta }
}

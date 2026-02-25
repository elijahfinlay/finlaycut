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

export function useWebSocket() {
  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<NodeJS.Timeout | null>(null)

  const connect = useCallback(() => {
    try {
      const wsUrl = getBaseUrl().replace('http', 'ws') + '/ws'
      const ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        setConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WSMessage
          setLastMessage(msg)
        } catch {
          // Ignore malformed messages
        }
      }

      ws.onclose = () => {
        setConnected(false)
        // Reconnect after 3 seconds
        reconnectTimer.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        ws.close()
      }

      wsRef.current = ws
    } catch {
      reconnectTimer.current = setTimeout(connect, 3000)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connected, lastMessage }
}

export function useProgress(operation: string) {
  const { lastMessage } = useWebSocket()
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [isComplete, setIsComplete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!lastMessage || lastMessage.operation !== operation) return

    switch (lastMessage.type) {
      case 'progress':
        setProgress(lastMessage.progress || 0)
        setMessage(lastMessage.message || '')
        setIsComplete(false)
        setError(null)
        break
      case 'complete':
        setProgress(1)
        setIsComplete(true)
        setError(null)
        break
      case 'error':
        setError(lastMessage.error || 'Unknown error')
        break
    }
  }, [lastMessage, operation])

  const reset = useCallback(() => {
    setProgress(0)
    setMessage('')
    setIsComplete(false)
    setError(null)
  }, [])

  return { progress, message, isComplete, error, reset }
}

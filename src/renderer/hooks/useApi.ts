import { useState, useCallback } from 'react'

// Default URL — updated asynchronously via IPC if available
let _baseUrl = 'http://127.0.0.1:42069'

// Attempt to fetch the real backend URL via IPC (non-blocking)
if (typeof window !== 'undefined' && (window as any).api?.getBackendUrl) {
  ;(window as any).api.getBackendUrl().then((url: string) => {
    if (url) _baseUrl = url
  }).catch(() => {
    // IPC unavailable — keep default
  })
}

export function getBaseUrl(): string {
  return _baseUrl
}

// Default timeout for normal requests; heavy ops get 5 hours
const DEFAULT_TIMEOUT_MS = 30_000
const HEAVY_TIMEOUT_MS = 18_000_000  // 5 hours
const HEAVY_PATHS = ['/api/transcript-editor/transcribe', '/api/transcript-editor/apply', '/api/transcript-editor/reconcile', '/api/silence/analyze', '/api/filler-words/analyze', '/api/captions/generate', '/api/viral/detect', '/api/broll/suggest', '/api/scenes/detect', '/api/diarization', '/api/waveform/analyze']

function getTimeoutForPath(path: string): number {
  if (HEAVY_PATHS.some((p) => path.startsWith(p))) return HEAVY_TIMEOUT_MS
  return DEFAULT_TIMEOUT_MS
}

export async function apiFetch<T = any>(
  path: string,
  options?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const url = `${_baseUrl}${path}`
  const timeoutMs = options?.timeoutMs ?? getTimeoutForPath(path)

  // Wire up timeout via AbortController (respects caller's signal too)
  const timeoutController = new AbortController()
  // timeoutMs === 0 means no timeout (for long-running ops like transcription)
  const timer = timeoutMs > 0 ? setTimeout(() => timeoutController.abort(), timeoutMs) : null

  // If caller provided a signal, forward its abort
  const callerSignal = options?.signal
  if (callerSignal) {
    if (callerSignal.aborted) {
      if (timer) clearTimeout(timer)
      throw new DOMException('Aborted', 'AbortError')
    }
    callerSignal.addEventListener('abort', () => timeoutController.abort(), { once: true })
  }

  try {
    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers
      },
      ...options,
      signal: timeoutController.signal
    })

    if (!response.ok) {
      const text = await response.text()

      // Parse 409 (operation lock) into a user-friendly message
      if (response.status === 409) {
        let detail = 'Another operation is already running. Please wait for it to finish.'
        try {
          const parsed = JSON.parse(text)
          if (parsed.detail) detail = parsed.detail
        } catch {
          // Use default message
        }
        const err = new Error(detail) as any
        err.status = 409
        throw err
      }

      throw new Error(`API error ${response.status}: ${text}`)
    }

    return response.json()
  } catch (err: any) {
    // User-friendly messages for common network errors
    if (err.name === 'AbortError') {
      if (callerSignal?.aborted) {
        throw new Error('Request was cancelled')
      }
      throw new Error('Request timed out — the backend may be busy or unresponsive')
    }
    if (err instanceof TypeError && err.message === 'Failed to fetch') {
      throw new Error('Cannot reach the backend. Make sure the app is fully started.')
    }
    throw err
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/**
 * Creates an AbortController for cancellable API requests.
 * Usage:
 *   const { controller, signal } = createAbortController()
 *   apiFetch('/api/...', { signal })
 *   controller.abort() // to cancel
 */
export function createAbortController() {
  const controller = new AbortController()
  return { controller, signal: controller.signal }
}

export function useApi<T = any>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const execute = useCallback(
    async (body?: any, method: string = 'POST') => {
      setLoading(true)
      setError(null)
      try {
        const fetchOptions: RequestInit = { method }
        if (body !== undefined) {
          fetchOptions.body = JSON.stringify(body)
        }
        const result = await apiFetch<T>(path, fetchOptions)
        setData(result)
        return result
      } catch (err: any) {
        const msg = err.message || 'Unknown error'
        setError(msg)
        throw err
      } finally {
        setLoading(false)
      }
    },
    [path]
  )

  return { data, loading, error, execute }
}

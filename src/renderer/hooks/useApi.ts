import { useState, useCallback } from 'react'

const _baseUrl = 'http://127.0.0.1:42069'

export function getBaseUrl(): string {
  return _baseUrl
}

export async function apiFetch<T = any>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${_baseUrl}${path}`
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers
    },
    ...options
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`API error ${response.status}: ${text}`)
  }

  return response.json()
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

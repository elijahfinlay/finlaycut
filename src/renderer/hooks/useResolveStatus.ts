import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from './useApi'

export interface ResolveStatus {
  connected: boolean
  project: {
    name: string
    timeline_count: number
  } | null
  timeline: {
    name: string
    duration: number
    start_frame: number
    end_frame: number
    video_tracks: number
    audio_tracks: number
    subtitle_tracks: number
    framerate: string
    resolution: string
  } | null
}

export function useResolveStatus(pollInterval = 2000) {
  const [status, setStatus] = useState<ResolveStatus>({
    connected: false,
    project: null,
    timeline: null
  })
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await apiFetch<ResolveStatus>('/api/resolve/status')
      setStatus(data)
      setError(null)
    } catch (err: any) {
      setStatus({ connected: false, project: null, timeline: null })
      setError('Backend not reachable')
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, pollInterval)
    return () => clearInterval(timer)
  }, [refresh, pollInterval])

  return { status, error, refresh }
}

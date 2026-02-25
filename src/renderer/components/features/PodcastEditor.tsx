import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

export function PodcastEditor() {
  const [tracks, setTracks] = useState<any[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('podcast')

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setTracks([])
    setResult(null)
    try {
      const data = await apiFetch('/api/podcast/analyze', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setTracks(data.tracks || [])
        setResult(data)
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setAnalyzing(false)
    }
  }

  const handleApply = async () => {
    try {
      const data = await apiFetch('/api/podcast/apply', {
        method: 'POST',
        body: JSON.stringify([])
      })
      setResult(data)
    } catch (err: any) {
      setResult({ error: err.message })
    }
  }

  return (
    <div className="feature-panel">
      <TimelineBanner />
      <div>
        <h2 className="feature-title">Podcast Multi-Track Editor</h2>
        <p className="feature-description">
          For multi-camera podcast recordings: auto-detects which person is speaking
          and switches camera angles accordingly. Requires at least 2 audio tracks.
        </p>
      </div>

      <Button onClick={handleAnalyze} loading={analyzing} size="lg">
        Analyze Tracks
      </Button>

      {analyzing && <ProgressBar progress={progress} message={message} />}

      {tracks.length > 0 && (
        <Card>
          <div className="space-y-4">
            <h3 className="text-sm font-semibold text-resolve-text">
              Audio Tracks ({tracks.length})
            </h3>
            {tracks.map((track, i) => (
              <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-resolve-bg">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: ['#6c63ff', '#4ade80', '#fbbf24', '#f87171'][i % 4] }}
                />
                <div>
                  <p className="text-sm text-resolve-text">Track {track.track_index}</p>
                  <p className="text-xs text-resolve-textMuted">{track.track_name || 'Unnamed'}</p>
                </div>
              </div>
            ))}

            <ConfirmApply onConfirm={handleApply}>
              Apply Camera Switching
            </ConfirmApply>
          </div>
        </Card>
      )}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
    </div>
  )
}

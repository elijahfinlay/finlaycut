import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface TrackInfo {
  track_index: number
  track_name: string
}

interface SwitchingEntry {
  start: number
  end: number
  track_index: number
}

export function PodcastEditor() {
  const [tracks, setTracks] = useState<TrackInfo[]>([])
  const [schedule, setSchedule] = useState<SwitchingEntry[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('podcast')

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setTracks([])
    setResult(null)
    try {
      const data = await apiFetch('/api/podcast/analyze', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setTracks(data.tracks || [])
        setSchedule(data.switching_schedule || [])
        setResult(data)
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setAnalyzing(false)
    }
  }

  const handleJump = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds })
      })
    } catch {
      // Ignore jump errors
    }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      const data = await apiFetch('/api/podcast/apply', {
        method: 'POST',
        body: JSON.stringify(schedule)
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Camera switching applied')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="feature-panel">
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

      {analyzing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

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

            {schedule.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-resolve-textDim uppercase tracking-wide">
                  Auto Switching Plan ({schedule.length})
                </h4>
                <div className="max-h-40 overflow-y-auto space-y-1">
                  {schedule.map((entry, i) => (
                    <button
                      key={`${entry.start}-${entry.end}-${entry.track_index}-${i}`}
                      onClick={() => handleJump(entry.start)}
                      className="w-full text-left px-3 py-2 rounded-lg bg-resolve-bg hover:bg-resolve-surfaceLight text-sm transition-colors"
                    >
                      <span className="font-mono text-resolve-textMuted">
                        {formatTime(entry.start)} - {formatTime(entry.end)}
                      </span>
                      <span className="ml-2 text-resolve-text">Camera {entry.track_index}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <ConfirmApply onConfirm={handleApply} loading={applying} disabled={schedule.length === 0}>
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

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

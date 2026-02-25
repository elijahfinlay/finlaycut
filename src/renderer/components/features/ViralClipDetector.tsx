import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface ViralClip {
  start_seconds: number
  end_seconds: number
  duration_seconds: number
  score: number
  reason: string
  suggested_title: string
  selected: boolean
}

export function ViralClipDetector() {
  const [clips, setClips] = useState<ViralClip[]>([])
  const [detecting, setDetecting] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('viral')

  const handleDetect = async () => {
    setDetecting(true)
    setClips([])
    setResult(null)
    try {
      const data = await apiFetch('/api/viral/detect', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setClips(data.clips || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setDetecting(false)
    }
  }

  const toggleClip = (index: number) => {
    setClips((prev) =>
      prev.map((c, i) => (i === index ? { ...c, selected: !c.selected } : c))
    )
  }

  const handleCreate = async (clip: ViralClip) => {
    try {
      const data = await apiFetch('/api/viral/create', {
        method: 'POST',
        body: JSON.stringify(clip)
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
        <h2 className="feature-title">Viral Clip Detection</h2>
        <p className="feature-description">
          AI scores segments for viral potential. One-click creation of vertical
          (9:16) Shorts/Reels/TikToks from your long-form content.
        </p>
      </div>

      <Button onClick={handleDetect} loading={detecting} size="lg">
        Find Viral Clips
      </Button>

      {detecting && <ProgressBar progress={progress} message={message} />}

      {clips.length > 0 && (
        <div className="space-y-3">
          {clips.map((clip, i) => (
            <Card
              key={i}
              className={clip.selected ? 'border-resolve-accent/30' : ''}
              onClick={() => toggleClip(i)}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex items-center gap-1">
                      {Array.from({ length: 10 }, (_, j) => (
                        <div
                          key={j}
                          className={`w-2 h-4 rounded-sm ${
                            j < clip.score ? 'bg-resolve-accent' : 'bg-resolve-border'
                          }`}
                        />
                      ))}
                    </div>
                    <span className="text-sm font-bold text-resolve-accent">
                      {clip.score}/10
                    </span>
                  </div>
                  <p className="text-sm font-medium text-resolve-text mb-1">
                    {clip.suggested_title || 'Untitled Clip'}
                  </p>
                  <p className="text-xs text-resolve-textMuted mb-2">{clip.reason}</p>
                  <div className="flex items-center gap-3 text-xs text-resolve-textDim">
                    <span className="font-mono">
                      {formatTime(clip.start_seconds)} - {formatTime(clip.end_seconds)}
                    </span>
                    <span>{Math.round(clip.duration_seconds)}s</span>
                  </div>
                </div>
                <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); handleCreate(clip) }}>
                  Create 9:16
                </Button>
              </div>
            </Card>
          ))}
        </div>
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

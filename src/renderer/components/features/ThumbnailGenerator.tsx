import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'

import { Slider } from '../common/Slider'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch, getBaseUrl } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

export function ThumbnailGenerator() {
  const [count, setCount] = useState(10)
  const [thumbnails, setThumbnails] = useState<any[]>([])
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('thumbnail')

  const handleGenerate = async () => {
    setGenerating(true)
    setThumbnails([])
    setResult(null)
    try {
      const data = await apiFetch(`/api/thumbnail/generate?count=${count}`, {
        method: 'POST'
      })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setThumbnails(data.thumbnails || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setGenerating(false)
    }
  }

  const handleJumpToTime = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds })
      })
    } catch {
      // Ignore jump errors
    }
  }

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">AI Thumbnail Generator</h2>
        <p className="feature-description">
          Extracts the most visually compelling frames from your video. AI scores them
          for thumbnail potential.
        </p>
      </div>

      <Card>
        <Slider
          label="Number of Candidates"
          value={count}
          onChange={setCount}
          min={5}
          max={30}
          step={5}
        />
      </Card>

      <Button onClick={handleGenerate} loading={generating} size="lg">
        Generate Thumbnails
      </Button>

      {generating && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {thumbnails.length > 0 && (
        <Card>
          <h3 className="text-sm font-semibold text-resolve-text mb-3">
            Thumbnail Candidates ({thumbnails.length})
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {thumbnails.map((thumb, i) => (
              <div key={i} className="space-y-1">
                <button
                  onClick={() => handleJumpToTime(thumb.time_seconds)}
                  className="w-full aspect-video bg-resolve-bg rounded-lg overflow-hidden border border-resolve-border hover:border-resolve-accent/50 transition-colors cursor-pointer"
                >
                  {thumb.path ? (
                    <img
                      src={`${getBaseUrl()}/api/thumbnail/serve/${encodeURIComponent(thumb.path.split('/').pop() || '')}`}
                      alt={`Frame at ${formatTime(thumb.time_seconds)}`}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-resolve-textMuted text-xs">
                      Frame @ {formatTime(thumb.time_seconds)}
                    </div>
                  )}
                </button>
                <p className="text-xs text-resolve-textMuted text-center font-mono">
                  {formatTime(thumb.time_seconds)}
                </p>
              </div>
            ))}
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

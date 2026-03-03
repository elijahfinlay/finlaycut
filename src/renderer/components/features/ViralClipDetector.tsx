import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

interface ViralClip {
  start_seconds: number
  end_seconds: number
  duration_seconds: number
  score: number
  reason: string
  suggested_title: string
  selected: boolean
}

const OP_NAME = 'viral'

export function ViralClipDetector() {
  const {
    viralClips: storedClips,
    setViralClips: storeClips,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [clips, setClips] = useState<ViralClip[]>(storedClips)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Persist across tab switches
  useEffect(() => {
    storeClips(clips)
  }, [clips, storeClips])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.clips) setClips(op.result.clips)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleDetect = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setClips([])
    setResult(null)
    try {
      const data = await apiFetch('/api/viral/detect', { method: 'POST' })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setViralClips(data.clips || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setClips(data.clips || [])
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setResult({ error: err.message })
      toast.error(err.message)
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
      if (data.success) toast.success(data.message || 'Viral clip created')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    }
  }

  const handleJumpToClip = async (seconds: number) => {
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
        <h2 className="feature-title">Viral Clip Detection</h2>
        <p className="feature-description">
          AI scores segments for viral potential. One-click creation of vertical
          (9:16) Shorts/Reels/TikToks from your long-form content.
        </p>
      </div>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Detecting...
        </Button>
      ) : (
        <Button onClick={handleDetect} size="lg">
          Find Viral Clips
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {clips.length > 0 && (
        <div className="space-y-3">
          {clips.map((clip, i) => (
            <Card
              key={i}
              className={clip.selected ? 'border-resolve-accent/30' : ''}
              onClick={() => {
                toggleClip(i)
                handleJumpToClip(clip.start_seconds)
              }}
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

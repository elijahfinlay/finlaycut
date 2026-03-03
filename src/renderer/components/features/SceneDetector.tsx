import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { Slider } from '../common/Slider'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useOperationStore } from '../../stores/operationStore'

interface Scene {
  index: number
  start_seconds: number
  end_seconds: number
  duration: number
}

const OP_NAME = 'scenes'

export function SceneDetector() {
  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [threshold, setThreshold] = useState(27)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.scenes) setScenes(op.result.scenes)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleDetect = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setScenes([])
    setResult(null)
    try {
      const data = await apiFetch(
        `/api/scenes/detect?threshold=${threshold}`,
        { method: 'POST' }
      )
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setScenes(data.scenes || [])
        toast.success(`Detected ${data.count} scenes`)
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setResult({ error: err.message })
      toast.error(err.message)
    }
  }

  const handleJumpToScene = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds }),
      })
    } catch {
      // Silently fail
    }
  }

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Scene Detection</h2>
        <p className="feature-description">
          Automatically detect visual scene changes in your video. Useful for
          identifying cuts, transitions, and content segments.
        </p>
      </div>

      <Card>
        <Slider
          label="Detection Sensitivity"
          value={threshold}
          onChange={setThreshold}
          min={5}
          max={80}
          step={1}
        />
        <p className="text-xs text-resolve-textMuted mt-2">
          Lower values detect more subtle changes. Default: 27
        </p>
      </Card>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Detecting...
        </Button>
      ) : (
        <Button onClick={handleDetect} size="lg">
          Detect Scenes
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {scenes.length > 0 && (
        <Card>
          <h3 className="text-sm font-semibold text-resolve-text mb-3">
            Scenes ({scenes.length})
          </h3>
          <div className="space-y-1 max-h-96 overflow-y-auto">
            {scenes.map((scene) => (
              <button
                key={scene.index}
                onClick={() => handleJumpToScene(scene.start_seconds)}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm hover:bg-resolve-surfaceLight transition-colors text-left"
              >
                <div className="flex items-center gap-3">
                  <span className="text-resolve-accent font-mono text-xs w-8 text-right">
                    {scene.index + 1}
                  </span>
                  <span className="text-resolve-text">
                    {formatTime(scene.start_seconds)} — {formatTime(scene.end_seconds)}
                  </span>
                </div>
                <span className="text-resolve-textMuted text-xs">
                  {scene.duration.toFixed(1)}s
                </span>
              </button>
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

import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { Slider } from '../common/Slider'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface Scene {
  index: number
  start_seconds: number
  end_seconds: number
  duration: number
}

export function SceneDetector() {
  const [threshold, setThreshold] = useState(27)
  const [detecting, setDetecting] = useState(false)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('scenes')

  const handleDetect = async () => {
    setDetecting(true)
    setScenes([])
    setResult(null)
    try {
      const data = await apiFetch(
        `/api/scenes/detect?threshold=${threshold}`,
        { method: 'POST' }
      )
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setScenes(data.scenes || [])
        toast.success(`Detected ${data.count} scenes`)
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setDetecting(false)
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

      <Button onClick={handleDetect} loading={detecting} size="lg">
        Detect Scenes
      </Button>

      {detecting && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

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

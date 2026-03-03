import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Slider } from '../common/Slider'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface ZoomPoint {
  time: number
  duration: number
  zoom: number
  reason: string
  confidence: number
  selected: boolean
}

export function DynamicZoom() {
  const [zoomAmount, setZoomAmount] = useState(115)
  const [zoomDuration, setZoomDuration] = useState(2.0)
  const [minGap, setMinGap] = useState(3.0)
  const [maxZooms, setMaxZooms] = useState(30)
  const [points, setPoints] = useState<ZoomPoint[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('zoom')

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setPoints([])
    setResult(null)
    try {
      const data = await apiFetch('/api/zoom/analyze', {
        method: 'POST',
        body: JSON.stringify({
          zoom_amount: zoomAmount / 100,
          zoom_duration_seconds: zoomDuration,
          min_gap_seconds: minGap,
          max_zooms: maxZooms
        })
      })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setPoints(data.zoom_points || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setAnalyzing(false)
    }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      const data = await apiFetch('/api/zoom/apply', {
        method: 'POST',
        body: JSON.stringify({ zoom_points: points })
      })
      setResult(data)
      if (data.success) toast.success(`Applied ${data.applied_count} zoom effects`)
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const togglePoint = (index: number) => {
    setPoints((prev) =>
      prev.map((p, i) => (i === index ? { ...p, selected: !p.selected } : p))
    )
    handleJumpToPoint(points[index]?.time ?? 0)
  }

  const handleJumpToPoint = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds })
      })
    } catch {
      // Ignore jump errors
    }
  }

  const selectedCount = points.filter((p) => p.selected).length

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Dynamic Zoom Cuts</h2>
        <p className="feature-description">
          Automatically add subtle zoom-ins at emphasis points to make talking-head
          videos more engaging. Uses transcript analysis and audio energy detection.
        </p>
      </div>

      <Card>
        <div className="space-y-5">
          <Slider
            label="Zoom Amount"
            value={zoomAmount}
            onChange={setZoomAmount}
            min={105}
            max={150}
            unit="%"
          />
          <Slider
            label="Zoom Duration"
            value={zoomDuration}
            onChange={setZoomDuration}
            min={0.5}
            max={5}
            step={0.5}
            unit="s"
          />
          <Slider
            label="Minimum Gap"
            value={minGap}
            onChange={setMinGap}
            min={1}
            max={10}
            step={0.5}
            unit="s"
          />
          <Slider
            label="Max Zoom Points"
            value={maxZooms}
            onChange={setMaxZooms}
            min={5}
            max={100}
            step={5}
          />
        </div>
      </Card>

      <Button onClick={handleAnalyze} loading={analyzing} size="lg">
        Analyze
      </Button>

      {analyzing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {points.length > 0 && (
        <Card>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-resolve-text">Zoom Points</h3>
              <span className="text-sm text-resolve-textDim">
                {selectedCount} of {points.length} selected
              </span>
            </div>

            <div className="max-h-64 overflow-y-auto space-y-1">
              {points.map((point, i) => (
                <button
                  key={i}
                  onClick={() => togglePoint(i)}
                  className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${
                    point.selected
                      ? 'bg-resolve-accent/10 text-resolve-accent'
                      : 'bg-resolve-surfaceLight text-resolve-textMuted'
                  }`}
                >
                  <span className="font-mono">{formatTime(point.time)}</span>
                  <span className="text-xs capitalize">{point.reason.replace('_', ' ')}</span>
                  <span className="text-xs">{Math.round(point.confidence * 100)}%</span>
                </button>
              ))}
            </div>

            <ConfirmApply onConfirm={handleApply} loading={applying} size="lg">
              Apply Zooms ({selectedCount})
            </ConfirmApply>
          </div>
        </Card>
      )}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
      {result?.success && (
        <Card className="border-resolve-success/30">
          <p className="text-sm text-resolve-success">
            Applied {result.applied_count} zoom effects
          </p>
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

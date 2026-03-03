import React, { useState, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Slider } from '../common/Slider'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { ConfirmApply } from '../common/TimelineGuard'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'

interface SilenceSegment {
  start: number
  end: number
  duration: number
  selected: boolean
}

export function SilenceRemoval() {
  const {
    silenceSegments: storedSegments,
    silenceSettings: storedSettings,
    setSilenceSegments: storeSegments,
    setSilenceSettings: storeSettings,
  } = useFeatureDataStore()

  const [threshold, setThreshold] = useState(storedSettings.threshold)
  const [minDuration, setMinDuration] = useState(storedSettings.minDuration)
  const [padding, setPadding] = useState(storedSettings.padding)
  const [segments, setSegments] = useState<SilenceSegment[]>(storedSegments)
  const [analyzing, setAnalyzing] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('silence')
  const abortRef = useRef<AbortController | null>(null)

  // Persist across tab switches
  useEffect(() => {
    storeSegments(segments)
  }, [segments, storeSegments])

  useEffect(() => {
    storeSettings({ threshold, minDuration, padding })
  }, [threshold, minDuration, padding, storeSettings])

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setSegments([])
    setResult(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const data = await apiFetch('/api/silence/analyze', {
        method: 'POST',
        body: JSON.stringify({
          threshold_db: threshold,
          min_duration_ms: minDuration,
          padding_ms: padding
        }),
        signal: controller.signal
      })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setSegments(data.segments || [])
        setResult(data)
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        setResult({ error: 'Analysis cancelled' })
        toast.error('Analysis cancelled')
      } else {
        setResult({ error: err.message })
        toast.error(err.message)
      }
    } finally {
      setAnalyzing(false)
      abortRef.current = null
    }
  }

  const handleCancel = () => {
    if (abortRef.current) {
      abortRef.current.abort()
    }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      const data = await apiFetch('/api/silence/apply', {
        method: 'POST',
        body: JSON.stringify({
          segments: segments.filter((s) => s.selected),
          padding_ms: padding
        })
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Silence removed successfully')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleJumpToSegment = async (seg: SilenceSegment) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds: seg.start })
      })
    } catch {
      // Silently fail
    }
  }

  const toggleSegment = (index: number) => {
    setSegments((prev) =>
      prev.map((s, i) => (i === index ? { ...s, selected: !s.selected } : s))
    )
    handleJumpToSegment(segments[index])
  }

  const selectedCount = segments.filter((s) => s.selected).length
  const totalSilence = segments
    .filter((s) => s.selected)
    .reduce((acc, s) => acc + s.duration, 0)

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Silence Removal</h2>
        <p className="feature-description">
          Detect and remove silent pauses from your footage. Adjust the settings below
          and click Analyze to preview which segments will be removed.
        </p>
      </div>

      {/* Settings */}
      <Card>
        <div className="space-y-5">
          <Slider
            label="Silence Threshold"
            value={threshold}
            onChange={setThreshold}
            min={-60}
            max={-20}
            unit=" dB"
          />
          <Slider
            label="Minimum Duration"
            value={minDuration}
            onChange={setMinDuration}
            min={100}
            max={2000}
            step={50}
            unit=" ms"
          />
          <Slider
            label="Padding"
            value={padding}
            onChange={setPadding}
            min={0}
            max={500}
            step={25}
            unit=" ms"
          />
        </div>
      </Card>

      {/* Analyze Button */}
      {analyzing ? (
        <Button onClick={handleCancel} variant="ghost" size="lg">
          Cancel
        </Button>
      ) : (
        <Button onClick={handleAnalyze} loading={analyzing} size="lg">
          Analyze
        </Button>
      )}

      {/* Progress */}
      {analyzing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {/* Results */}
      {segments.length > 0 && (
        <Card>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-resolve-text">
                Detected Silences
              </h3>
              <span className="text-sm text-resolve-textDim">
                {selectedCount} of {segments.length} selected ({totalSilence.toFixed(1)}s)
              </span>
            </div>

            <div className="max-h-64 overflow-y-auto space-y-1">
              {segments.map((seg, i) => (
                <button
                  key={i}
                  onClick={() => toggleSegment(i)}
                  className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${
                    seg.selected
                      ? 'bg-resolve-error/10 text-resolve-error'
                      : 'bg-resolve-surfaceLight text-resolve-textMuted'
                  }`}
                >
                  <span className="font-mono">
                    {formatTime(seg.start)} - {formatTime(seg.end)}
                  </span>
                  <span className="font-mono">{seg.duration.toFixed(1)}s</span>
                </button>
              ))}
            </div>

            <ConfirmApply
              onConfirm={handleApply}
              loading={applying}
              description={`This will remove ${selectedCount} silent segments (${totalSilence.toFixed(1)}s total).`}
            >
              Apply ({selectedCount} cuts)
            </ConfirmApply>
          </div>
        </Card>
      )}

      {/* Result message */}
      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
      {result?.success && (
        <Card className="border-resolve-success/30">
          <p className="text-sm text-resolve-success">{result.message}</p>
        </Card>
      )}
    </div>
  )
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 10)
  return `${m}:${s.toString().padStart(2, '0')}.${ms}`
}

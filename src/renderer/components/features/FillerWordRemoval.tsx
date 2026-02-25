import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface FillerDetection {
  word: string
  start: number
  end: number
  context: string
  selected: boolean
}

export function FillerWordRemoval() {
  const [detections, setDetections] = useState<FillerDetection[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('filler_words')

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setDetections([])
    setResult(null)
    try {
      const data = await apiFetch('/api/filler-words/analyze', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setDetections(data.detections || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setAnalyzing(false)
    }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      const data = await apiFetch('/api/filler-words/apply', {
        method: 'POST',
        body: JSON.stringify(detections.filter((d) => d.selected))
      })
      setResult(data)
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setApplying(false)
    }
  }

  const toggleDetection = (index: number) => {
    setDetections((prev) =>
      prev.map((d, i) => (i === index ? { ...d, selected: !d.selected } : d))
    )
  }

  const selectedCount = detections.filter((d) => d.selected).length

  return (
    <div className="feature-panel">
      <TimelineBanner />
      <div>
        <h2 className="feature-title">Filler Word Removal</h2>
        <p className="feature-description">
          Detect and remove filler words like "um", "uh", "like", "you know" from your
          footage. Review each detection before applying.
        </p>
      </div>

      <Button onClick={handleAnalyze} loading={analyzing} size="lg">
        Analyze
      </Button>

      {analyzing && <ProgressBar progress={progress} message={message} />}

      {detections.length > 0 && (
        <Card>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-resolve-text">
                Detected Filler Words
              </h3>
              <span className="text-sm text-resolve-textDim">
                {selectedCount} of {detections.length} selected
              </span>
            </div>

            <div className="max-h-80 overflow-y-auto space-y-2">
              {detections.map((det, i) => (
                <button
                  key={i}
                  onClick={() => toggleDetection(i)}
                  className={`w-full text-left px-4 py-3 rounded-lg text-sm transition-colors ${
                    det.selected
                      ? 'bg-resolve-warning/10 border border-resolve-warning/20'
                      : 'bg-resolve-surfaceLight border border-transparent'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`font-semibold ${det.selected ? 'text-resolve-warning' : 'text-resolve-textMuted'}`}>
                      "{det.word}"
                    </span>
                    <span className="font-mono text-xs text-resolve-textMuted">
                      {formatTime(det.start)}
                    </span>
                  </div>
                  <p className="text-xs text-resolve-textDim leading-relaxed">
                    ...{det.context}...
                  </p>
                </button>
              ))}
            </div>

            <ConfirmApply onConfirm={handleApply} loading={applying} size="lg">
              Remove Selected ({selectedCount})
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
          <p className="text-sm text-resolve-success">{result.message}</p>
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

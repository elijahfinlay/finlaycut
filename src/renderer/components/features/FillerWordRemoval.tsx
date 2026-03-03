import React, { useState, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

interface FillerDetection {
  word: string
  start: number
  end: number
  context: string
  selected: boolean
}

const OP_NAME = 'filler_words'

export function FillerWordRemoval() {
  const {
    fillerDetections: storedDetections,
    setFillerDetections: storeDetections,
    transcriptWords,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [detections, setDetections] = useState<FillerDetection[]>(storedDetections)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)
  const abortRef = useRef<AbortController | null>(null)

  const hasTranscript = transcriptWords.length > 0

  // Persist detections across tab switches
  useEffect(() => {
    storeDetections(detections)
  }, [detections, storeDetections])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.detections) {
        setDetections(op.result.detections)
      }
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleAnalyze = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setDetections([])
    setResult(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const body: any = {}
      if (hasTranscript) {
        body.transcript = {
          words: transcriptWords.map((w) => ({
            word: w.word,
            start: w.start,
            end: w.end,
          })),
        }
      }

      const data = await apiFetch('/api/filler-words/analyze', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setFillerDetections(data.detections || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setDetections(data.detections || [])
        if (data.detections?.length === 0) {
          toast.info('No filler words detected')
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        useOperationStore.getState().failOperation(OP_NAME, 'Analysis cancelled')
        setResult({ error: 'Analysis cancelled' })
        toast.error('Analysis cancelled')
      } else {
        useOperationStore.getState().failOperation(OP_NAME, err.message)
        setResult({ error: err.message })
        toast.error(err.message)
      }
    } finally {
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
      const data = await apiFetch('/api/filler-words/apply', {
        method: 'POST',
        body: JSON.stringify(detections.filter((d) => d.selected))
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Filler words removed successfully')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleJumpToFiller = async (detection: FillerDetection) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds: detection.start })
      })
    } catch {
      // Silently fail
    }
  }

  const toggleDetection = (index: number) => {
    setDetections((prev) =>
      prev.map((d, i) => (i === index ? { ...d, selected: !d.selected } : d))
    )
  }

  const selectAll = () => {
    const allSelected = detections.every((d) => d.selected)
    setDetections((prev) => prev.map((d) => ({ ...d, selected: !allSelected })))
  }

  const selectedCount = detections.filter((d) => d.selected).length

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Filler Word Removal</h2>
        <p className="feature-description">
          Detect and remove filler words like "um", "uh", "like", "you know" from your timeline.
        </p>
      </div>

      <Card>
        {isRunning ? (
          abortRef.current ? (
            <Button onClick={handleCancel} variant="ghost" size="lg">
              Cancel
            </Button>
          ) : (
            <Button disabled size="lg" variant="ghost">
              Analysis in progress...
            </Button>
          )
        ) : (
          <div className="space-y-2">
            <Button onClick={handleAnalyze} size="lg">
              {detections.length > 0 ? 'Re-Analyze' : 'Analyze'}
            </Button>
            {hasTranscript && (
              <p className="text-xs text-resolve-textMuted">
                Using existing transcript from Transcript Editor — no re-transcription needed.
              </p>
            )}
            {!hasTranscript && (
              <p className="text-xs text-resolve-textMuted">
                No transcript available. This will transcribe first, then detect filler words.
                Tip: transcribe in the Transcript Editor first for faster analysis.
              </p>
            )}
          </div>
        )}
      </Card>

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {detections.length > 0 && (
        <Card>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-resolve-textDim">
                {selectedCount} of {detections.length} selected
              </span>
              <Button variant="ghost" size="sm" onClick={selectAll}>
                {detections.every((d) => d.selected) ? 'Deselect All' : 'Select All'}
              </Button>
            </div>

            <div className="max-h-80 overflow-y-auto space-y-1.5">
              {detections.map((det, i) => (
                <button
                  key={i}
                  onClick={() => {
                    toggleDetection(i)
                    handleJumpToFiller(det)
                  }}
                  className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer ${
                    det.selected
                      ? 'bg-resolve-warning/10 border border-resolve-warning/20'
                      : 'bg-resolve-surfaceLight border border-transparent'
                  }`}
                >
                  <div className="flex items-center justify-between mb-0.5">
                    <span
                      className={`font-semibold ${det.selected ? 'text-resolve-warning' : 'text-resolve-textMuted'}`}
                    >
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

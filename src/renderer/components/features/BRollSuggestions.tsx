import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

const OP_NAME = 'broll'

export function BRollSuggestions() {
  const {
    brollSuggestions: storedSuggestions,
    setBrollSuggestions: storeSuggestions,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [suggestions, setSuggestions] = useState<any[]>(storedSuggestions)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Persist across tab switches
  useEffect(() => {
    storeSuggestions(suggestions)
  }, [suggestions, storeSuggestions])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.suggestions) setSuggestions(op.result.suggestions)
      if (!op.result.has_pexels_key) {
        setResult({ warning: 'No Pexels API key configured. Add one in Settings for stock footage search.' })
      }
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleAnalyze = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setSuggestions([])
    setResult(null)
    try {
      const data = await apiFetch('/api/broll/suggest', { method: 'POST' })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setBrollSuggestions(data.suggestions || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setSuggestions(data.suggestions || [])
        if (!data.has_pexels_key) {
          setResult({ warning: 'No Pexels API key configured. Add one in Settings for stock footage search.' })
          toast.warning('No Pexels API key configured. Add one in Settings for stock footage search.')
        }
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setResult({ error: err.message })
      toast.error(err.message)
    }
  }

  const toggleSuggestion = (index: number) => {
    setSuggestions((prev) =>
      prev.map((s, i) => (i === index ? { ...s, selected: !s.selected } : s))
    )
  }

  const handleInsert = async () => {
    const selected = suggestions.filter((s) => s.selected)
    try {
      const data = await apiFetch('/api/broll/insert', {
        method: 'POST',
        body: JSON.stringify(selected)
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'B-Roll inserted')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    }
  }

  const handleJumpToSuggestion = async (seconds: number) => {
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
        <h2 className="feature-title">AI B-Roll Suggestions</h2>
        <p className="feature-description">
          Analyzes your transcript and suggests relevant stock footage from Pexels.
          Pick the clips you like and insert them directly onto your timeline.
        </p>
      </div>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Analyzing...
        </Button>
      ) : (
        <Button onClick={handleAnalyze} size="lg">
          Suggest B-Roll
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {suggestions.length > 0 && (
        <div className="space-y-3">
          {suggestions.map((sug, i) => (
            <Card
              key={i}
              className={sug.selected ? 'border-resolve-accent/30' : ''}
              onClick={() => {
                toggleSuggestion(i)
                handleJumpToSuggestion(sug.time_seconds)
              }}
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-resolve-textMuted">
                    {formatTime(sug.time_seconds)}
                  </span>
                  <span className="text-xs px-2 py-0.5 bg-resolve-accent/10 text-resolve-accent rounded-full">
                    {sug.search_term}
                  </span>
                </div>
                <p className="text-sm text-resolve-textDim">{sug.context}</p>
                <p className="text-xs text-resolve-textMuted">{sug.reason}</p>

                {sug.stock_results?.length > 0 && (
                  <div className="flex gap-2 mt-2">
                    {sug.stock_results.map((vid: any, vi: number) => (
                      <div
                        key={vi}
                        className="w-24 h-16 bg-resolve-bg rounded overflow-hidden"
                      >
                        {vid.preview_url && (
                          <img
                            src={vid.preview_url}
                            alt=""
                            className="w-full h-full object-cover"
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          ))}

          <ConfirmApply
            onConfirm={handleInsert}
            size="lg"
            disabled={!suggestions.some((s) => s.selected)}
          >
            Insert Selected B-Roll
          </ConfirmApply>
        </div>
      )}

      {result?.warning && (
        <Card className="border-resolve-warning/30">
          <p className="text-sm text-resolve-warning">{result.warning}</p>
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

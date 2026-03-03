import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

export function BRollSuggestions() {
  const [suggestions, setSuggestions] = useState<any[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('broll')

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setSuggestions([])
    setResult(null)
    try {
      const data = await apiFetch('/api/broll/suggest', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setSuggestions(data.suggestions || [])
        if (!data.has_pexels_key) {
          setResult({ warning: 'No Pexels API key configured. Add one in Settings for stock footage search.' })
          toast.warning('No Pexels API key configured. Add one in Settings for stock footage search.')
        }
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setAnalyzing(false)
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

      <Button onClick={handleAnalyze} loading={analyzing} size="lg">
        Suggest B-Roll
      </Button>

      {analyzing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

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

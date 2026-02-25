import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

export function RepetitionRemoval() {
  const [groups, setGroups] = useState<any[]>([])
  const [detecting, setDetecting] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('repetition')

  const handleDetect = async () => {
    setDetecting(true)
    setGroups([])
    setResult(null)
    try {
      const data = await apiFetch('/api/repetition/detect', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setGroups(data.groups || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setDetecting(false)
    }
  }

  const selectBest = (groupIndex: number, takeIndex: number) => {
    setGroups((prev) =>
      prev.map((g, i) =>
        i === groupIndex ? { ...g, best_index: takeIndex } : g
      )
    )
  }

  const handleApply = async () => {
    try {
      const data = await apiFetch('/api/repetition/apply', {
        method: 'POST',
        body: JSON.stringify(groups)
      })
      setResult(data)
    } catch (err: any) {
      setResult({ error: err.message })
    }
  }

  return (
    <div className="feature-panel">
      <TimelineBanner />
      <div>
        <h2 className="feature-title">Take/Repetition Removal</h2>
        <p className="feature-description">
          Detects when someone re-does a line. Scores each take for quality and
          recommends the best one. You choose which take to keep.
        </p>
      </div>

      <Button onClick={handleDetect} loading={detecting} size="lg">
        Detect Repetitions
      </Button>

      {detecting && <ProgressBar progress={progress} message={message} />}

      {groups.length > 0 && (
        <div className="space-y-3">
          {groups.map((group, gi) => (
            <Card key={gi}>
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-resolve-text">
                  Repeated Section ({group.count} takes)
                </h4>
                {group.takes.map((take: any, ti: number) => (
                  <button
                    key={ti}
                    onClick={() => selectBest(gi, ti)}
                    className={`w-full text-left p-3 rounded-lg text-sm transition-colors ${
                      group.best_index === ti
                        ? 'bg-resolve-success/10 border border-resolve-success/30'
                        : 'bg-resolve-bg hover:bg-resolve-surfaceLight'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-resolve-textMuted">
                        Take {ti + 1} - {formatTime(take.start)}
                      </span>
                      {group.best_index === ti && (
                        <span className="text-xs text-resolve-success font-medium">Best</span>
                      )}
                    </div>
                    <p className="text-resolve-textDim">{take.text}</p>
                  </button>
                ))}
              </div>
            </Card>
          ))}

          <ConfirmApply onConfirm={handleApply} size="lg">
            Keep Best Takes
          </ConfirmApply>
        </div>
      )}

      {groups.length === 0 && !detecting && result && !result.error && (
        <Card>
          <p className="text-sm text-resolve-textDim">No repetitions detected.</p>
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

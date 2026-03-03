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

const OP_NAME = 'repetition'

export function RepetitionRemoval() {
  const {
    repetitionGroups: storedGroups,
    setRepetitionGroups: storeGroups,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [groups, setGroups] = useState<any[]>(storedGroups)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Persist across tab switches
  useEffect(() => {
    storeGroups(groups)
  }, [groups, storeGroups])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.groups) setGroups(op.result.groups)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleDetect = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setGroups([])
    setResult(null)
    try {
      const data = await apiFetch('/api/repetition/detect', { method: 'POST' })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setRepetitionGroups(data.groups || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setGroups(data.groups || [])
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setResult({ error: err.message })
      toast.error(err.message)
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
    setApplying(true)
    try {
      const data = await apiFetch('/api/repetition/apply', {
        method: 'POST',
        body: JSON.stringify(groups)
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Best takes applied')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleJumpToTake = async (seconds: number) => {
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
        <h2 className="feature-title">Take/Repetition Removal</h2>
        <p className="feature-description">
          Detects when someone re-does a line. Scores each take for quality and
          recommends the best one. You choose which take to keep.
        </p>
      </div>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Detecting...
        </Button>
      ) : (
        <Button onClick={handleDetect} size="lg">
          Detect Repetitions
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

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
                    onClick={() => {
                      selectBest(gi, ti)
                      handleJumpToTake(take.start)
                    }}
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

          <ConfirmApply onConfirm={handleApply} loading={applying} size="lg">
            Keep Best Takes
          </ConfirmApply>
        </div>
      )}

      {groups.length === 0 && !isRunning && result && !result.error && (
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

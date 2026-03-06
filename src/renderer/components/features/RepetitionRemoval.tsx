import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { Modal } from '../common/Modal'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'
import { isAnalysisMarker, normalizeSpokenToken } from './transcript/types'

const OP_NAME = 'repetition'

interface TakeScores {
  completion: number
  confidence: number
  fluency: number
  combined: number
}

interface Take {
  text: string
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  word_start_index?: number
  word_end_index?: number
  scores: TakeScores
}

interface RepetitionGroup {
  takes: Take[]
  count: number
  best_index: number
  matched_text: string
  match_length?: number
}

function scoreColor(score: number): string {
  if (score >= 0.7) return 'text-resolve-success'
  if (score >= 0.4) return 'text-yellow-400'
  return 'text-resolve-error'
}

function scoreBadgeBg(score: number): string {
  if (score >= 0.7) return 'bg-resolve-success/15 border-resolve-success/30'
  if (score >= 0.4) return 'bg-yellow-400/15 border-yellow-400/30'
  return 'bg-resolve-error/15 border-resolve-error/30'
}

export function RepetitionRemoval() {
  const {
    repetitionGroups: storedGroups,
    setRepetitionGroups: storeGroups,
    transcriptWords,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [groups, setGroups] = useState<RepetitionGroup[]>(storedGroups as RepetitionGroup[])
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [undoEntryId, setUndoEntryId] = useState<string | null>(null)
  const [showUndoConfirm, setShowUndoConfirm] = useState(false)
  const [undoing, setUndoing] = useState(false)
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
      // If transcript words exist, pass them to skip re-transcription
      const spokenWords = transcriptWords.filter((w) => !isAnalysisMarker(w))
      const body: Record<string, any> = {}
      if (spokenWords.length > 0) {
        body.transcript = {
          words: spokenWords.map((w) => ({
            word: w.word,
            start: w.start,
            end: w.end,
            start_frame: w.start_frame,
            end_frame: w.end_frame,
            source_start_frame: w.source_start_frame,
            source_end_frame: w.source_end_frame,
            probability: w.probability,
          })),
        }
      }

      const data = await apiFetch('/api/repetition/detect', {
        method: 'POST',
        body: JSON.stringify(body),
      })
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
      const previousGroups = groups
      const data = await apiFetch('/api/repetition/apply', {
        method: 'POST',
        body: JSON.stringify(groups)
      })
      setResult(data)
      if (data.success) {
        toast.success(data.message || 'Best takes applied')
        setUndoEntryId(data.undo_entry_id || null)
        setGroups([])
        useFeatureDataStore.getState().setRepetitionGroups([])
        previousGroupsRef.current = previousGroups
      }
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

  const previousGroupsRef = React.useRef<RepetitionGroup[]>([])

  const handleUndo = async () => {
    if (!undoEntryId) return
    setUndoing(true)
    try {
      const data = await apiFetch(`/api/undo/rollback/${undoEntryId}`, { method: 'POST' })
      if (data.success) {
        toast.success(data.message || 'Rolled back successfully')
        setResult(null)
        setUndoEntryId(null)
        setGroups(previousGroupsRef.current)
        useFeatureDataStore.getState().setRepetitionGroups(previousGroupsRef.current)
      } else if (data.error) {
        toast.error(data.error)
      }
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setUndoing(false)
      setShowUndoConfirm(false)
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
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-resolve-text">
                    Repeated Section ({group.count} takes)
                  </h4>
                  {group.matched_text && (
                    <span className="text-xs text-resolve-textMuted italic truncate ml-2 max-w-[200px]">
                      "{group.matched_text}"
                    </span>
                  )}
                </div>
                {group.takes.map((take, ti) => {
                  const isRecommended = group.best_index === ti
                  const pct = Math.round((take.scores?.combined ?? 0) * 100)
                  return (
                    <button
                      key={ti}
                      onClick={() => {
                        selectBest(gi, ti)
                        handleJumpToTake(take.start)
                      }}
                      className={`w-full text-left p-3 rounded-lg text-sm transition-colors ${
                        isRecommended
                          ? 'bg-resolve-success/10 border border-resolve-success/30'
                          : 'bg-resolve-bg hover:bg-resolve-surfaceLight'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-resolve-textMuted">
                          Take {ti + 1} - {formatTime(take.start)}
                        </span>
                        <div className="flex items-center gap-2">
                          {take.scores && (
                            <span className={`text-xs font-mono px-1.5 py-0.5 rounded border ${scoreBadgeBg(take.scores.combined)} ${scoreColor(take.scores.combined)}`}>
                              {pct}%
                            </span>
                          )}
                          {isRecommended && (
                            <span className="text-xs text-resolve-success font-medium">Recommended</span>
                          )}
                        </div>
                      </div>
                      <TakeText text={take.text} matchedText={group.matched_text} />
                      {take.scores && (
                        <div className="flex gap-3 mt-1.5">
                          <span className="text-xs text-resolve-textMuted">
                            Completeness <span className={scoreColor(take.scores.completion)}>{Math.round(take.scores.completion * 100)}%</span>
                          </span>
                          <span className="text-xs text-resolve-textMuted">
                            Clarity <span className={scoreColor(take.scores.confidence)}>{Math.round(take.scores.confidence * 100)}%</span>
                          </span>
                          <span className="text-xs text-resolve-textMuted">
                            Fluency <span className={scoreColor(take.scores.fluency)}>{Math.round(take.scores.fluency * 100)}%</span>
                          </span>
                        </div>
                      )}
                    </button>
                  )
                })}
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

      {result?.success && (
        <Card className="border-resolve-success/30">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-resolve-success">{result.message}</p>
            {undoEntryId && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowUndoConfirm(true)}
                disabled={undoing}
              >
                Undo
              </Button>
            )}
          </div>
        </Card>
      )}

      <Modal open={showUndoConfirm} onClose={() => setShowUndoConfirm(false)} title="Undo Repetition Removal">
        <p className="text-sm text-resolve-textDim mb-4">
          This will roll back to the backup timeline created before duplicate takes were removed. Continue?
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShowUndoConfirm(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleUndo} disabled={undoing}>
            {undoing ? 'Rolling back...' : 'Confirm Undo'}
          </Button>
        </div>
      </Modal>
    </div>
  )
}

/** Renders take text with matched portion dimmed and continuation bright. */
function TakeText({ text, matchedText }: { text: string; matchedText?: string }) {
  if (!matchedText) {
    return <p className="text-resolve-text">{text}</p>
  }

  const textTokens = text.trim().split(/\s+/).filter(Boolean)
  const matchTokens = matchedText.trim().split(/\s+/).filter(Boolean)
  const startIndex = findTokenSubsequence(textTokens, matchTokens)

  if (startIndex === -1 || matchTokens.length === 0) {
    return <p className="text-resolve-text">{text}</p>
  }

  const before = textTokens.slice(0, startIndex).join(' ')
  const matched = textTokens.slice(startIndex, startIndex + matchTokens.length).join(' ')
  const after = textTokens.slice(startIndex + matchTokens.length).join(' ')

  return (
    <p className="leading-relaxed">
      {before && <span className="text-resolve-text">{before} </span>}
      <span className="text-resolve-textMuted">{matched}</span>
      {after && <span className="text-resolve-text font-medium"> {after}</span>}
    </p>
  )
}

function findTokenSubsequence(textTokens: string[], matchTokens: string[]): number {
  if (matchTokens.length === 0 || textTokens.length < matchTokens.length) {
    return -1
  }

  const normalizedText = textTokens.map((token) => normalizeSpokenToken(token))
  const normalizedMatch = matchTokens.map((token) => normalizeSpokenToken(token))

  for (let start = 0; start <= normalizedText.length - normalizedMatch.length; start += 1) {
    let matched = true
    for (let offset = 0; offset < normalizedMatch.length; offset += 1) {
      if (normalizedText[start + offset] !== normalizedMatch[offset]) {
        matched = false
        break
      }
    }
    if (matched) {
      return start
    }
  }

  return -1
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

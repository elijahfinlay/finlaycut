import React, { useEffect, useRef, useState } from 'react'
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

interface DuplicateClipInstance {
  clip_name: string
  track_index: number
  timeline_item_unique_id?: string | null
  timeline_start_frame: number
  timeline_end_frame: number
  timeline_start_seconds: number
  timeline_end_seconds: number
  source_start_frame: number
  source_end_frame: number
}

interface DuplicateClipGroup {
  source_key: string
  source_label: string
  source_start_frame: number
  source_end_frame: number
  instance_count: number
  instances: DuplicateClipInstance[]
}

interface DuplicateClipScanResult {
  success?: boolean
  error?: string
  warning?: string
  message?: string
  duplicate_group_count?: number
  duplicate_instance_count?: number
  timeline_marker_count?: number
  clip_marker_count?: number
  cleared_count?: number
  skipped_untrackable_count?: number
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
    duplicateClipGroups: storedDuplicateGroups,
    setDuplicateClipGroups: storeDuplicateGroups,
    transcriptWords,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [groups, setGroups] = useState<RepetitionGroup[]>(storedGroups as RepetitionGroup[])
  const [duplicateGroups, setDuplicateGroups] = useState<DuplicateClipGroup[]>(
    storedDuplicateGroups as DuplicateClipGroup[]
  )
  const [applying, setApplying] = useState(false)
  const [duplicateScanning, setDuplicateScanning] = useState(false)
  const [retakeResult, setRetakeResult] = useState<any>(null)
  const [duplicateResult, setDuplicateResult] = useState<DuplicateClipScanResult | null>(null)
  const [undoEntryId, setUndoEntryId] = useState<string | null>(null)
  const [showUndoConfirm, setShowUndoConfirm] = useState(false)
  const [undoing, setUndoing] = useState(false)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)
  const previousGroupsRef = useRef<RepetitionGroup[]>([])

  useEffect(() => {
    storeGroups(groups)
  }, [groups, storeGroups])

  useEffect(() => {
    storeDuplicateGroups(duplicateGroups)
  }, [duplicateGroups, storeDuplicateGroups])

  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.groups) setGroups(op.result.groups)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setRetakeResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleDetect = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setGroups([])
    setRetakeResult(null)
    try {
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
        setRetakeResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setRepetitionGroups(data.groups || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setGroups(data.groups || [])
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setRetakeResult({ error: err.message })
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
      setRetakeResult(data)
      if (data.success) {
        toast.success(data.message || 'Best takes applied')
        setUndoEntryId(data.undo_entry_id || null)
        setGroups([])
        useFeatureDataStore.getState().setRepetitionGroups([])
        previousGroupsRef.current = previousGroups
      } else if (data.error) {
        toast.error(data.error)
      }
    } catch (err: any) {
      setRetakeResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleScanDuplicateClips = async () => {
    setDuplicateScanning(true)
    setDuplicateResult(null)
    try {
      const data = await apiFetch('/api/repetition/duplicate-clips/scan', {
        method: 'POST',
      })
      if (data.error) {
        setDuplicateResult({ error: data.error })
        toast.error(data.error)
        return
      }

      const nextGroups = (data.groups || []) as DuplicateClipGroup[]
      setDuplicateGroups(nextGroups)
      useFeatureDataStore.getState().setDuplicateClipGroups(nextGroups)
      setDuplicateResult(data)

      if (data.warning) {
        toast.info(data.warning)
      }
      if (data.duplicate_group_count > 0) {
        toast.success(data.message || 'Duplicate clips flagged')
      } else {
        toast.info(data.message || 'No duplicate clips detected')
      }
    } catch (err: any) {
      setDuplicateResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setDuplicateScanning(false)
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

  const handleUndo = async () => {
    if (!undoEntryId) return
    setUndoing(true)
    try {
      const data = await apiFetch(`/api/undo/rollback/${undoEntryId}`, { method: 'POST' })
      if (data.success) {
        toast.success(data.message || 'Rolled back successfully')
        setRetakeResult(null)
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
        <h2 className="feature-title">Takes & Duplicates</h2>
        <p className="feature-description">
          Review retakes to remove and scan the current timeline for exact duplicate clip usage.
          Duplicate scans add review markers to every matching instance.
        </p>
      </div>

      <Card>
        <div className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-resolve-text">Retake Detection</h3>
            <p className="text-sm text-resolve-textDim mt-1">
              Detects when someone re-does a line, scores each take, and recommends the best version to keep.
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
                <div key={gi} className="rounded-xl border border-resolve-border bg-resolve-bg p-4">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold text-resolve-text">
                        Repeated Section ({group.count} takes)
                      </h4>
                      {group.matched_text && (
                        <span className="text-xs text-resolve-textMuted italic truncate ml-2 max-w-[220px]">
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
                              : 'bg-resolve-surface hover:bg-resolve-surfaceLight'
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
                </div>
              ))}

              <ConfirmApply onConfirm={handleApply} loading={applying} size="lg">
                Keep Best Takes
              </ConfirmApply>
            </div>
          )}

          {groups.length === 0 && !isRunning && retakeResult && !retakeResult.error && (
            <div className="rounded-lg bg-resolve-bg p-4">
              <p className="text-sm text-resolve-textDim">No repetitions detected.</p>
            </div>
          )}

          {retakeResult?.error && (
            <div className="rounded-lg border border-resolve-error/30 bg-resolve-error/5 p-4">
              <p className="text-sm text-resolve-error">{retakeResult.error}</p>
            </div>
          )}

          {retakeResult?.success && (
            <div className="rounded-lg border border-resolve-success/30 bg-resolve-success/5 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-resolve-success">{retakeResult.message}</p>
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
            </div>
          )}
        </div>
      </Card>

      <Card>
        <div className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-resolve-text">Duplicate Clip Detection</h3>
            <p className="text-sm text-resolve-textDim mt-1">
              Scans video clips for exact duplicate source spans and flags every matching instance with review markers.
            </p>
          </div>

          <Button onClick={handleScanDuplicateClips} size="lg" loading={duplicateScanning}>
            {duplicateScanning ? 'Scanning Timeline...' : 'Scan Timeline for Duplicate Clips'}
          </Button>

          {duplicateResult?.message && !duplicateResult.error && (
            <div className="rounded-lg border border-resolve-accent/30 bg-resolve-accent/5 p-4 space-y-2">
              <p className="text-sm text-resolve-text">{duplicateResult.message}</p>
              <p className="text-xs text-resolve-textMuted">{buildDuplicateSummary(duplicateResult)}</p>
            </div>
          )}

          {duplicateResult?.warning && (
            <div className="rounded-lg border border-yellow-400/30 bg-yellow-400/10 p-4">
              <p className="text-sm text-yellow-300">{duplicateResult.warning}</p>
            </div>
          )}

          {duplicateResult?.error && (
            <div className="rounded-lg border border-resolve-error/30 bg-resolve-error/5 p-4">
              <p className="text-sm text-resolve-error">{duplicateResult.error}</p>
            </div>
          )}

          {duplicateGroups.length > 0 && (
            <div className="space-y-3">
              {duplicateGroups.map((group) => (
                <div
                  key={`${group.source_key}-${group.source_start_frame}-${group.source_end_frame}`}
                  className="rounded-xl border border-resolve-border bg-resolve-bg p-4"
                >
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div>
                      <h4 className="text-sm font-semibold text-resolve-text">
                        Duplicate Source Span ({group.instance_count} instances)
                      </h4>
                      <p className="text-xs text-resolve-textMuted mt-1">{group.source_label}</p>
                    </div>
                    <span className="text-xs font-mono text-resolve-textMuted">
                      Source {group.source_start_frame}-{group.source_end_frame}
                    </span>
                  </div>

                  <div className="space-y-2">
                    {group.instances.map((instance) => (
                      <button
                        key={`${instance.timeline_item_unique_id || instance.timeline_start_frame}-${instance.track_index}`}
                        onClick={() => handleJumpToTake(instance.timeline_start_seconds)}
                        className="w-full rounded-lg border border-transparent bg-resolve-surface p-3 text-left transition-colors hover:border-resolve-border hover:bg-resolve-surfaceLight"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm font-medium text-resolve-text">{instance.clip_name}</span>
                          <span className="text-xs text-resolve-textMuted">
                            V{instance.track_index} · {formatTime(instance.timeline_start_seconds, 'tenths')} - {formatTime(instance.timeline_end_seconds, 'tenths')}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-resolve-textMuted">
                          Timeline {instance.timeline_start_frame}-{instance.timeline_end_frame}
                          {' · '}
                          Source {instance.source_start_frame}-{instance.source_end_frame}
                        </p>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

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

function buildDuplicateSummary(result: DuplicateClipScanResult): string {
  const parts = [
    `${result.duplicate_group_count || 0} group${result.duplicate_group_count === 1 ? '' : 's'}`,
    `${result.duplicate_instance_count || 0} instance${result.duplicate_instance_count === 1 ? '' : 's'}`,
    `${result.timeline_marker_count || 0} timeline marker${result.timeline_marker_count === 1 ? '' : 's'}`,
    `${result.clip_marker_count || 0} clip marker${result.clip_marker_count === 1 ? '' : 's'}`,
  ]
  if (result.skipped_untrackable_count) {
    parts.push(`${result.skipped_untrackable_count} skipped`)
  }
  if (result.cleared_count) {
    parts.push(`${result.cleared_count} cleared`)
  }
  return parts.join(' · ')
}

function formatTime(seconds: number, precision: 'seconds' | 'tenths' = 'seconds'): string {
  const m = Math.floor(seconds / 60)
  const s = seconds - m * 60
  if (precision === 'tenths') {
    return `${m}:${s.toFixed(1).padStart(4, '0')}`
  }
  return `${m}:${Math.floor(s).toString().padStart(2, '0')}`
}

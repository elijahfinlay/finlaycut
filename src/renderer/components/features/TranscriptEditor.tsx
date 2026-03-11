import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { Modal } from '../common/Modal'
import { useProjectStore } from '../../stores/projectStore'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress, subscribe, sendWsMessage } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'
import { useSettingsStore } from '../../stores/settingsStore'

import {
  type WordAction,
  type TranscriptWord,
  type Segment,
  type TranscriptContext,
  type TranscriptSession,
  type SpeakerSummary,
  type WaveformViewport,
  type CutBoundary,
  TE_OP_NAME,
  FILLER_WORDS,
  FILLER_PATTERNS,
  MIN_FILLER_FRAMES,
  SAFETY_PADDING_SECONDS,
  lcsWordDiff,
  normalizeSpokenToken,
  isSilenceMarker,
  isAudioFillerMarker,
  isAnalysisMarker,
  sortTranscriptWords,
  isLikelyFillerWord,
  wordOverlapsSegment,
  findActiveWordIndex,
  retimeWordsAfterCuts,
  normalizeWord,
  isTranscriptSessionStale,
  mergeWordSegments,
  formatTimestamp,
} from './transcript/types'
import { TranscriptToolbar } from './transcript/TranscriptToolbar'
import { TranscriptWords } from './transcript/TranscriptWords'
import { TranscriptFooter } from './transcript/TranscriptFooter'
import { SilenceSettingsPanel } from './transcript/SilenceSettingsPanel'
import { SpeakerPanel } from './transcript/SpeakerPanel'
import { ScriptCutPanel } from './transcript/ScriptCutPanel'
import { WaveformCard } from './transcript/WaveformCard'

interface RepetitionTake {
  word_start_index?: number
  word_end_index?: number
}

interface RepetitionGroup {
  best_index?: number
  takes?: RepetitionTake[]
}

type QuickAnalyzeStep = 'idle' | 'silences' | 'fillers' | 'retakes' | 'speakers'

interface QuickAnalyzeSummary {
  silenceCount: number
  silenceSeconds: number
  fillerCount: number
  retakeGroups: number
  retakeTakes: number
  speakerCount: number
  completedAt: number
}

function normalizeAppliedCutSegments(rawSegments: any[]): Segment[] {
  return rawSegments
    .map((raw) => ({
      start: Number(raw?.start || 0),
      end: Number(raw?.end || 0),
      start_frame: typeof raw?.start_frame === 'number' ? raw.start_frame : undefined,
      end_frame: typeof raw?.end_frame === 'number' ? raw.end_frame : undefined,
      source_start_frame:
        typeof raw?.source_start_frame === 'number' ? raw.source_start_frame : undefined,
      source_end_frame:
        typeof raw?.source_end_frame === 'number' ? raw.source_end_frame : undefined,
      kind: typeof raw?.kind === 'string' ? raw.kind : undefined,
      label: typeof raw?.label === 'string' ? raw.label : undefined,
      boundary_refined:
        typeof raw?.boundary_refined === 'boolean' ? raw.boundary_refined : undefined,
      boundary_source:
        typeof raw?.boundary_source === 'string' ? raw.boundary_source : undefined,
      prev_word_end:
        typeof raw?.prev_word_end === 'number' ? raw.prev_word_end : undefined,
      next_word_start:
        typeof raw?.next_word_start === 'number' ? raw.next_word_start : undefined,
      prev_word_end_frame:
        typeof raw?.prev_word_end_frame === 'number' ? raw.prev_word_end_frame : undefined,
      next_word_start_frame:
        typeof raw?.next_word_start_frame === 'number' ? raw.next_word_start_frame : undefined,
      padding_ms: typeof raw?.padding_ms === 'number' ? raw.padding_ms : undefined,
    }))
    .filter((segment) => segment.end > segment.start)
    .sort((a, b) => a.start - b.start)
}

export function TranscriptEditor() {
  const {
    transcriptWords: storedWords,
    transcriptLanguage: storedLanguage,
    transcriptContext: storedContext,
    transcriptSession: storedSession,
    transcriptSpeakers: storedSpeakers,
    transcriptWaveform: storedWaveform,
    transcriptBackup: storedBackup,
    pendingTranscriptRecovery,
    silenceSettings: storedSilenceSettings,
    setTranscriptWords: storeWords,
    setTranscriptLanguage: storeLanguage,
    setTranscriptContext: storeContext,
    setTranscriptSession: storeSession,
    setTranscriptSpeakers: storeSpeakers,
    setTranscriptWaveform: storeWaveform,
    setTranscriptBackup: storeTranscriptBackup,
    restoreTranscriptBackup,
    setPendingTranscriptRecovery,
    setSilenceSegments: storeSilenceSegments,
    setSilenceSettings: storeSilenceSettings,
    setRepetitionGroups: storeRepetitionGroups,
  } = useFeatureDataStore()

  const teOp = useOperationStore((s) => s.operations[TE_OP_NAME])
  const transcribing = teOp?.status === 'running'
  const fillerWords = useSettingsStore((s) => s.fillerWords)

  const [words, setWords] = useState<TranscriptWord[]>(
    storedWords.map((word) => normalizeWord(word))
  )
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [language, setLanguage] = useState(storedLanguage)
  const [transcriptContext, setTranscriptContext] = useState<TranscriptContext | null>(storedContext)
  const [transcriptSession, setTranscriptSession] = useState<TranscriptSession | null>(storedSession)
  const [speakerSummaries, setSpeakerSummaries] = useState<SpeakerSummary[]>(storedSpeakers || [])
  const [selectionAnchor, setSelectionAnchor] = useState<number | null>(null)
  const [dragStart, setDragStart] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [waveform, setWaveform] = useState<{ amplitudes: number[]; duration: number } | null>(storedWaveform)
  const [activeWordIndex, setActiveWordIndex] = useState<number | null>(null)
  const [playheadSeconds, setPlayheadSeconds] = useState<number>(0)
  const [isFollowing, setIsFollowing] = useState(true)
  const [showTimestamps, setShowTimestamps] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [findingFillers, setFindingFillers] = useState(false)
  const [findingRepetitions, setFindingRepetitions] = useState(false)
  const [findingSilences, setFindingSilences] = useState(false)
  const [pendingAutoAnalyzeFillers, setPendingAutoAnalyzeFillers] = useState(false)
  const [showSilenceSettings, setShowSilenceSettings] = useState(false)
  const [showScriptPanel, setShowScriptPanel] = useState(false)
  const [showSpeakers, setShowSpeakers] = useState(false)
  const [showClearTranscriptConfirm, setShowClearTranscriptConfirm] = useState(false)
  const [silenceThreshold, setSilenceThreshold] = useState(storedSilenceSettings.threshold) // dB
  const [silenceMinDuration, setSilenceMinDuration] = useState(storedSilenceSettings.minDuration) // ms
  const [silenceMinFrames, setSilenceMinFrames] = useState(Math.max(storedSilenceSettings.minFrames ?? 14, 14))
  const [silencePadding, setSilencePadding] = useState(Math.max(storedSilenceSettings.padding, 150)) // ms — floor of 150 to protect dialogue
  const [autoDetectFillers, setAutoDetectFillers] = useState(true)
  const [detectedFillerRanges, setDetectedFillerRanges] = useState<Segment[]>([])
  const [analyzingSpeakers, setAnalyzingSpeakers] = useState(false)
  const [isReconciling, setIsReconciling] = useState(false)
  const [scriptText, setScriptText] = useState('')
  const [heldClickAction, setHeldClickAction] = useState<'cut' | 'mute' | 'keep' | null>(null)
  const [quickAnalyzeStep, setQuickAnalyzeStep] = useState<QuickAnalyzeStep>('idle')
  const [quickAnalyzeSummary, setQuickAnalyzeSummary] = useState<QuickAnalyzeSummary | null>(null)
  const pendingAutoAnalyzeTranscriptWordsRef = useRef<TranscriptWord[] | null>(null)
  const {
    framerate,
    timelineName: currentTimelineName,
    hasPendingResolveChange,
    pendingProjectName,
    pendingTimelineName,
  } = useProjectStore()
  const [operationBlocked, setOperationBlocked] = useState(false)
  const { progress, message, elapsed, eta } = useProgress('transcript_editor')
  const {
    progress: speakerProgress,
    message: speakerMessage,
    elapsed: speakerElapsed,
    eta: speakerEta,
  } = useProgress('diarization')
  const abortRef = useRef<AbortController | null>(null)
  const activeWordRef = useRef<HTMLSpanElement>(null)
  const transcriptContainerRef = useRef<HTMLDivElement | null>(null)
  const lastClickTimeRef = useRef<number>(0)
  const pendingAutoDetectRef = useRef(false)
  const heldClickActionRef = useRef<'cut' | 'mute' | 'keep' | null>(null)
  const staleNotifiedRef = useRef(false)
  const recoveryHandledRef = useRef(false)
  const mountedAtRef = useRef(Date.now())
  const staleCountRef = useRef(0)
  const isPlayingRef = useRef(false)
  const lastToggleTimeRef = useRef(0)
  const pendingResolveNoticeAtRef = useRef(0)
  const playheadSampleRef = useRef<{ seconds: number; sampledAt: number }>({
    seconds: 0,
    sampledAt: performance.now(),
  })
  const quickAnalyzeRunning = quickAnalyzeStep !== 'idle'
  const transcriptIsStale = isTranscriptSessionStale(transcriptSession)
  const transcriptMutationBlocked = transcriptIsStale || applying || isReconciling || quickAnalyzeRunning
  const transcriptClearBlocked =
    transcribing || applying || isReconciling || findingFillers || findingRepetitions || findingSilences || analyzingSpeakers || quickAnalyzeRunning
  // Waveform viewport: null = auto (follow selection), 'full' = force full timeline, object = manual pan/zoom
  const [waveformViewport, setWaveformViewport] = useState<WaveformViewport | 'full' | null>(null)

  // Auto-zoom viewport: when words are selected, zoom the waveform to that region
  const autoZoomViewport = useMemo<WaveformViewport | null>(() => {
    const selected = words.filter((w) => w.selected)
    if (selected.length === 0 || !waveform) return null

    const selStart = Math.min(...selected.map((w) => w.start))
    const selEnd = Math.max(...selected.map((w) => w.end))
    const selDuration = selEnd - selStart

    // Add context padding: ~2s or 1.5x selection duration, whichever is larger
    const padding = Math.max(2, selDuration * 1.5)
    const startTime = Math.max(0, selStart - padding)
    const endTime = Math.min(waveform.duration, selEnd + padding)

    return { startTime, endTime }
  }, [words, waveform])

  // Freeze viewport during boundary drags so word time changes don't shift the view
  const frozenViewportRef = useRef<WaveformViewport | null | undefined>(undefined)

  // Resolve effective viewport: manual > 'full' override > auto-zoom
  const computedViewport =
    waveformViewport === 'full'
      ? null
      : typeof waveformViewport === 'object' && waveformViewport !== null
        ? waveformViewport
        : autoZoomViewport

  const effectiveViewport = frozenViewportRef.current !== undefined
    ? frozenViewportRef.current
    : computedViewport

  // Reset manual viewport whenever the selection changes so auto-zoom kicks in
  const selectionSig = useMemo(() => {
    let sig = ''
    for (let i = 0; i < words.length; i++) {
      if (words[i].selected) sig += `${i},`
    }
    return sig
  }, [words])

  const prevSelSigRef = useRef(selectionSig)
  useEffect(() => {
    if (selectionSig !== prevSelSigRef.current) {
      const hadSelection = prevSelSigRef.current !== ''
      const hasSelection = selectionSig !== ''
      prevSelSigRef.current = selectionSig

      // Only auto-zoom when gaining a NEW selection, not when selection changes/clears
      // If user manually zoomed to 'full', don't override unless they make a fresh selection
      if (hasSelection && !hadSelection) {
        setWaveformViewport(null) // triggers auto-zoom
      } else if (!hasSelection) {
        setWaveformViewport(null) // no selection → full timeline (autoZoomViewport returns null)
      }
      // If selection changed but user was in 'full' or manual zoom, keep their viewport
    }
  }, [selectionSig])

  // Cut boundaries: all cut↔keep transitions with exact edge times for frame-accurate trimming
  const cutBoundaries = useMemo<CutBoundary[]>(() => {
    if (words.length === 0) return []

    const boundaries: CutBoundary[] = []
    for (let i = 0; i < words.length - 1; i++) {
      const curr = words[i]
      const next = words[i + 1]

      // Transition from non-cut to cut → boundary is at the start of the cut word
      if (curr.action !== 'cut' && next.action === 'cut') {
        boundaries.push({
          time: next.start,
          wordIndexBefore: i,
          wordIndexAfter: i + 1,
          side: 'start',
        })
      }
      // Transition from cut to non-cut → boundary is at the end of the cut word
      if (curr.action === 'cut' && next.action !== 'cut') {
        boundaries.push({
          time: curr.end,
          wordIndexBefore: i,
          wordIndexAfter: i + 1,
          side: 'end',
        })
      }
    }

    // Edge: first word is cut
    if (words[0].action === 'cut') {
      boundaries.push({ time: words[0].start, wordIndexBefore: -1, wordIndexAfter: 0, side: 'start' })
    }
    // Edge: last word is cut
    const last = words.length - 1
    if (words[last].action === 'cut') {
      boundaries.push({ time: words[last].end, wordIndexBefore: last, wordIndexAfter: last + 1, side: 'end' })
    }

    return boundaries
  }, [words])

  // Snapshot of words at drag start — used to compute frame-accurate boundary trims
  const dragSnapshotRef = useRef<TranscriptWord[] | null>(null)
  const fpsValue = parseFloat(framerate) || 24

  // Snap a time value to the nearest frame boundary
  const snapToFrame = useCallback(
    (t: number) => Math.round(t * fpsValue) / fpsValue,
    [fpsValue],
  )

  const handleBoundaryDragStart = useCallback(() => {
    dragSnapshotRef.current = words.map((w) => ({ ...w }))
    frozenViewportRef.current = effectiveViewport
  }, [words, effectiveViewport])

  const handleBoundaryDrag = useCallback(
    (boundary: CutBoundary, newTime: number) => {
      if (transcriptMutationBlocked) return
      const snapshot = dragSnapshotRef.current
      if (!snapshot) return

      const snapped = snapToFrame(Math.max(0, newTime))

      setWords((prev) => {
        const next = prev.map((word, i) => {
          const orig = snapshot[i]

          if (boundary.side === 'start') {
            // Dragging the start-of-cut boundary adjusts where the cut begins.
            // The cut word (wordIndexAfter) has its start time trimmed.
            // If dragged far enough past a word's midpoint, toggle its action.
            const cutIdx = boundary.wordIndexAfter
            if (cutIdx >= 0 && cutIdx < prev.length && i === cutIdx) {
              // This is the first cut word — trim its start
              const clampedStart = Math.max(0, Math.min(snapped, orig.end - 1 / fpsValue))
              if (Math.abs(word.start - clampedStart) < 0.0001) return word
              const newStartFrame = typeof word.start_frame === 'number'
                ? Math.round(clampedStart * fpsValue) + (word.start_frame - Math.round(word.start * fpsValue))
                : word.start_frame
              const newSourceStartFrame = typeof word.source_start_frame === 'number'
                ? Math.round(clampedStart * fpsValue) + (word.source_start_frame - Math.round(word.start * fpsValue))
                : word.source_start_frame
              return { ...word, start: Number(clampedStart.toFixed(4)), start_frame: newStartFrame, source_start_frame: newSourceStartFrame }
            }
            // Words whose midpoint falls between new and original boundary get toggled
            const mid = (orig.start + orig.end) / 2
            if (snapped < boundary.time && mid >= snapped && mid < boundary.time && orig.action !== 'cut') {
              return { ...word, action: 'cut' as WordAction, start: orig.start, end: orig.end }
            }
            if (snapped > boundary.time && mid >= boundary.time && mid < snapped && orig.action === 'cut') {
              return { ...word, action: 'keep' as WordAction, start: orig.start, end: orig.end }
            }
          } else {
            // Dragging the end-of-cut boundary adjusts where the cut ends.
            // The cut word (wordIndexBefore) has its end time trimmed.
            const cutIdx = boundary.wordIndexBefore
            if (cutIdx >= 0 && cutIdx < prev.length && i === cutIdx) {
              // This is the last cut word — trim its end
              const clampedEnd = Math.max(orig.start + 1 / fpsValue, Math.min(snapped, waveform?.duration ?? Infinity))
              if (Math.abs(word.end - clampedEnd) < 0.0001) return word
              const newEndFrame = typeof word.end_frame === 'number'
                ? Math.round(clampedEnd * fpsValue) + (word.end_frame - Math.round(word.end * fpsValue))
                : word.end_frame
              const newSourceEndFrame = typeof word.source_end_frame === 'number'
                ? Math.round(clampedEnd * fpsValue) + (word.source_end_frame - Math.round(word.end * fpsValue))
                : word.source_end_frame
              return { ...word, end: Number(clampedEnd.toFixed(4)), end_frame: newEndFrame, source_end_frame: newSourceEndFrame }
            }
            // Words whose midpoint falls between original boundary and new position get toggled
            const mid = (orig.start + orig.end) / 2
            if (snapped > boundary.time && mid > boundary.time && mid <= snapped && orig.action !== 'cut') {
              return { ...word, action: 'cut' as WordAction, start: orig.start, end: orig.end }
            }
            if (snapped < boundary.time && mid > snapped && mid <= boundary.time && orig.action === 'cut') {
              return { ...word, action: 'keep' as WordAction, start: orig.start, end: orig.end }
            }
          }

          // Restore original times for unaffected words (in case a previous drag modified them)
          if (word.start !== orig.start || word.end !== orig.end || word.action !== orig.action) {
            return { ...word, start: orig.start, end: orig.end, action: orig.action }
          }
          return word
        })

        return next
      })
    },
    [transcriptMutationBlocked, snapToFrame, fpsValue, waveform],
  )

  const handleBoundaryDragEnd = useCallback(() => {
    dragSnapshotRef.current = null
    frozenViewportRef.current = undefined
  }, [])

  const handleViewportChange = useCallback((vp: WaveformViewport | null) => {
    // null from the waveform means "zoomed all the way out"
    setWaveformViewport(vp ?? 'full')
  }, [])

  const handleResetZoom = useCallback(() => {
    setWaveformViewport('full')
  }, [])

  // Sync words to persistent store whenever they change
  useEffect(() => {
    storeWords(words)
  }, [words, storeWords])

  useEffect(() => {
    storeLanguage(language)
  }, [language, storeLanguage])

  useEffect(() => {
    storeContext(transcriptContext)
  }, [transcriptContext, storeContext])

  useEffect(() => {
    storeSession(transcriptSession)
  }, [transcriptSession, storeSession])

  useEffect(() => {
    storeSpeakers(speakerSummaries)
  }, [speakerSummaries, storeSpeakers])

  // Sync waveform to store so it survives page switches
  useEffect(() => {
    storeWaveform(waveform)
  }, [waveform, storeWaveform])

  // Sync silence settings to store
  useEffect(() => {
    storeSilenceSettings({
      threshold: silenceThreshold,
      minDuration: silenceMinDuration,
      minFrames: silenceMinFrames,
      padding: silencePadding,
    })
  }, [silenceThreshold, silenceMinDuration, silenceMinFrames, silencePadding, storeSilenceSettings])

  // Keep ref in sync so poll closure can read current play state
  useEffect(() => {
    isPlayingRef.current = isPlaying
  }, [isPlaying])

  const handleEmptyTranscriptResult = useCallback((data?: any) => {
    const message = data?.error || 'Transcription finished without any usable words.'
    useOperationStore.getState().failOperation(TE_OP_NAME, message)
    setResult({ error: message })
    toast.error(message)
  }, [])

  const saveTranscriptBackup = useCallback((snapshot?: {
    words?: TranscriptWord[]
    language?: string
    transcriptContext?: TranscriptContext | null
    transcriptSession?: TranscriptSession | null
    speakerSummaries?: SpeakerSummary[]
    waveform?: { amplitudes: number[]; duration: number } | null
  }) => {
    const wordsToSave = (snapshot?.words ?? words).map((word) => ({ ...word }))
    if (wordsToSave.length === 0) return false

    storeTranscriptBackup({
      words: wordsToSave,
      language: snapshot?.language ?? language,
      context: snapshot?.transcriptContext
        ? { ...snapshot.transcriptContext }
        : (transcriptContext ? { ...transcriptContext } : null),
      session: snapshot?.transcriptSession
        ? { ...snapshot.transcriptSession }
        : (transcriptSession ? { ...transcriptSession } : null),
      speakers: (snapshot?.speakerSummaries ?? speakerSummaries).map((speaker) => ({ ...speaker })),
      waveform: snapshot?.waveform
        ? {
            amplitudes: [...snapshot.waveform.amplitudes],
            duration: snapshot.waveform.duration,
          }
        : waveform
          ? {
              amplitudes: [...waveform.amplitudes],
              duration: waveform.duration,
            }
          : null,
      savedAt: Date.now(),
    })
    return true
  }, [
    words,
    language,
    transcriptContext,
    transcriptSession,
    speakerSummaries,
    waveform,
    storeTranscriptBackup,
  ])

  useEffect(() => {
    const handleBeforeUnload = () => {
      saveTranscriptBackup()
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [saveTranscriptBackup])

  useEffect(() => {
    if (recoveryHandledRef.current || !pendingTranscriptRecovery) return
    if (pendingTranscriptRecovery.startedAt >= mountedAtRef.current) return
    recoveryHandledRef.current = true

    const backupTimelineName = storedBackup?.session?.timeline_name ?? null
    const expectedTimelineName = pendingTranscriptRecovery.timelineName ?? null
    const backupMatchesRecoveryTimeline =
      expectedTimelineName === null
        ? backupTimelineName === null
        : backupTimelineName === expectedTimelineName
    const backupMatchesCurrentTimeline =
      !currentTimelineName || !backupTimelineName || backupTimelineName === currentTimelineName

    let restored = false
    if (words.length === 0 && storedBackup && backupMatchesRecoveryTimeline && backupMatchesCurrentTimeline) {
      const backup = restoreTranscriptBackup()
      if (backup) {
        restored = true
        setWords(backup.words.map((word) => normalizeWord(word)))
        setLanguage(backup.language)
        setTranscriptContext(backup.context ? { ...backup.context } : null)
        setTranscriptSession(backup.session ? { ...backup.session } : null)
        setSpeakerSummaries(Array.isArray(backup.speakers) ? backup.speakers.map((speaker) => ({ ...speaker })) : [])
        setWaveform(
          backup.waveform
            ? {
                amplitudes: [...backup.waveform.amplitudes],
                duration: backup.waveform.duration,
              }
            : null
        )
        staleNotifiedRef.current = false
      }
    }

    const timelineLabel = pendingTranscriptRecovery.timelineName
      ? ` on ${pendingTranscriptRecovery.timelineName}`
      : ''
    const message = restored
      ? `FinlayCut closed during transcription${timelineLabel}. Restored your last saved transcript.`
      : `FinlayCut closed during transcription${timelineLabel}. The unfinished transcription was interrupted.`

    useOperationStore.getState().failOperation(TE_OP_NAME, message)
    setResult({ error: message })
    toast.error(message)
    setPendingTranscriptRecovery(null)
  }, [
    currentTimelineName,
    pendingTranscriptRecovery,
    restoreTranscriptBackup,
    setPendingTranscriptRecovery,
    storedBackup,
    words.length,
  ])

  const applyBackendFillerDetections = useCallback((
    inputWords: TranscriptWord[],
    detections: any[],
    markForCut: boolean,
  ) => {
    const transcriptRanges: Segment[] = detections
      .filter((detection: any) => detection?.kind !== 'audio_hesitation')
      .map((detection: any) => ({
        start: detection.start,
        end: detection.end,
        start_frame: detection.start_frame,
        end_frame: detection.end_frame,
        source_start_frame: detection.source_start_frame,
        source_end_frame: detection.source_end_frame,
        kind: typeof detection.kind === 'string' ? detection.kind : undefined,
        label: typeof detection.word === 'string' ? detection.word : undefined,
      }))
    const highlightRanges = [...transcriptRanges]
    const audioOnlyDetections = detections.filter((detection: any) => detection?.kind === 'audio_hesitation')
    const heuristicRanges = inputWords.reduce<Segment[]>((acc, word, index) => {
      const prevWord = index > 0 ? inputWords[index - 1] : null
      const nextWord = index < inputWords.length - 1 ? inputWords[index + 1] : null
      if (isAnalysisMarker(word) || !isLikelyFillerWord(word, prevWord, nextWord)) {
        return acc
      }
      acc.push({
        start: word.start,
        end: word.end,
        start_frame: word.start_frame,
        end_frame: word.end_frame,
        source_start_frame: word.source_start_frame,
        source_end_frame: word.source_end_frame,
      })
      return acc
    }, [])
    const combinedRanges = [...highlightRanges]
    for (const heuristic of heuristicRanges) {
      const overlapsExisting = combinedRanges.some((segment) => {
        if (
          typeof heuristic.start_frame === 'number' &&
          typeof heuristic.end_frame === 'number' &&
          typeof segment.start_frame === 'number' &&
          typeof segment.end_frame === 'number'
        ) {
          return heuristic.end_frame > segment.start_frame && heuristic.start_frame < segment.end_frame
        }
        return heuristic.end > segment.start && heuristic.start < segment.end
      })
      if (!overlapsExisting) {
        combinedRanges.push(heuristic)
      }
    }

    let markedCount = 0
    const wordsWithoutConflictingSilence = inputWords.filter(
      (word) =>
        !(
          isSilenceMarker(word) &&
          combinedRanges.some((segment) => wordOverlapsSegment(word, segment))
        )
    )
    const nextWords = wordsWithoutConflictingSilence.map((word) => {
      if (isAnalysisMarker(word)) return word
      if (!combinedRanges.some((segment) => wordOverlapsSegment(word, segment))) return word
      if (!markForCut || word.action === 'cut') return word
      markedCount++
      return { ...word, action: 'cut' as WordAction }
    })

    const fps = parseFloat(framerate) || 24
    for (const range of transcriptRanges) {
      const overlappingIndices: number[] = []
      for (let i = 0; i < nextWords.length; i++) {
        const w = nextWords[i]
        if (!isAnalysisMarker(w) && !isSilenceMarker(w) && wordOverlapsSegment(w, range)) {
          overlappingIndices.push(i)
        }
      }
      if (overlappingIndices.length === 0) continue

      const firstIdx = overlappingIndices[0]
      const lastIdx = overlappingIndices[overlappingIndices.length - 1]

      if (range.start !== nextWords[firstIdx].start) {
        const first = { ...nextWords[firstIdx] }
        const delta = first.start - range.start
        const deltaFrames = Math.round(delta * fps)
        first.start = Number(range.start.toFixed(4))
        if (typeof first.start_frame === 'number') first.start_frame -= deltaFrames
        if (typeof first.source_start_frame === 'number') first.source_start_frame -= deltaFrames
        first.boundary_refined = true
        nextWords[firstIdx] = first
      }

      if (range.end !== nextWords[lastIdx].end) {
        const last = { ...nextWords[lastIdx] }
        const delta = range.end - last.end
        const deltaFrames = Math.round(delta * fps)
        last.end = Number(range.end.toFixed(4))
        if (typeof last.end_frame === 'number') last.end_frame += deltaFrames
        if (typeof last.source_end_frame === 'number') last.source_end_frame += deltaFrames
        last.boundary_refined = true
        nextWords[lastIdx] = last
      }
    }

    const attachedAudioDetections = audioOnlyDetections.filter(
      (detection: any) =>
        detection?.attachment === 'word_onset' || detection?.attachment === 'word_offset'
    )
    const standaloneAudioDetections = audioOnlyDetections.filter(
      (detection: any) =>
        detection?.attachment !== 'word_onset' && detection?.attachment !== 'word_offset'
    )

    const attachedFillerMarkers = attachedAudioDetections.reduce<TranscriptWord[]>(
      (markers, detection: any) => {
        const segment = {
          start: Number(detection.start || 0),
          end: Number(detection.end || 0),
          start_frame:
            typeof detection.start_frame === 'number' ? detection.start_frame : undefined,
          end_frame:
            typeof detection.end_frame === 'number' ? detection.end_frame : undefined,
        }
        const overlappingIndices = nextWords.reduce<number[]>((acc, word, index) => {
          if (!isAnalysisMarker(word) && wordOverlapsSegment(word, segment)) {
            acc.push(index)
          }
          return acc
        }, [])

        const attachedWordIndex =
          overlappingIndices.find((index) =>
            typeof detection.attached_word === 'string'
              ? nextWords[index].word === detection.attached_word
              : false
          ) ?? (overlappingIndices.length === 1 ? overlappingIndices[0] : -1)

        if (attachedWordIndex < 0) {
          markers.push(
            normalizeWord({
              word:
                typeof detection.word === 'string' && detection.word.trim().length > 0
                  ? detection.word
                  : '[filler]',
              kind: 'audio_hesitation',
              start: detection.start,
              end: detection.end,
              start_frame: detection.start_frame,
              end_frame: detection.end_frame,
              source_start_frame: detection.source_start_frame,
              source_end_frame: detection.source_end_frame,
              probability: detection.probability ?? 1,
              action: markForCut ? 'cut' : 'keep',
              selected: false,
            })
          )
          return markers
        }

        const attachedWord = nextWords[attachedWordIndex]
        const minRemainingSeconds = Math.max(1 / Math.max(fps, 1), 0.02)
        const updatedWord = { ...attachedWord, boundary_refined: true }
        if (detection?.attachment === 'word_offset') {
          const nextEnd = Number(segment.start.toFixed(4))
          if (
            !Number.isFinite(nextEnd)
            || nextEnd <= attachedWord.start + minRemainingSeconds
            || nextEnd >= attachedWord.end
          ) {
            return markers
          }
          updatedWord.end = nextEnd
          if (typeof detection.start_frame === 'number') {
            updatedWord.end_frame = detection.start_frame
          } else if (typeof attachedWord.end_frame === 'number') {
            updatedWord.end_frame = attachedWord.end_frame - Math.round((attachedWord.end - nextEnd) * fps)
          }
          if (typeof detection.source_start_frame === 'number') {
            updatedWord.source_end_frame = detection.source_start_frame
          } else if (typeof attachedWord.source_end_frame === 'number') {
            updatedWord.source_end_frame = attachedWord.source_end_frame - Math.round((attachedWord.end - nextEnd) * fps)
          }
        } else {
          const nextStart = Number(segment.end.toFixed(4))
          if (
            !Number.isFinite(nextStart)
            || nextStart <= attachedWord.start
            || nextStart >= attachedWord.end - minRemainingSeconds
          ) {
            return markers
          }
          updatedWord.start = nextStart
          if (typeof detection.end_frame === 'number') {
            updatedWord.start_frame = detection.end_frame
          } else if (typeof attachedWord.start_frame === 'number') {
            updatedWord.start_frame = attachedWord.start_frame + Math.round((nextStart - attachedWord.start) * fps)
          }
          if (typeof detection.source_end_frame === 'number') {
            updatedWord.source_start_frame = detection.source_end_frame
          } else if (typeof attachedWord.source_start_frame === 'number') {
            updatedWord.source_start_frame = attachedWord.source_start_frame + Math.round((nextStart - attachedWord.start) * fps)
          }
        }
        nextWords[attachedWordIndex] = updatedWord

        markers.push(
          normalizeWord({
            word:
              typeof detection.word === 'string' && detection.word.trim().length > 0
                ? detection.word
                : '[filler]',
            kind: 'audio_hesitation',
            start: detection.start,
            end: detection.end,
            start_frame: detection.start_frame,
            end_frame: detection.end_frame,
            source_start_frame: detection.source_start_frame,
            source_end_frame: detection.source_end_frame,
            probability: detection.probability ?? 1,
            action: markForCut ? 'cut' : 'keep',
            selected: false,
          })
        )

        return markers
      },
      []
    )

    const fillerMarkers = [
      ...attachedFillerMarkers,
      ...standaloneAudioDetections
        .filter((detection: any) => {
          const segment = {
            start: Number(detection.start || 0),
            end: Number(detection.end || 0),
            start_frame:
              typeof detection.start_frame === 'number' ? detection.start_frame : undefined,
            end_frame:
              typeof detection.end_frame === 'number' ? detection.end_frame : undefined,
          }
          return !nextWords.some(
            (word) => !isAnalysisMarker(word) && wordOverlapsSegment(word, segment)
          )
        })
        .map((detection: any) =>
          normalizeWord({
            word:
              typeof detection.word === 'string' && detection.word.trim().length > 0
                ? detection.word
                : '[filler]',
            kind: 'audio_hesitation',
            start: detection.start,
            end: detection.end,
            start_frame: detection.start_frame,
            end_frame: detection.end_frame,
            source_start_frame: detection.source_start_frame,
            source_end_frame: detection.source_end_frame,
            probability: detection.probability ?? 1,
            action: markForCut ? 'cut' : 'keep',
            selected: false,
          })
        ),
    ]

    return {
      words: sortTranscriptWords([...nextWords, ...fillerMarkers]),
      ranges: combinedRanges,
      totalMarked: markedCount + fillerMarkers.length,
    }
  }, [framerate])

  // Pickup: if transcription completed while component was unmounted
  useEffect(() => {
    if (teOp?.status === 'complete' && teOp.result) {
      const data = teOp.result
      if (Array.isArray(data.words) && data.words.length > 0) {
        const normalized = data.words.map((word: any) => normalizeWord(word))
        const nextLanguage =
          typeof data.language === 'string' && data.language.trim().length > 0
            ? data.language
            : language
        const hasInitialFillerDetections = autoDetectFillers && Array.isArray(data.filler_detections)
        const annotated = hasInitialFillerDetections
          ? applyBackendFillerDetections(normalized, data.filler_detections || [], false)
          : null
        pendingAutoDetectRef.current = true
        pendingAutoAnalyzeTranscriptWordsRef.current = normalized
        setWords(annotated ? annotated.words : normalized)
        setLanguage(nextLanguage)
        setSpeakerSummaries(Array.isArray(data.speakers) ? data.speakers : [])
        setDetectedFillerRanges(annotated ? annotated.ranges : [])
        storeRepetitionGroups([])
        if (data.transcript_context) {
          setTranscriptContext(data.transcript_context)
        }
        if (data.transcript_session) {
          setTranscriptSession(data.transcript_session)
          staleNotifiedRef.current = false
        }
      } else {
        handleEmptyTranscriptResult(data)
      }
      useOperationStore.getState().clearResult(TE_OP_NAME)
    }
    if (teOp?.status === 'error' && teOp.error) {
      setResult({ error: teOp.error })
    }
  }, [teOp?.status, teOp?.result, teOp?.error, autoDetectFillers, applyBackendFillerDetections, handleEmptyTranscriptResult, language, storeRepetitionGroups])

  useEffect(() => {
    const handleWindowMouseUp = () => {
      setIsDragging(false)
      setDragStart(null)
    }
    window.addEventListener('mouseup', handleWindowMouseUp)
    return () => {
      window.removeEventListener('mouseup', handleWindowMouseUp)
    }
  }, [])

  useEffect(() => {
    if (!transcriptIsStale) {
      staleNotifiedRef.current = false
    }
  }, [transcriptIsStale])

  useEffect(() => {
    if (words.length === 0 || transcriptSession) return
    setTranscriptSession({
      session_id: 'legacy-transcript-session',
      timeline_name: null,
      timeline_signature: '',
      last_synced_at: new Date().toISOString(),
      sync_state: 'requires_retranscribe',
      stale_reason: 'signature_mismatch',
      origin: 'transcribe',
    })
  }, [words.length, transcriptSession])

  const markTranscriptStale = useCallback((
    reason: TranscriptSession['stale_reason'] = 'external_timeline_change',
  ) => {
    setTranscriptSession((prev) => {
      if (!prev) return prev
      if (prev.sync_state === 'stale' && prev.stale_reason === reason) {
        return prev
      }
      return {
        ...prev,
        sync_state: 'stale',
        stale_reason: reason,
      }
    })
    if (!staleNotifiedRef.current) {
      toast.warning('This timeline changed after the transcript was created. Re-transcribe or sync before editing.')
      staleNotifiedRef.current = true
    }
  }, [])

  useEffect(() => {
    if (!transcriptSession || words.length === 0) return
    if (transcriptSession.sync_state !== 'fresh') return
    if (transcribing || applying || isReconciling) return
    if (!currentTimelineName || !transcriptSession.timeline_name) return
    if (currentTimelineName === transcriptSession.timeline_name) return
    markTranscriptStale('external_timeline_change')
  }, [
    currentTimelineName,
    transcriptSession,
    words.length,
    transcribing,
    applying,
    isReconciling,
    markTranscriptStale,
  ])

  const requireFreshTranscript = useCallback((message?: string) => {
    if (!transcriptIsStale) return true
    if (message) {
      toast.info(message)
    }
    return false
  }, [transcriptIsStale])

  const requirePinnedTimelineInteraction = useCallback((action: string, showToast = true) => {
    if (!hasPendingResolveChange) return true
    if (showToast) {
      const now = Date.now()
      if (now - pendingResolveNoticeAtRef.current > 1500) {
        const pendingTarget = pendingTimelineName
          ? `${pendingProjectName ? `${pendingProjectName} / ` : ''}${pendingTimelineName}`
          : 'the current Resolve timeline'
        toast.info(`Resolve is on ${pendingTarget}. Click Reconnect in the header before ${action}.`)
        pendingResolveNoticeAtRef.current = now
      }
    }
    return false
  }, [hasPendingResolveChange, pendingProjectName, pendingTimelineName])

  const replaceSpokenWords = useCallback((
    allWords: TranscriptWord[],
    nextSpokenWords: TranscriptWord[],
  ) => {
    let spokenIndex = 0
    return allWords.map((word) => {
      if (isAnalysisMarker(word)) return word
      const replacement = nextSpokenWords[spokenIndex]
      spokenIndex += 1
      return replacement ?? word
    })
  }, [])

  const applyRepetitionCuts = useCallback((
    spokenWords: TranscriptWord[],
    groups: RepetitionGroup[],
  ) => {
    if (spokenWords.length === 0 || groups.length === 0) {
      return {
        words: spokenWords,
        groups: 0,
        takes: 0,
        wordsMarked: 0,
      }
    }

    const nextWords = spokenWords.map((word) => ({ ...word }))
    let groupsMarked = 0
    let takesMarked = 0
    let wordsMarked = 0

    for (const group of groups) {
      const takes = Array.isArray(group.takes) ? group.takes : []
      if (takes.length <= 1) continue

      let groupMarked = false
      for (let takeIndex = 0; takeIndex < takes.length; takeIndex += 1) {
        if (takeIndex === (group.best_index ?? 0)) continue

        const take = takes[takeIndex]
        const startIndex = typeof take.word_start_index === 'number' ? take.word_start_index : -1
        const endIndex = typeof take.word_end_index === 'number' ? take.word_end_index : -1
        if (startIndex < 0 || endIndex <= startIndex || endIndex > nextWords.length) continue

        let takeMarkedWords = 0
        for (let wordIndex = startIndex; wordIndex < endIndex; wordIndex += 1) {
          if (nextWords[wordIndex].action === 'cut') continue
          nextWords[wordIndex] = {
            ...nextWords[wordIndex],
            action: 'cut',
            selected: false,
          }
          takeMarkedWords += 1
        }

        if (takeMarkedWords > 0) {
          takesMarked += 1
          wordsMarked += takeMarkedWords
          groupMarked = true
        }
      }

      if (groupMarked) {
        groupsMarked += 1
      }
    }

    return {
      words: nextWords,
      groups: groupsMarked,
      takes: takesMarked,
      wordsMarked,
    }
  }, [])

  // Tell the backend to watch for signature changes when we have a fresh transcript
  const didRegisterWatchRef = useRef(false)
  const isReconcilingRef = useRef(false)

  useEffect(() => {
    if (
      words.length === 0 ||
      !transcriptSession?.timeline_signature ||
      transcriptSession.sync_state !== 'fresh' ||
      transcribing ||
      applying ||
      isReconciling ||
      hasPendingResolveChange
    ) {
      // Only send stop if we previously registered
      if (didRegisterWatchRef.current) {
        sendWsMessage({ type: 'watch_signature', signature: null, timeline_name: null })
        didRegisterWatchRef.current = false
      }
      return
    }

    didRegisterWatchRef.current = true
    sendWsMessage({
      type: 'watch_signature',
      signature: transcriptSession.timeline_signature,
      timeline_name: transcriptSession.timeline_name ?? null,
    })

    return () => {
      didRegisterWatchRef.current = false
      sendWsMessage({ type: 'watch_signature', signature: null, timeline_name: null })
    }
  }, [
    words.length,
    transcriptSession?.timeline_signature,
    transcriptSession?.sync_state,
    transcriptSession?.timeline_name,
    transcribing,
    applying,
    isReconciling,
    hasPendingResolveChange,
  ])

  // Auto-reconcile when the backend pushes a timeline_changed event
  const autoReconcile = useCallback(async () => {
    if (!transcriptSession || words.length === 0) return
    if (transcriptSession.sync_state !== 'fresh') return
    if (isReconcilingRef.current) return
    if (hasPendingResolveChange) return

    isReconcilingRef.current = true
    setIsReconciling(true)
    try {
      const data = await apiFetch('/api/transcript-editor/reconcile', {
        method: 'POST',
        body: JSON.stringify({
          words,
          transcript_context: transcriptContext,
          transcript_session: transcriptSession,
        }),
      })

      if (data?.success) {
        const normalizedWords = ((data.words || []) as any[])
          .map((word) => normalizeWord(word))
          .map((word) => ({
            ...word,
            speaker: undefined,
            speaker_index: undefined,
          }))
          .filter((word) => !isAnalysisMarker(word))
        setWords(sortTranscriptWords(normalizedWords))
        setTranscriptContext(data.transcript_context || transcriptContext)
        setTranscriptSession(data.transcript_session || transcriptSession)
        setDetectedFillerRanges([])
        setSpeakerSummaries([])
        storeRepetitionGroups([])
        setQuickAnalyzeSummary(null)
        staleNotifiedRef.current = false
        toast.success('Timeline changed — transcript synced automatically')
        return
      }

      // Reconcile failed — fall back to stale modal
      if (data?.transcript_session) {
        setTranscriptSession(data.transcript_session)
      }
      markTranscriptStale('external_timeline_change')
    } catch {
      markTranscriptStale('external_timeline_change')
    } finally {
      isReconcilingRef.current = false
      setIsReconciling(false)
    }
  }, [words, transcriptContext, transcriptSession, markTranscriptStale, hasPendingResolveChange, storeRepetitionGroups])

  // Stable ref so the WS subscriber never needs to re-register
  const autoReconcileRef = useRef(autoReconcile)
  useEffect(() => { autoReconcileRef.current = autoReconcile }, [autoReconcile])

  useEffect(() => {
    const unsub = subscribe((msg) => {
      if (msg.type === 'timeline_changed') {
        if (hasPendingResolveChange) return
        if (
          transcriptSession?.timeline_name &&
          msg.timeline_name &&
          msg.timeline_name !== transcriptSession.timeline_name
        ) {
          return
        }
        void autoReconcileRef.current()
      }
    })
    return unsub
  }, [hasPendingResolveChange, transcriptSession?.timeline_name])

  // Annotation pipeline: mark fillers and repeated words for highlighting
  const { fillerCount, repeatedCount } = useMemo(() => {
    if (words.length === 0) return { fillerCount: 0, repeatedCount: 0, filteredText: '' }

    let fillers = 0
    let repeats = 0
    const textParts: string[] = []

    for (let i = 0; i < words.length; i++) {
      const w = words[i]
      if (w.action === 'cut') continue // excluded from filtered text

      const norm = normalizeSpokenToken(w.word)

      const isDetectedFiller =
        detectedFillerRanges.some((segment) => wordOverlapsSegment(w, segment)) ||
        isLikelyFillerWord(w, words[i - 1], words[i + 1])

      if (autoDetectFillers && isDetectedFiller) {
        fillers++
      }

      if (autoDetectFillers && i > 0 && norm.length > 1) {
        const prevNorm = normalizeSpokenToken(words[i - 1].word)
        if (norm === prevNorm) repeats++
      }

      textParts.push(w.word)
    }

    return { fillerCount: fillers, repeatedCount: repeats, filteredText: textParts.join(' ') }
  }, [words, autoDetectFillers, detectedFillerRanges])

  // Helper: check if a word is a filler
  const isWordFiller = useCallback((word: TranscriptWord) => {
    if (!autoDetectFillers) return false
    if (isAudioFillerMarker(word)) return true
    if (detectedFillerRanges.some((segment) => wordOverlapsSegment(word, segment))) {
      return true
    }
    const index = words.indexOf(word)
    return isLikelyFillerWord(
      word,
      index > 0 ? words[index - 1] : null,
      index >= 0 && index < words.length - 1 ? words[index + 1] : null
    )
  }, [autoDetectFillers, words, detectedFillerRanges])

  // Helper: check if a word is a repeat of its predecessor
  const isWordRepeated = useCallback((index: number) => {
    if (!autoDetectFillers || index === 0) return false
    const norm = normalizeSpokenToken(words[index].word)
    if (norm.length <= 1) return false
    const prevNorm = normalizeSpokenToken(words[index - 1].word)
    return norm === prevNorm
  }, [words, autoDetectFillers])

  const hasDetectedFillers = useMemo(
    () => words.some((word) => isWordFiller(word)),
    [words, isWordFiller]
  )

  // Auto-detect silences + fillers after transcription completes
  useEffect(() => {
    if (!pendingAutoDetectRef.current || words.length === 0) return
    pendingAutoDetectRef.current = false
    if (hasPendingResolveChange) return
    setPendingAutoAnalyzeFillers(autoDetectFillers)
    void handleFindSilences({
      showTimelineSwitchToast: false,
      insertMarkers: false,
      showToasts: false,
    })
  }, [words, autoDetectFillers, hasPendingResolveChange]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pendingAutoAnalyzeFillers || findingSilences || words.length === 0 || hasPendingResolveChange) return
    setPendingAutoAnalyzeFillers(false)

    void analyzeFillers({
      markForCut: false,
      showToasts: false,
      enforceFreshTranscript: false,
      transcriptWordsOverride: pendingAutoAnalyzeTranscriptWordsRef.current || undefined,
    })
    pendingAutoAnalyzeTranscriptWordsRef.current = null
  }, [pendingAutoAnalyzeFillers, findingSilences, words, hasPendingResolveChange]) // eslint-disable-line react-hooks/exhaustive-deps

  // Fast poll: drift-correct interpolation base from Resolve's playhead (no play-state logic)
  useEffect(() => {
    if (words.length === 0) return

    let cancelled = false
    let lastSeconds = -1

    const poll = async () => {
      if (cancelled) return
      if (Date.now() - lastClickTimeRef.current < 1000) return

      try {
        const data = await apiFetch('/api/resolve/playhead', { method: 'GET' })
        if (cancelled || data?.seconds == null) return

        const sec = data.seconds as number
        const now = performance.now()

        // Drift-correct whenever Resolve reports a new position
        if (Math.abs(sec - playheadSampleRef.current.seconds) > 0.01) {
          playheadSampleRef.current = { seconds: sec, sampledAt: now }
          setPlayheadSeconds(sec)
        }

        // When paused, update active word from polled position
        if (Math.abs(sec - lastSeconds) < 0.005) return
        lastSeconds = sec
        setActiveWordIndex((current) => {
          const next = findActiveWordIndex(words, sec)
          return current === next ? current : next
        })
      } catch {
        // Silently fail — Resolve may not be connected
      }
    }

    const interval = window.setInterval(() => void poll(), 120)
    void poll()
    return () => { cancelled = true; clearInterval(interval) }
  }, [words])

  // WebSocket listener: instant transport state sync from backend key-event monitor
  useEffect(() => {
    if (words.length === 0) return

    const unsub = subscribe((msg: any) => {
      if (msg?.type !== 'transport_state') return
      // Don't override shortly after a user-initiated toggle from FinlayCut
      if (Date.now() - lastToggleTimeRef.current < 1500) return

      const resolveIsPlaying = msg.is_playing as boolean

      if (resolveIsPlaying && !isPlayingRef.current) {
        // Resolve started playing — start following from current position
        if (msg.seconds != null) {
          playheadSampleRef.current = {
            seconds: msg.seconds as number,
            sampledAt: performance.now(),
          }
        }
        setIsPlaying(true)
        setIsFollowing(true)
      } else if (!resolveIsPlaying && isPlayingRef.current) {
        // Resolve stopped — pause and snap to final position
        setIsPlaying(false)
        if (msg.seconds != null) {
          playheadSampleRef.current = {
            seconds: msg.seconds as number,
            sampledAt: performance.now(),
          }
          setPlayheadSeconds(msg.seconds as number)
        }
      }
    })

    return unsub
  }, [words])

  // rAF interpolation loop — only runs during playback to smoothly advance the playhead
  useEffect(() => {
    if (words.length === 0 || !isPlaying) return

    let cancelled = false
    let frameHandle = 0

    const tick = () => {
      if (cancelled) return

      const sample = playheadSampleRef.current
      const now = performance.now()
      const estimatedSeconds = sample.seconds + Math.max(0, (now - sample.sampledAt) / 1000)

      setPlayheadSeconds((prev) =>
        Math.abs(prev - estimatedSeconds) > 0.016 ? estimatedSeconds : prev
      )

      setActiveWordIndex((current) => {
        const next = findActiveWordIndex(words, estimatedSeconds)
        return current === next ? current : next
      })

      frameHandle = window.requestAnimationFrame(tick)
    }

    frameHandle = window.requestAnimationFrame(tick)
    return () => {
      cancelled = true
      window.cancelAnimationFrame(frameHandle)
    }
  }, [words, isPlaying])

  // Auto-scroll to the active word
  useEffect(() => {
    if (activeWordIndex !== null && activeWordRef.current && isFollowing) {
      activeWordRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
  }, [activeWordIndex, isFollowing])

  // Disable auto-follow on manual wheel scroll during playback
  useEffect(() => {
    const container = transcriptContainerRef.current
    if (!container) return

    const handleWheel = () => {
      if (isPlaying && isFollowing) {
        setIsFollowing(false)
      }
    }

    container.addEventListener('wheel', handleWheel, { passive: true })
    return () => {
      container.removeEventListener('wheel', handleWheel)
    }
  }, [isPlaying, isFollowing])

  // Spacebar play/pause shortcut plus hold-key click modes for cut/mute/keep.
  const lastSpaceRef = useRef<number>(0)
  const arrowDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const waveformSeekDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (words.length === 0) return

    const handleKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return

      if (e.repeat && (e.code === 'KeyC' || e.code === 'KeyM' || e.code === 'KeyK')) return

      if (e.code === 'Space') {
        e.preventDefault()
        if (!requirePinnedTimelineInteraction('previewing this transcript in Resolve')) return

        const now = Date.now()
        if (now - lastSpaceRef.current < 500) return
        lastSpaceRef.current = now

        // Optimistic toggle: immediately flip play state so interpolation starts/stops
        lastToggleTimeRef.current = Date.now()
        setIsPlaying((prev) => {
          if (!prev) {
            // Starting playback — re-snapshot so interpolation advances from current position
            playheadSampleRef.current = {
              ...playheadSampleRef.current,
              sampledAt: performance.now(),
            }
            // Re-enable auto-follow when starting playback
            setIsFollowing(true)
          }
          staleCountRef.current = 0
          return !prev
        })

        apiFetch('/api/resolve/transport', {
          method: 'POST',
          body: JSON.stringify({ action: 'toggle' }),
        }).catch(() => {})
        return
      }

      if (e.code === 'ArrowLeft' || e.code === 'ArrowRight') {
        e.preventDefault()
        if (!requirePinnedTimelineInteraction('previewing this transcript in Resolve')) return
        const frameDuration = 1 / fpsValue
        const dir = e.code === 'ArrowRight' ? 1 : -1
        const current = playheadSampleRef.current.seconds
        const next = Math.max(0, snapToFrame(current + dir * frameDuration))

        // Update local UI instantly
        lastClickTimeRef.current = Date.now()
        playheadSampleRef.current = { seconds: next, sampledAt: performance.now() }
        setPlayheadSeconds(next)
        setIsFollowing(false)
        setActiveWordIndex(findActiveWordIndex(words, next))

        // Debounce API call — only send final position to Resolve
        if (arrowDebounceRef.current) clearTimeout(arrowDebounceRef.current)
        arrowDebounceRef.current = setTimeout(() => {
          arrowDebounceRef.current = null
          apiFetch('/api/resolve/playhead', {
            method: 'POST',
            body: JSON.stringify({ seconds: playheadSampleRef.current.seconds }),
          }).catch(() => {})
        }, 80)
        return
      }

      if (e.code === 'KeyC' || e.code === 'KeyM' || e.code === 'KeyK') {
        if (transcriptIsStale) return
        const nextAction = e.code === 'KeyC'
          ? 'cut'
          : e.code === 'KeyM'
            ? 'mute'
            : 'keep'
        heldClickActionRef.current = nextAction
        setHeldClickAction(nextAction)
      }
    }

    const handleKeyUp = (e: KeyboardEvent) => {
      if (
        (e.code === 'KeyC' && heldClickActionRef.current === 'cut') ||
        (e.code === 'KeyM' && heldClickActionRef.current === 'mute') ||
        (e.code === 'KeyK' && heldClickActionRef.current === 'keep')
      ) {
        heldClickActionRef.current = null
        setHeldClickAction(null)
      }
    }

    const clearHeldAction = () => {
      heldClickActionRef.current = null
      setHeldClickAction(null)
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    window.addEventListener('blur', clearHeldAction)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      window.removeEventListener('blur', clearHeldAction)
      if (arrowDebounceRef.current) clearTimeout(arrowDebounceRef.current)
    }
  }, [words.length, words, transcriptIsStale, fpsValue, snapToFrame, requirePinnedTimelineInteraction])

  const setSelectionRange = useCallback((from: number, to: number, additive = false) => {
    const start = Math.min(from, to)
    const end = Math.max(from, to)

    setWords((prev) =>
      prev.map((word, index) => {
        const inRange = index >= start && index <= end
        const selected = additive ? word.selected || inRange : inRange
        return word.selected === selected ? word : { ...word, selected }
      })
    )
  }, [])

  const handleTranscribe = async (range?: { in_seconds: number; out_seconds: number }) => {
    if (!requirePinnedTimelineInteraction('starting a new transcription')) {
      return
    }
    saveTranscriptBackup()
    setPendingTranscriptRecovery({
      startedAt: Date.now(),
      timelineName: currentTimelineName,
      language: language || null,
      range: range ?? null,
    })
    pendingAutoAnalyzeTranscriptWordsRef.current = null
    useOperationStore.getState().startOperation(TE_OP_NAME)
    setResult(null)
    setSpeakerSummaries([])
    setDetectedFillerRanges([])
    setQuickAnalyzeSummary(null)
    storeSilenceSegments([])
    storeRepetitionGroups([])

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const body: any = { language: language || null, filler_words: fillerWords }
      if (range) {
        body.in_seconds = range.in_seconds
        body.out_seconds = range.out_seconds
      }
      const data = await apiFetch('/api/transcript-editor/transcribe', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (data.error) {
        useOperationStore.getState().failOperation(TE_OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else if (!Array.isArray(data.words) || data.words.length === 0) {
        handleEmptyTranscriptResult(data)
      } else {
        const normalized = (data.words || []).map((word: any) => normalizeWord(word))
        const ctx = data.transcript_context || null
        const nextSession = data.transcript_session || null
        const nextSpeakers = Array.isArray(data.speakers) ? data.speakers : []
        const nextLanguage =
          typeof data.language === 'string' && data.language.trim().length > 0
            ? data.language
            : language
        const hasInitialFillerDetections = autoDetectFillers && Array.isArray(data.filler_detections)
        const annotated = hasInitialFillerDetections
          ? applyBackendFillerDetections(normalized, data.filler_detections || [], false)
          : null

        // Write to Zustand stores directly (works even if unmounted)
        useFeatureDataStore.getState().setTranscriptWords(normalized)
        useFeatureDataStore.getState().setTranscriptLanguage(nextLanguage)
        useFeatureDataStore.getState().setTranscriptContext(ctx)
        useFeatureDataStore.getState().setTranscriptSession(nextSession)
        useFeatureDataStore.getState().setTranscriptSpeakers(nextSpeakers)
        saveTranscriptBackup({
          words: annotated ? annotated.words : normalized,
          language: nextLanguage,
          transcriptContext: ctx,
          transcriptSession: nextSession,
          speakerSummaries: nextSpeakers,
          waveform,
        })
        setLanguage(nextLanguage)
        setTranscriptSession(nextSession)
        staleNotifiedRef.current = false
        useOperationStore.getState().completeOperation(TE_OP_NAME, {
          words: data.words,
          language: nextLanguage,
          transcript_context: ctx,
          transcript_session: nextSession,
          speakers: nextSpeakers,
          filler_detections: data.filler_detections,
        })

        // Update local state for the UI
        pendingAutoDetectRef.current = true
        pendingAutoAnalyzeTranscriptWordsRef.current = normalized
        setWords(annotated ? annotated.words : normalized)
        setDetectedFillerRanges(annotated ? annotated.ranges : [])
        setTranscriptContext(ctx)
        setSpeakerSummaries(nextSpeakers)
        storeRepetitionGroups([])
        if (data.speaker_detection_error) {
          console.warn('Automatic speaker detection failed during transcription:', data.speaker_detection_error)
        }

        // Fetch waveform data in background — also writes to store
        if (!useProjectStore.getState().hasPendingResolveChange) {
          apiFetch('/api/waveform/analyze?samples=800', { method: 'POST' })
            .then((wf) => {
              if (wf.amplitudes) {
                const waveformData = { amplitudes: wf.amplitudes, duration: wf.duration }
                useFeatureDataStore.getState().setTranscriptWaveform(waveformData)
                setWaveform(waveformData)
              }
            })
            .catch(() => {})
        }
        if (range) {
          toast.success(`Transcribed selection (${formatTimestamp(range.in_seconds)} - ${formatTimestamp(range.out_seconds)})`)
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        useOperationStore.getState().failOperation(TE_OP_NAME, 'Transcription cancelled')
        setResult({ error: 'Transcription cancelled' })
        toast.error('Transcription cancelled')
      } else {
        const is409 = err.status === 409 || (err.message?.includes('already running'))
        if (is409) setOperationBlocked(true)
        useOperationStore.getState().failOperation(TE_OP_NAME, err.message)
        setResult({ error: err.message })
        toast.error(is409 ? 'A previous operation is stuck. Click "Force Cancel" to clear it.' : err.message)
      }
    } finally {
      abortRef.current = null
      setPendingTranscriptRecovery(null)
    }
  }

  const handleTranscribeSelection = async () => {
    if (!requirePinnedTimelineInteraction('starting a transcription on the current Resolve timeline')) {
      return
    }
    try {
      const data = await apiFetch('/api/resolve/in-out', { method: 'GET' })
      if (!data?.range) {
        toast.error('No in/out points set on the timeline. Set in/out marks in DaVinci Resolve first (I and O keys).')
        return
      }
      const { in_seconds, out_seconds } = data.range
      handleTranscribe({ in_seconds, out_seconds })
    } catch {
      toast.error('Could not read in/out points from DaVinci Resolve')
    }
  }

  const handleCancel = () => {
    if (abortRef.current) {
      abortRef.current.abort()
    }
  }

  const handleForceCancel = async () => {
    try {
      const data = await apiFetch('/api/resolve/operation/cancel', { method: 'POST' })
      if (data.reset) {
        setOperationBlocked(false)
        setResult(null)
        toast.success(`Cleared stuck operation: ${data.previous_operation || 'unknown'}`)
      } else {
        setOperationBlocked(false)
        setResult(null)
        toast.info('No stuck operation found — you should be good to go')
      }
    } catch (err: any) {
      toast.error(`Failed to cancel operation: ${err.message}`)
    }
  }

  const handleJumpToWord = async (word: TranscriptWord) => {
    if (!requirePinnedTimelineInteraction('previewing this transcript in Resolve')) {
      return
    }
    try {
      if (transcriptContext && (transcriptContext.clip_item_id || transcriptContext.file_path)) {
        const data = await apiFetch('/api/transcript-editor/jump-word', {
          method: 'POST',
          body: JSON.stringify({
            word: {
              start: word.start,
              end: word.end,
              start_frame: word.start_frame,
              end_frame: word.end_frame,
              source_start_frame: word.source_start_frame,
              source_end_frame: word.source_end_frame,
            },
            transcript_context: transcriptContext,
          }),
        })
        if (data?.success && typeof data.frame === 'number') {
          setWords((prev) =>
            prev.map((entry) =>
              entry === word
                ? {
                    ...entry,
                    start_frame: data.frame,
                    end_frame: typeof data.end_frame === 'number' ? data.end_frame : entry.end_frame,
                  }
                : entry
            )
          )
          return
        }
      }

      if (typeof word.start_frame === 'number') {
        await apiFetch('/api/resolve/playhead-frame', {
          method: 'POST',
          body: JSON.stringify({ frame: word.start_frame }),
        })
      } else {
        await apiFetch('/api/resolve/playhead', {
          method: 'POST',
          body: JSON.stringify({ seconds: word.start }),
        })
      }
    } catch {
      // Silently fail — don't interrupt the user's workflow
    }
  }

  const handlePlayPause = async () => {
    if (!requirePinnedTimelineInteraction('previewing this transcript in Resolve')) {
      return
    }
    // Optimistic toggle: immediately flip play state so interpolation starts/stops
    lastToggleTimeRef.current = Date.now()
    setIsPlaying((prev) => {
      if (!prev) {
        playheadSampleRef.current = {
          ...playheadSampleRef.current,
          sampledAt: performance.now(),
        }
        // Re-enable auto-follow when starting playback
        setIsFollowing(true)
      }
      staleCountRef.current = 0
      return !prev
    })

    try {
      await apiFetch('/api/resolve/transport', {
        method: 'POST',
        body: JSON.stringify({ action: isPlaying ? 'stop' : 'play' }),
      })
    } catch {
      // Silently fail
    }
  }

  const handleAnalyzeSpeakers = async ({ showToasts = true }: { showToasts?: boolean } = {}) => {
    if (words.length === 0) {
      return { speakerCount: 0 }
    }
    if (!requirePinnedTimelineInteraction('running speaker analysis on the current Resolve timeline')) {
      return null
    }
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before running speaker analysis.')) {
      return null
    }

    const spokenWords = words.filter((word) => !isAnalysisMarker(word))
    if (spokenWords.length === 0) {
      if (showToasts) {
        toast.info('No spoken words available for speaker analysis')
      }
      return { speakerCount: 0 }
    }

    useOperationStore.getState().startOperation('diarization')
    setAnalyzingSpeakers(true)
    setSpeakerSummaries([])

    try {
      const data = await apiFetch('/api/diarization/annotate-transcript', {
        method: 'POST',
        body: JSON.stringify({
          words: spokenWords.map((word) => ({
            word: word.word,
            start: word.start,
            end: word.end,
            start_frame: word.start_frame,
            end_frame: word.end_frame,
            source_start_frame: word.source_start_frame,
            source_end_frame: word.source_end_frame,
            probability: word.probability,
          })),
        }),
      })

      if (data?.error) {
        useOperationStore.getState().failOperation('diarization', data.error)
        toast.error(data.error)
        return { speakerCount: 0 }
      }

      let spokenIndex = 0
      const annotatedWords = words.map((word) => {
        if (isAnalysisMarker(word)) {
          return { ...word, speaker: undefined, speaker_index: undefined }
        }

        const annotated = data.words?.[spokenIndex]
        spokenIndex++
        const normalized = normalizeWord({
          ...word,
          ...annotated,
          action: word.action ?? 'keep',
          selected: word.selected ?? false,
        })
        return {
          ...normalized,
          action: word.action ?? normalized.action,
          selected: word.selected ?? normalized.selected,
        }
      })

      setWords(annotatedWords)
      setSpeakerSummaries(data.speakers || [])
      setShowSpeakers(true)
      setShowScriptPanel(false)
      useOperationStore.getState().completeOperation('diarization', data)
      const speakerCount =
        typeof data.speaker_count === 'number'
          ? data.speaker_count
          : Array.isArray(data.speakers)
            ? data.speakers.length
            : 0
      if (showToasts && data.method === 'local_fallback') {
        toast.info('Using local speaker separation fallback. No Hugging Face login is required.')
      }
      if (showToasts) {
        toast.success(
          speakerCount > 1
            ? `Detected ${speakerCount} speakers`
            : speakerCount === 1
              ? 'Detected 1 speaker'
              : 'No clear speakers detected'
        )
      }
      return { speakerCount }
    } catch (err: any) {
      useOperationStore.getState().failOperation('diarization', err.message)
      toast.error(err.message)
      return { speakerCount: 0 }
    } finally {
      setAnalyzingSpeakers(false)
    }
  }

  const applySpeakerAction = (speaker: string, mode: 'keepOnly' | 'cut' | 'keep') => {
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before changing transcript edits.')) {
      return
    }
    setWords((prev) =>
      prev.map((word) => {
        if (isAnalysisMarker(word) || !word.speaker) return word

        if (mode === 'keepOnly') {
          return word.speaker === speaker
            ? { ...word, action: 'keep' as WordAction }
            : { ...word, action: 'cut' as WordAction }
        }

        if (word.speaker !== speaker) return word
        return { ...word, action: mode === 'cut' ? 'cut' : 'keep' }
      })
    )
  }

  const handleWordMouseDown = (
    index: number,
    event: React.MouseEvent<HTMLSpanElement>
  ) => {
    if (event.button !== 0) return

    event.preventDefault()
    lastClickTimeRef.current = Date.now()

    const clickAction = heldClickActionRef.current
    if (clickAction && words[index] && !isSilenceMarker(words[index])) {
      if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before changing cut, mute, or keep states.')) {
        return
      }
      setWords((prev) =>
        prev.map((word, i) =>
          i === index ? { ...word, action: clickAction, selected: false } : word
        )
      )
      setSelectionAnchor(index)
      setIsDragging(false)
      setDragStart(null)
      void handleJumpToWord(words[index])
      return
    }

    const additive = event.metaKey || event.ctrlKey

    if (event.shiftKey && selectionAnchor !== null) {
      setSelectionRange(selectionAnchor, index, additive)
      setSelectionAnchor(index)
      void handleJumpToWord(words[index])
      return
    }

    if (additive) {
      setWords((prev) =>
        prev.map((word, i) =>
          i === index ? { ...word, selected: !word.selected } : word
        )
      )
      setSelectionAnchor(index)
      void handleJumpToWord(words[index])
      return
    }

    setSelectionRange(index, index)
    setSelectionAnchor(index)
    setDragStart(index)
    setIsDragging(true)
    void handleJumpToWord(words[index])
  }

  const handleWordMouseEnter = (index: number) => {
    if (!isDragging || dragStart === null) return
    setSelectionRange(dragStart, index)
  }

  const handleWordMouseUp = () => {
    setIsDragging(false)
    setDragStart(null)
  }

  const selectSentence = (index: number) => {
    let start = index
    let end = index

    while (start > 0 && !words[start - 1].word.match(/[.!?]$/)) start--
    while (end < words.length - 1 && !words[end].word.match(/[.!?]$/)) end++

    setSelectionRange(start, end)
    setSelectionAnchor(index)
  }

  const applyActionToSelection = (action: WordAction) => {
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before changing transcript edits.')) {
      return
    }
    setWords((prev) =>
      prev.map((word) =>
        word.selected && word.action !== action ? { ...word, action } : word
      )
    )
  }

  const handleSelectAllWords = () => {
    const allSelected = words.length > 0 && words.every((w) => w.selected)
    setWords((prev) => prev.map((word) => ({ ...word, selected: !allSelected })))
  }

  const handleKeepAll = () => {
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before resetting transcript edits.')) {
      return
    }
    storeRepetitionGroups([])
    setWords((prev) =>
      prev
        .filter((word) => !isAnalysisMarker(word))
        .map((word) => ({ ...word, action: 'keep' as WordAction }))
    )
    setDetectedFillerRanges([])
  }

  const handleClearTranscript = useCallback(() => {
    if (transcriptClearBlocked) return

    setShowClearTranscriptConfirm(false)
    pendingAutoDetectRef.current = false
    pendingAutoAnalyzeTranscriptWordsRef.current = null
    setPendingAutoAnalyzeFillers(false)
    staleNotifiedRef.current = false
    useOperationStore.getState().clearResult(TE_OP_NAME)

    setWords([])
    setResult(null)
    setTranscriptContext(null)
    setTranscriptSession(null)
    setSpeakerSummaries([])
    setWaveform(null)
    setDetectedFillerRanges([])
    setQuickAnalyzeSummary(null)
    setShowSilenceSettings(false)
    setShowScriptPanel(false)
    setShowSpeakers(false)
    setSelectionAnchor(null)
    setDragStart(null)
    setIsDragging(false)
    setActiveWordIndex(null)
    setPlayheadSeconds(0)
    setIsPlaying(false)
    storeTranscriptBackup(null)
    setPendingTranscriptRecovery(null)
    storeSilenceSegments([])
    storeRepetitionGroups([])
    useFeatureDataStore.getState().setTranscriptWords([])
    useFeatureDataStore.getState().setTranscriptContext(null)
    useFeatureDataStore.getState().setTranscriptSession(null)
    useFeatureDataStore.getState().setTranscriptSpeakers([])
    useFeatureDataStore.getState().setTranscriptWaveform(null)

    toast.success('Cleared the current transcript. You can transcribe again from a clean slate.')
  }, [
    transcriptClearBlocked,
    storeTranscriptBackup,
    setPendingTranscriptRecovery,
    storeSilenceSegments,
    storeRepetitionGroups,
  ])

  const analyzeFillers = async ({
    markForCut,
    showToasts,
    enforceFreshTranscript,
    transcriptWordsOverride,
  }: {
    markForCut: boolean
    showToasts: boolean
    enforceFreshTranscript: boolean
    transcriptWordsOverride?: TranscriptWord[]
  }) => {
    if (words.length === 0) {
      return { fillerCount: 0 }
    }
    if (!requirePinnedTimelineInteraction('running filler detection on the current Resolve timeline', showToasts)) {
      return null
    }
    if (
      enforceFreshTranscript &&
      !requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before finding fillers.')
    ) {
      return null
    }
    setFindingFillers(true)

    try {
      const baseWords = words.filter((word) => !isAudioFillerMarker(word))
      const analysisWords = transcriptWordsOverride ?? baseWords
      const transcriptPayload = {
        words: analysisWords
          .filter((word) => !isAnalysisMarker(word))
          .map((word) => ({
            word: word.word,
            start: word.start,
            end: word.end,
            start_frame: word.start_frame,
            end_frame: word.end_frame,
            source_start_frame: word.source_start_frame,
            source_end_frame: word.source_end_frame,
            probability: word.probability,
          })),
      }

      const data = await apiFetch('/api/filler-words/analyze', {
        method: 'POST',
        body: JSON.stringify({
          transcript: transcriptPayload,
          filler_words: fillerWords,
        }),
      })
      if (data?.error) {
        throw new Error(data.error)
      }

      const detections = data?.detections || []
      const applied = applyBackendFillerDetections(baseWords, detections, markForCut)
      setDetectedFillerRanges(applied.ranges)
      setWords(applied.words)

      const totalMarked = applied.totalMarked
      if (showToasts && totalMarked === 0) {
        toast.info('No filler words detected')
      } else if (showToasts) {
        toast.success(
          markForCut
            ? `Marked ${totalMarked} filler candidate${totalMarked !== 1 ? 's' : ''} for cutting`
            : `Detected ${totalMarked} filler candidate${totalMarked !== 1 ? 's' : ''}`
        )
      }
      return { fillerCount: totalMarked }
    } catch (err: any) {
      console.warn('Backend filler analysis failed, falling back to local transcript heuristic', err)
      let markedCount = 0
      let skippedCount = 0
      const localRanges: Segment[] = []

      setWords((prev) =>
        prev
          .filter((word) => !isAudioFillerMarker(word))
          .map((w, i, base) => {
          if (isAnalysisMarker(w) || w.action === 'cut') return w
          const prevWord = i > 0 ? base[i - 1] : null
          const nextWord = i < base.length - 1 ? base[i + 1] : null
          if (!isLikelyFillerWord(w, prevWord, nextWord)) return w

          const gapBefore = prevWord ? w.start - prevWord.end : Infinity
          const gapAfter = nextWord ? nextWord.start - w.end : Infinity

          if (gapBefore < 0.03 || gapAfter < 0.03) {
            skippedCount++
            return w
          }

          localRanges.push({
            start: w.start,
            end: w.end,
            start_frame: w.start_frame,
            end_frame: w.end_frame,
            source_start_frame: w.source_start_frame,
            source_end_frame: w.source_end_frame,
          })
          if (!markForCut) return w
          markedCount++
          return { ...w, action: 'cut' as WordAction }
        })
      )

      setDetectedFillerRanges(localRanges)

      if (!showToasts) {
        return { fillerCount: markForCut ? markedCount : localRanges.length }
      }

      const backendMessage =
        typeof err?.message === 'string' && err.message.length > 0
          ? ` Backend analysis failed: ${err.message}.`
          : ''

      if (markedCount === 0 && skippedCount > 0) {
        toast.info(`Found ${skippedCount} filler word${skippedCount !== 1 ? 's' : ''} but skipped — too close to dialogue.${backendMessage}`)
      } else if (markedCount === 0) {
        if (markForCut) {
          toast.info(`No filler words detected.${backendMessage}`)
        } else if (localRanges.length > 0) {
          toast.success(`Detected ${localRanges.length} filler candidate${localRanges.length !== 1 ? 's' : ''} from transcript text only.${backendMessage}`)
        } else {
          toast.info(`No filler words detected.${backendMessage}`)
        }
      } else {
        const skipMsg = skippedCount > 0 ? ` (${skippedCount} skipped to avoid harsh cuts)` : ''
        const sourceMsg = markForCut ? ' using transcript-text fallback' : ' from transcript text only'
        toast.success(`Marked ${markedCount} filler word${markedCount !== 1 ? 's' : ''} for cutting${skipMsg}${sourceMsg}.${backendMessage}`)
      }
      return { fillerCount: markForCut ? markedCount : localRanges.length }
    } finally {
      setFindingFillers(false)
    }
  }

  const handleFindFillers = async () => {
    await analyzeFillers({
      markForCut: true,
      showToasts: true,
      enforceFreshTranscript: true,
    })
  }

  const handleCutDetectedFillers = async () => {
    if (words.length === 0) return
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before cutting filler words.')) {
      return
    }

    const isHighlightedTranscriptFiller = (
      word: TranscriptWord,
      index: number,
      allWords: TranscriptWord[]
    ) => {
      if (isSilenceMarker(word)) return false
      if (isAudioFillerMarker(word)) return true
      if (!autoDetectFillers) return false
      if (detectedFillerRanges.some((segment) => wordOverlapsSegment(word, segment))) return true
      return isLikelyFillerWord(
        word,
        index > 0 ? allWords[index - 1] : null,
        index < allWords.length - 1 ? allWords[index + 1] : null
      )
    }

    const highlightedFillerCount = words.filter((word, index, allWords) =>
      isHighlightedTranscriptFiller(word, index, allWords)
    ).length

    if (highlightedFillerCount === 0) {
      toast.info('No filler words are currently highlighted in the transcript')
      return
    }

    let markedCount = 0
    setWords((prev) =>
      prev.map((word, index, allWords) => {
        const shouldCut = isHighlightedTranscriptFiller(word, index, allWords)

        if (!shouldCut || word.action === 'cut') return word
        markedCount++
        return { ...word, action: 'cut' as WordAction, selected: false }
      })
    )

    if (markedCount === 0) {
      toast.info('All highlighted filler candidates are already marked for cutting')
    } else {
      toast.success(`Marked ${markedCount} highlighted filler candidate${markedCount !== 1 ? 's' : ''} for cutting`)
    }
  }

  const handleMarkRepetitions = async ({ showToasts = true }: { showToasts?: boolean } = {}) => {
    if (words.length === 0) {
      return { groups: 0, takesMarked: 0 }
    }
    if (!requirePinnedTimelineInteraction('running retake detection on the current Resolve timeline')) {
      return null
    }
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before finding retakes.')) {
      return null
    }

    const spokenWords = words.filter((word) => !isAnalysisMarker(word))
    if (spokenWords.length < 6) {
      storeRepetitionGroups([])
      if (showToasts) {
        toast.info('Not enough transcript to detect retakes')
      }
      return { groups: 0, takesMarked: 0 }
    }

    setFindingRepetitions(true)

    try {
      const data = await apiFetch('/api/repetition/detect', {
        method: 'POST',
        body: JSON.stringify({
          transcript: {
            words: spokenWords.map((word) => ({
              word: word.word,
              start: word.start,
              end: word.end,
              start_frame: word.start_frame,
              end_frame: word.end_frame,
              source_start_frame: word.source_start_frame,
              source_end_frame: word.source_end_frame,
              probability: word.probability,
            })),
          },
        }),
      })

      if (data?.error) {
        throw new Error(data.error)
      }

      const groups = Array.isArray(data?.groups) ? (data.groups as RepetitionGroup[]) : []
      storeRepetitionGroups(groups)

      if (groups.length === 0) {
        if (showToasts) {
          toast.info('No retakes detected')
        }
        return { groups: 0, takesMarked: 0 }
      }

      const applied = applyRepetitionCuts(spokenWords, groups)
      if (applied.wordsMarked > 0) {
        setWords((prev) => replaceSpokenWords(prev, applied.words))
      }

      if (applied.takes === 0) {
        if (showToasts) {
          toast.info(`Detected ${groups.length} retake section${groups.length !== 1 ? 's' : ''}, but the losing takes were already marked for cutting`)
        }
        return { groups: groups.length, takesMarked: 0 }
      }

      if (showToasts) {
        toast.success(
          `Marked ${applied.takes} losing take${applied.takes !== 1 ? 's' : ''} across ${applied.groups} retake section${applied.groups !== 1 ? 's' : ''} for cutting`
        )
      }
      return { groups: applied.groups, takesMarked: applied.takes }
    } catch (err: any) {
      toast.error(err?.message || 'Failed to detect retakes')
      return { groups: 0, takesMarked: 0 }
    } finally {
      setFindingRepetitions(false)
    }
  }

  const handleFindSilences = async (
    options: {
      showTimelineSwitchToast?: boolean
      insertMarkers?: boolean
      showToasts?: boolean
    } = {}
  ) => {
    if (words.length < 2) {
      return { silenceCount: 0, silenceSeconds: 0 }
    }
    if (
      !requirePinnedTimelineInteraction(
        'running silence detection on the current Resolve timeline',
        options.showTimelineSwitchToast ?? true,
      )
    ) {
      return null
    }
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before finding silences.')) {
      return null
    }
    setFindingSilences(true)
    const insertMarkers = options.insertMarkers ?? true
    const showToasts = options.showToasts ?? true

    const fps = parseFloat(framerate) || 24
    const minGap = Math.max(silenceMinDuration / 1000, silenceMinFrames / fps)
    const pad = silencePadding / 1000 // convert ms to seconds
    const boundaryPadSeconds = Math.max(pad, SAFETY_PADDING_SECONDS)
    const boundaryPadFrames = Math.ceil(boundaryPadSeconds * fps)

    // First remove any previously inserted [silence] markers
    const cleanWords = words.filter((w) => !isSilenceMarker(w))

    try {
      // Call backend silence detection for audio-level analysis
      const data = await apiFetch('/api/silence/analyze', {
        method: 'POST',
        body: JSON.stringify({
          threshold_db: silenceThreshold,
          min_duration_ms: silenceMinDuration,
          min_duration_frames: silenceMinFrames,
          padding_ms: silencePadding,
        }),
      })

      const backendSilences: Array<{ start: number; end: number; duration: number; start_frame?: number; end_frame?: number }> =
        (data?.segments || []).filter((s: any) => s.duration >= minGap)

      // Also find gaps between transcript words as a fallback
      const gapSilences: Array<{ start: number; end: number; start_frame?: number; end_frame?: number }> = []
      for (let i = 0; i < cleanWords.length - 1; i++) {
        const gapStart = cleanWords[i].end
        const gapEnd = cleanWords[i + 1].start
        if (gapEnd - gapStart >= minGap) {
          gapSilences.push({
            start: gapStart,
            end: gapEnd,
            start_frame: cleanWords[i].end_frame,
            end_frame: cleanWords[i + 1].start_frame,
          })
        }
      }

      // Merge backend + gap silences: use all backend detections plus any
      // transcript gaps that don't overlap with backend detections
      const allSilences: Array<{ start: number; end: number; start_frame?: number; end_frame?: number }> = [
        ...backendSilences.map(s => ({
          start: s.start,
          end: s.end,
          start_frame: s.start_frame,
          end_frame: s.end_frame,
        })),
      ]
      for (const gap of gapSilences) {
        const overlaps = allSilences.some(
          s => gap.start < s.end + 0.1 && gap.end > s.start - 0.1
        )
        if (!overlaps) allSilences.push(gap)
      }
      allSilences.sort((a, b) => a.start - b.start)

      const persistedSilences = allSilences
        .map((silence) => ({
          start: silence.start,
          end: silence.end,
          duration: Math.max(0, silence.end - silence.start),
          start_frame: silence.start_frame,
          end_frame: silence.end_frame,
          selected: true,
        }))
        .filter((silence) => silence.duration >= minGap)
      storeSilenceSegments(persistedSilences)

      // Insert [silence] markers into the transcript at the right positions
      const newWords: TranscriptWord[] = []
      let silenceCount = 0
      let silenceTotal = 0
      let silIdx = 0

      for (let i = 0; i < cleanWords.length; i++) {
        newWords.push(cleanWords[i])

        // Check if any silence falls between this word and the next
        const wordEnd = cleanWords[i].end
        const nextWordStart = i < cleanWords.length - 1 ? cleanWords[i + 1].start : Infinity

        while (silIdx < allSilences.length && allSilences[silIdx].start < nextWordStart) {
          const sil = allSilences[silIdx]
          // Clip silence to the gap between words, with padding to protect dialogue
          const cutStart = Math.max(sil.start, wordEnd) + boundaryPadSeconds
          const cutEnd = Math.min(sil.end, nextWordStart) - boundaryPadSeconds
          if (cutEnd - cutStart >= minGap) {
            const gapStartFrame =
              typeof cleanWords[i].end_frame === 'number'
                ? cleanWords[i].end_frame
                : typeof sil.start_frame === 'number'
                  ? sil.start_frame
                  : undefined
            const gapEndFrame =
              i < cleanWords.length - 1 && typeof cleanWords[i + 1].start_frame === 'number'
                ? cleanWords[i + 1].start_frame
                : typeof sil.end_frame === 'number'
                  ? sil.end_frame
                  : undefined
            const adjustedStartFrame =
              typeof gapStartFrame === 'number' ? gapStartFrame + boundaryPadFrames : undefined
            const adjustedEndFrame =
              typeof gapEndFrame === 'number' ? gapEndFrame - boundaryPadFrames : undefined
            silenceCount++
            const cutDuration = cutEnd - cutStart
              silenceTotal += cutDuration
              newWords.push({
                word: '[silence]',
                kind: 'silence',
                start: cutStart,
                end: cutEnd,
              start_frame:
                typeof adjustedStartFrame === 'number' &&
                typeof adjustedEndFrame === 'number' &&
                adjustedEndFrame > adjustedStartFrame
                  ? adjustedStartFrame
                  : undefined,
              end_frame:
                typeof adjustedStartFrame === 'number' &&
                typeof adjustedEndFrame === 'number' &&
                adjustedEndFrame > adjustedStartFrame
                  ? adjustedEndFrame
                  : undefined,
              probability: 1,
              action: 'cut',
              selected: false,
            })
          }
          silIdx++
        }
      }

      if (insertMarkers) {
        setWords(silenceCount > 0 ? newWords : cleanWords)
      }

      if (!showToasts) {
        return { silenceCount, silenceSeconds: silenceTotal }
      }
      if (silenceCount === 0) {
        toast.info(`No silences found at ${silenceThreshold}dB / ${Math.round(minGap * 1000)}ms minimum`)
      } else {
        toast.success(
          insertMarkers
            ? `Marked ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} for cutting (${silenceTotal.toFixed(1)}s total)`
            : `Detected ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} in the background`
        )
      }
      return { silenceCount, silenceSeconds: silenceTotal }
    } catch {
      // Backend unavailable — fall back to gap-only detection
      const newWords: TranscriptWord[] = []
      let silenceCount = 0
      let silenceTotal = 0
      const fallbackSegments: Array<{
        start: number
        end: number
        duration: number
        start_frame?: number
        end_frame?: number
        selected: boolean
      }> = []

      for (let i = 0; i < cleanWords.length; i++) {
        const currentWord = cleanWords[i]
        if (!currentWord) continue
        newWords.push(currentWord)
        if (i < cleanWords.length - 1) {
          const nextWord = cleanWords[i + 1]
          if (!nextWord) continue
          const gapStart = currentWord.end
          const gapEnd = nextWord.start
          if (gapEnd - gapStart >= minGap) {
            const cutStart = gapStart + boundaryPadSeconds
            const cutEnd = gapEnd - boundaryPadSeconds
            if (cutEnd - cutStart >= minGap) {
              const adjustedStartFrame =
                typeof currentWord.end_frame === 'number'
                  ? currentWord.end_frame + boundaryPadFrames
                  : undefined
              const adjustedEndFrame =
                typeof nextWord.start_frame === 'number'
                  ? nextWord.start_frame - boundaryPadFrames
                  : undefined
              silenceCount++
              silenceTotal += cutEnd - cutStart
              fallbackSegments.push({
                start: cutStart,
                end: cutEnd,
                duration: cutEnd - cutStart,
                start_frame:
                  typeof adjustedStartFrame === 'number' &&
                  typeof adjustedEndFrame === 'number' &&
                  adjustedEndFrame > adjustedStartFrame
                    ? adjustedStartFrame
                    : undefined,
                end_frame:
                  typeof adjustedStartFrame === 'number' &&
                  typeof adjustedEndFrame === 'number' &&
                  adjustedEndFrame > adjustedStartFrame
                    ? adjustedEndFrame
                    : undefined,
                selected: true,
              })
              newWords.push({
                word: '[silence]',
                kind: 'silence',
                start: cutStart,
                end: cutEnd,
                start_frame:
                  typeof adjustedStartFrame === 'number' &&
                  typeof adjustedEndFrame === 'number' &&
                  adjustedEndFrame > adjustedStartFrame
                    ? adjustedStartFrame
                    : undefined,
                end_frame:
                  typeof adjustedStartFrame === 'number' &&
                  typeof adjustedEndFrame === 'number' &&
                  adjustedEndFrame > adjustedStartFrame
                    ? adjustedEndFrame
                    : undefined,
                probability: 1,
                action: 'cut',
                selected: false,
              })
            }
          }
        }
      }
      storeSilenceSegments(fallbackSegments)

      if (insertMarkers) {
        setWords(silenceCount > 0 ? newWords : cleanWords)
      }

      if (!showToasts) {
        return { silenceCount, silenceSeconds: silenceTotal }
      }
      if (silenceCount === 0) {
        toast.info(`No silences longer than ${silenceMinDuration}ms found`)
      } else {
        toast.success(
          insertMarkers
            ? `Marked ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} for cutting (${silenceTotal.toFixed(1)}s total)`
            : `Detected ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} in the background`
        )
      }
      return { silenceCount, silenceSeconds: silenceTotal }
    } finally {
      setFindingSilences(false)
    }
  }

  const handleQuickAnalyze = async () => {
    if (quickAnalyzeRunning || words.length === 0) return
    if (!requirePinnedTimelineInteraction('running Quick Analyze on the current Resolve timeline')) {
      return
    }
    if (!requireFreshTranscript('This transcript is out of date. Re-transcribe or sync it before running Quick Analyze.')) {
      return
    }

    setQuickAnalyzeSummary(null)

    try {
      setQuickAnalyzeStep('silences')
      const silenceResult = await handleFindSilences({
        showTimelineSwitchToast: false,
        insertMarkers: true,
        showToasts: false,
      })

      setQuickAnalyzeStep('fillers')
      const fillerResult = await analyzeFillers({
        markForCut: true,
        showToasts: false,
        enforceFreshTranscript: false,
      })

      setQuickAnalyzeStep('retakes')
      const repetitionResult = await handleMarkRepetitions({ showToasts: false })

      setQuickAnalyzeStep('speakers')
      const speakerResult = await handleAnalyzeSpeakers({ showToasts: false })

      const summary: QuickAnalyzeSummary = {
        silenceCount: silenceResult?.silenceCount ?? 0,
        silenceSeconds: silenceResult?.silenceSeconds ?? 0,
        fillerCount: fillerResult?.fillerCount ?? 0,
        retakeGroups: repetitionResult?.groups ?? 0,
        retakeTakes: repetitionResult?.takesMarked ?? 0,
        speakerCount: speakerResult?.speakerCount ?? 0,
        completedAt: Date.now(),
      }

      if (summary.silenceCount > 0) {
        setShowSilenceSettings(true)
      }

      setQuickAnalyzeSummary(summary)

      const summaryParts = [
        `${summary.silenceCount} silence${summary.silenceCount !== 1 ? 's' : ''}`,
        `${summary.fillerCount} filler${summary.fillerCount !== 1 ? 's' : ''}`,
        `${summary.retakeTakes} losing take${summary.retakeTakes !== 1 ? 's' : ''}`,
      ]
      if (summary.speakerCount > 0) {
        summaryParts.push(`${summary.speakerCount} speaker${summary.speakerCount !== 1 ? 's' : ''}`)
      }

      toast.success(`Quick Analyze complete: ${summaryParts.join(', ')}`)
    } finally {
      setQuickAnalyzeStep('idle')
    }
  }

  const handleTranscriptFreshnessFailure = useCallback((data: any) => {
    if (!data?.code) return false
    if (data.code !== 'TRANSCRIPT_STALE' && data.code !== 'TRANSCRIPT_SESSION_REQUIRED') {
      return false
    }

    if (data.code === 'TRANSCRIPT_SESSION_REQUIRED') {
      setTranscriptSession((prev) => prev ?? {
        session_id: 'legacy-transcript-session',
        timeline_name: null,
        timeline_signature: '',
        last_synced_at: new Date().toISOString(),
        sync_state: 'requires_retranscribe',
        stale_reason: 'signature_mismatch',
        origin: 'transcribe',
      })
    } else {
      markTranscriptStale('external_timeline_change')
    }

    return true
  }, [markTranscriptStale])

  const handleSyncWithoutRetranscribe = async () => {
    if (!requirePinnedTimelineInteraction('syncing this transcript to the current Resolve timeline')) {
      return
    }
    if (!transcriptSession) {
      toast.error('This transcript is missing freshness metadata. Re-transcribe before syncing it.')
      return
    }

    setIsReconciling(true)
    setResult(null)

    try {
      const data = await apiFetch('/api/transcript-editor/reconcile', {
        method: 'POST',
        body: JSON.stringify({
          words,
          transcript_context: transcriptContext,
          transcript_session: transcriptSession,
        }),
      })

      if (data?.success) {
        const normalizedWords = ((data.words || []) as any[])
          .map((word) => normalizeWord(word))
          .map((word) => ({
            ...word,
            speaker: undefined,
            speaker_index: undefined,
          }))
          .filter((word) => !isAnalysisMarker(word))
        setWords(sortTranscriptWords(normalizedWords))
        setTranscriptContext(data.transcript_context || transcriptContext)
        setTranscriptSession(data.transcript_session || transcriptSession)
        setDetectedFillerRanges([])
        setSpeakerSummaries([])
        storeRepetitionGroups([])
        setQuickAnalyzeSummary(null)
        staleNotifiedRef.current = false
        toast.success(data.message || 'Transcript synced to the current timeline. Re-run fillers, silences, and speakers if needed.')
        return
      }

      if (data?.transcript_session) {
        setTranscriptSession(data.transcript_session)
      } else {
        setTranscriptSession((prev) => prev ? {
          ...prev,
          sync_state: 'requires_retranscribe',
          stale_reason: data?.reason === 'ambiguous_sync' ? 'ambiguous_sync' : 'content_changed',
        } : prev)
      }
      staleNotifiedRef.current = true
      toast.error(data?.message || 'This timeline changed in a way that requires a fresh transcription.')
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setIsReconciling(false)
    }
  }

  const handleRetranscribeCurrentTimeline = () => {
    void handleTranscribe()
  }

  const handleApply = async () => {
    if (!requirePinnedTimelineInteraction('applying transcript edits to the current Resolve timeline')) {
      return
    }
    if (!requireFreshTranscript('This timeline changed after the transcript was created. Re-transcribe or sync it before applying transcript edits.')) {
      return
    }
    setApplying(true)
    setResult(null)

    try {
      const fps = parseFloat(framerate) || 24
      const rawFillerCutSegments = mergeWordSegments(
        words,
        (word) => word.action === 'cut' && !isSilenceMarker(word) && isWordFiller(word),
        { kind: 'filler' }
      )
      const rawSpeechCutSegments = mergeWordSegments(
        words,
        (word) => word.action === 'cut' && !isSilenceMarker(word) && !isWordFiller(word),
        { kind: 'speech' }
      )
      const rawSilenceCutSegments = mergeWordSegments(
        words,
        (word) => word.action === 'cut' && isSilenceMarker(word)
      )
      const mutedSegments = mergeWordSegments(words, (word) => word.action === 'mute')

      const silenceCutSegments = rawSilenceCutSegments.filter((seg) => seg.end - seg.start > 0.02)
      const cutSegments = [...rawFillerCutSegments, ...rawSpeechCutSegments, ...silenceCutSegments].sort((a, b) => a.start - b.start)

      const data = await apiFetch('/api/transcript-editor/apply', {
        method: 'POST',
        body: JSON.stringify({
          cut_segments: cutSegments,
          muted_segments: mutedSegments,
          transcript_context: transcriptContext,
          transcript_session: transcriptSession,
        }),
      })
      if (handleTranscriptFreshnessFailure(data)) {
        setResult(data)
        if (data?.error) toast.error(data.error)
        return
      }
      if (data?.success) {
        const nextContext = data.updated_transcript_context || transcriptContext
        const nextTimelineStartFrame =
          typeof nextContext?.clip_start_frame === 'number' ? nextContext.clip_start_frame : undefined
        const appliedCutSegments = normalizeAppliedCutSegments((data.applied_cut_segments || cutSegments) as any[])
        setWords((prev) => retimeWordsAfterCuts(prev, appliedCutSegments, fps, nextTimelineStartFrame))
        setSpeakerSummaries([])
        setDetectedFillerRanges([])
        storeRepetitionGroups([])
        setQuickAnalyzeSummary(null)
        if (nextContext) {
          setTranscriptContext(nextContext)
        }
        if (data.transcript_session) {
          setTranscriptSession(data.transcript_session)
          staleNotifiedRef.current = false
        }
        setResult({
          ...data,
          message: data.message || 'Edits applied successfully',
        })
        toast.success(data.message || 'Edits applied successfully')
      } else {
        setResult(data)
        if (data?.error) toast.error(data.error)
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleWaveformClick = (seconds: number) => {
    if (!requirePinnedTimelineInteraction('previewing this transcript in Resolve')) {
      return
    }
    // Immediately snap local playhead so the UI feels instant
    lastClickTimeRef.current = Date.now()
    playheadSampleRef.current = { seconds, sampledAt: performance.now() }
    setPlayheadSeconds(seconds)
    setIsFollowing(false)
    setActiveWordIndex(findActiveWordIndex(words, seconds))

    // Debounced Resolve seek — only send final position (prevents flooding during drag)
    if (waveformSeekDebounceRef.current) clearTimeout(waveformSeekDebounceRef.current)
    waveformSeekDebounceRef.current = setTimeout(() => {
      waveformSeekDebounceRef.current = null
      apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds: playheadSampleRef.current.seconds }),
      }).catch(() => {})
    }, 80)
  }

  const fileInputRef = useRef<HTMLInputElement>(null)

  const applyScriptEdits = async (editedText: string, sourceLabel = 'Script') => {
    if (!requirePinnedTimelineInteraction(`applying ${sourceLabel.toLowerCase()} edits to the current Resolve timeline`)) {
      return
    }
    if (!requireFreshTranscript('This timeline changed after the transcript was created. Re-transcribe or sync it before applying script edits.')) {
      return
    }
    const cleanedText = editedText.replace(/\[\d+:\d{2}(:\d{2})?\]/g, '').trim()
    const editedWords = cleanedText.split(/\s+/).filter((w) => w.length > 0)
    const scriptableWords = words.filter((w) => !isAnalysisMarker(w))
    const originalWords = scriptableWords.map((w) => w.word)

    if (scriptableWords.length === 0) {
      toast.error('No spoken transcript words available for script matching')
      return
    }

    if (editedWords.length === 0) {
      toast.error(`${sourceLabel} is empty`)
      return
    }

    const kept = lcsWordDiff(originalWords, editedWords)
    const keptCount = kept.filter((k) => k).length
    const removedCount = kept.filter((k) => !k).length

    if (removedCount === 0) {
      toast.info('No differences found — script matches')
      return
    }

    const matchRate = keptCount / originalWords.length
    if (matchRate < 0.1) {
      toast.error(
        `${sourceLabel} aborted: only ${keptCount} of ${originalWords.length} words matched. ` +
          'The script may have formatting differences that prevent matching.'
      )
      return
    }

    const scriptWords = scriptableWords.map((w, idx) => ({
      ...w,
      action: kept[idx] ? ('keep' as WordAction) : ('cut' as WordAction),
    }))
    const cutSegments = mergeWordSegments(scriptWords, (w) => w.action === 'cut', { kind: 'script' })
    const cutWordsList = scriptableWords.filter((_, idx) => !kept[idx])
    const cutSeconds = cutWordsList.reduce((sum, w) => sum + (w.end - w.start), 0)

    toast.info(`${sourceLabel}: cutting ${removedCount} words (${cutSeconds.toFixed(1)}s)...`)

    setApplying(true)
    setResult(null)
    try {
      const fps = parseFloat(framerate) || 24
      const data = await apiFetch('/api/transcript-editor/apply', {
        method: 'POST',
        body: JSON.stringify({
          cut_segments: cutSegments,
          muted_segments: [],
          transcript_context: transcriptContext,
          transcript_session: transcriptSession,
        }),
      })
      if (handleTranscriptFreshnessFailure(data)) {
        setResult(data)
        if (data?.error) toast.error(data.error)
        return
      }
      if (data?.success) {
        const nextContext = data.updated_transcript_context || transcriptContext
        const nextTimelineStartFrame =
          typeof nextContext?.clip_start_frame === 'number' ? nextContext.clip_start_frame : undefined
        const appliedCutSegments = normalizeAppliedCutSegments((data.applied_cut_segments || cutSegments) as any[])
        setWords(retimeWordsAfterCuts(scriptWords, appliedCutSegments, fps, nextTimelineStartFrame))
        setSpeakerSummaries([])
        setDetectedFillerRanges([])
        setQuickAnalyzeSummary(null)
        if (nextContext) {
          setTranscriptContext(nextContext)
        }
        if (data.transcript_session) {
          setTranscriptSession(data.transcript_session)
          staleNotifiedRef.current = false
        }
        setScriptText(cleanedText)
        setResult({
          ...data,
          message: data.message || `${sourceLabel} edits applied successfully`,
        })
        toast.success(data.message || `${sourceLabel} edits applied successfully`)
      } else {
        setResult(data)
        if (data?.error) toast.error(data.error)
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleExportTranscript = () => {
    if (words.length === 0) return

    // Build text with timestamps injected every ~10 seconds
    const lines: string[] = []
    let currentLine = ''
    let lastTimestamp = -Infinity

    // Filter out cut words and analysis markers from export.
    const keptWords = words.filter((w) => w.action !== 'cut' && !isAnalysisMarker(w))

    for (const word of keptWords) {
      // Inject a timestamp marker when >=10s have passed since the last one
      if (word.start - lastTimestamp >= 10 || lastTimestamp === -Infinity) {
        // Flush current line if it has content
        if (currentLine.trim()) {
          lines.push(currentLine.trim())
          currentLine = ''
        }
        lines.push('')
        lines.push(`[${formatTimestamp(word.start)}]`)
        lastTimestamp = word.start
      }
      currentLine += word.word + ' '

      // Also break at sentence endings for readability
      if (word.word.match(/[.!?]$/)) {
        lines.push(currentLine.trim())
        currentLine = ''
      }
    }

    // Flush remaining
    if (currentLine.trim()) {
      lines.push(currentLine.trim())
    }

    const text = lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()

    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'transcript.txt'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success('Transcript exported')
  }

  const handleImportTranscript = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = async (e) => {
      const editedText = (e.target?.result as string) || ''
      await applyScriptEdits(editedText, 'Imported script')
    }
    reader.readAsText(file)

    // Reset input so same file can be re-imported
    event.target.value = ''
  }

  const selectedCount = words.filter((word) => word.selected).length
  const cutCount = words.filter((word) => word.action === 'cut').length
  const muteCount = words.filter((word) => word.action === 'mute').length
  const cutSeconds = words
    .filter((word) => word.action === 'cut')
    .reduce((acc, word) => acc + (word.end - word.start), 0)
  const muteSeconds = words
    .filter((word) => word.action === 'mute')
    .reduce((acc, word) => acc + (word.end - word.start), 0)
  const totalSeconds =
    words.length > 0 ? words[words.length - 1].end - words[0].start : 0
  const keptSeconds = Math.max(0, totalSeconds - cutSeconds)
  const hasEdits = cutCount > 0 || muteCount > 0
  const effectiveMinSilenceMs = Math.max(
    silenceMinDuration,
    Math.round((silenceMinFrames / fpsValue) * 1000)
  )
  const primarySpeaker = speakerSummaries.find((speaker) => speaker.is_primary_suggestion)
  const quickAnalyzeStepLabel =
    quickAnalyzeStep === 'silences'
      ? 'Finding silences'
      : quickAnalyzeStep === 'fillers'
        ? 'Marking filler words'
        : quickAnalyzeStep === 'retakes'
          ? 'Finding retakes'
          : quickAnalyzeStep === 'speakers'
            ? 'Separating speakers'
            : 'Quick Analyze'
  const quickAnalyzeCompletedLabel = quickAnalyzeSummary
    ? new Date(quickAnalyzeSummary.completedAt).toLocaleTimeString([], {
        hour: 'numeric',
        minute: '2-digit',
      })
    : null



  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Transcript Editor</h2>
        <p className="feature-description">
          Edit your video by editing text. Select words and mark them as cut or mute. Press spacebar to play/pause.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
          className="bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
        >
          <option value="">Auto-detect</option>
          <option value="en">English</option>
          <option value="es">Spanish</option>
          <option value="fr">French</option>
          <option value="de">German</option>
          <option value="ja">Japanese</option>
          <option value="ko">Korean</option>
          <option value="zh">Chinese</option>
          <option value="pt">Portuguese</option>
          <option value="it">Italian</option>
        </select>
        {transcribing ? (
          abortRef.current ? (
            <Button onClick={handleCancel} variant="ghost">
              Cancel
            </Button>
          ) : (
            <Button disabled variant="ghost">
              Transcribing...
            </Button>
          )
        ) : (
          <>
            <Button onClick={() => handleTranscribe()}>
              {words.length > 0 ? 'Re-Transcribe' : 'Transcribe'}
            </Button>
            <Button
              onClick={handleTranscribeSelection}
              variant="ghost"
              title="Transcribe only the in/out range set on your DaVinci Resolve timeline (I and O keys)"
            >
              Transcribe Selection
            </Button>
          </>
        )}
      </div>

      {transcribing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}
      {analyzingSpeakers && (
        <ProgressBar progress={speakerProgress} message={speakerMessage} elapsed={speakerElapsed} eta={speakerEta} />
      )}
      {words.length > 0 && transcriptIsStale && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-medium text-amber-200">
                This timeline changed after this transcript was created. Re-transcribe before applying more transcript edits.
              </div>
              <div className="mt-1 text-xs text-amber-100/80">
                {transcriptSession?.sync_state === 'requires_retranscribe'
                  ? 'This timeline changed in a way that requires a fresh transcription.'
                  : 'To keep transcript edits safe, either re-transcribe the current timeline or try to sync this transcript to the current timeline.'}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={handleRetranscribeCurrentTimeline}>
                Re-Transcribe
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void handleSyncWithoutRetranscribe()} loading={isReconciling}>
                Try Sync Without Re-Transcribe
              </Button>
            </div>
          </div>
        </Card>
      )}

      {/* Error/success result cards */}
      {result?.error && (
        <Card className="border-resolve-error/30">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <p className="text-sm text-resolve-error flex-1">
              {operationBlocked
                ? 'A previous operation got stuck and is blocking new requests.'
                : result.error}
            </p>
            {operationBlocked && (
              <Button onClick={handleForceCancel} variant="danger" size="sm">
                Force Cancel
              </Button>
            )}
          </div>
        </Card>
      )}

      {words.length > 0 && (
        <>
          <TranscriptToolbar
            isPlaying={isPlaying}
            isFollowing={isFollowing}
            showTimestamps={showTimestamps}
            onPlayPause={handlePlayPause}
            onToggleTimestamps={() => setShowTimestamps((t) => !t)}
            selectedCount={selectedCount}
            onCutSelection={() => applyActionToSelection('cut')}
            onMuteSelection={() => applyActionToSelection('mute')}
            onKeepSelection={() => applyActionToSelection('keep')}
            onFindFillers={handleFindFillers}
            findingFillers={findingFillers}
            onCutFillers={() => void handleCutDetectedFillers()}
            hasDetectedFillers={hasDetectedFillers}
            onMarkRepetitions={() => void handleMarkRepetitions()}
            findingRepetitions={findingRepetitions}
            onQuickAnalyze={() => void handleQuickAnalyze()}
            quickAnalyzeRunning={quickAnalyzeRunning}
            quickAnalyzeLabel={quickAnalyzeStepLabel}
            autoDetectFillers={autoDetectFillers}
            onToggleAutoDetect={() => setAutoDetectFillers(f => !f)}
            fillerCount={fillerCount}
            repeatedCount={repeatedCount}
            onToggleSilenceSettings={() => setShowSilenceSettings(s => !s)}
            showSilenceSettings={showSilenceSettings}
            onSpeakers={() => {
              if (speakerSummaries.length > 0) {
                setShowSpeakers((s) => !s)
              } else {
                handleAnalyzeSpeakers()
              }
            }}
            analyzingSpeakers={analyzingSpeakers}
            showSpeakers={showSpeakers && speakerSummaries.length > 0}
            onToggleScriptPanel={() => setShowScriptPanel((open) => !open)}
            showScriptPanel={showScriptPanel}
            onClearTranscript={() => setShowClearTranscriptConfirm(true)}
            clearTranscriptDisabled={transcriptClearBlocked}
            allSelected={words.length > 0 && words.every((w) => w.selected)}
            onSelectAll={handleSelectAllWords}
            onKeepAll={handleKeepAll}
            cutCount={cutCount}
            muteCount={muteCount}
            heldClickAction={heldClickAction}
            primarySpeaker={primarySpeaker ?? null}
            onExport={handleExportTranscript}
            transcriptMutationBlocked={transcriptMutationBlocked}
          />

          {(quickAnalyzeRunning || quickAnalyzeSummary) && (
            <Card className="border-resolve-accent/20 bg-resolve-accent/5">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-resolve-text">Quick Analyze</div>
                  {quickAnalyzeRunning ? (
                    <div className="mt-1 text-xs text-resolve-textMuted">
                      {quickAnalyzeStepLabel}. FinlayCut is running the default cleanup pass on this transcript.
                    </div>
                  ) : quickAnalyzeSummary ? (
                    <div className="mt-1 text-xs text-resolve-textMuted">
                      Last run at {quickAnalyzeCompletedLabel}. Review the marked transcript edits, then apply them to Resolve when you are satisfied.
                    </div>
                  ) : null}
                </div>
                {quickAnalyzeSummary && !quickAnalyzeRunning && (
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full border border-resolve-border bg-resolve-bg px-2 py-1 text-resolve-textMuted">
                      {quickAnalyzeSummary.silenceCount} silence{quickAnalyzeSummary.silenceCount !== 1 ? 's' : ''}
                      {quickAnalyzeSummary.silenceSeconds > 0 ? ` (${quickAnalyzeSummary.silenceSeconds.toFixed(1)}s)` : ''}
                    </span>
                    <span className="rounded-full border border-resolve-border bg-resolve-bg px-2 py-1 text-resolve-textMuted">
                      {quickAnalyzeSummary.fillerCount} filler{quickAnalyzeSummary.fillerCount !== 1 ? 's' : ''}
                    </span>
                    <span className="rounded-full border border-resolve-border bg-resolve-bg px-2 py-1 text-resolve-textMuted">
                      {quickAnalyzeSummary.retakeTakes} losing take{quickAnalyzeSummary.retakeTakes !== 1 ? 's' : ''} across {quickAnalyzeSummary.retakeGroups} retake section{quickAnalyzeSummary.retakeGroups !== 1 ? 's' : ''}
                    </span>
                    <span className="rounded-full border border-resolve-border bg-resolve-bg px-2 py-1 text-resolve-textMuted">
                      {quickAnalyzeSummary.speakerCount} speaker{quickAnalyzeSummary.speakerCount !== 1 ? 's' : ''}
                    </span>
                  </div>
                )}
              </div>
            </Card>
          )}

          {showSilenceSettings && (
            <SilenceSettingsPanel
              silenceThreshold={silenceThreshold}
              silenceMinDuration={silenceMinDuration}
              silenceMinFrames={silenceMinFrames}
              silencePadding={silencePadding}
              onThresholdChange={setSilenceThreshold}
              onMinDurationChange={setSilenceMinDuration}
              onMinFramesChange={setSilenceMinFrames}
              onPaddingChange={setSilencePadding}
              effectiveMinSilenceMs={effectiveMinSilenceMs}
              framerate={framerate}
              findingSilences={findingSilences}
              onFindSilences={handleFindSilences}
              transcriptMutationBlocked={transcriptMutationBlocked}
            />
          )}
          {showSpeakers && speakerSummaries.length > 0 && (
            <SpeakerPanel
              speakers={speakerSummaries}
              onSpeakerAction={applySpeakerAction}
              transcriptMutationBlocked={transcriptMutationBlocked}
            />
          )}
          {showScriptPanel && (
            <ScriptCutPanel
              scriptText={scriptText}
              onScriptTextChange={setScriptText}
              onApplyScript={() => applyScriptEdits(scriptText, 'Script cut')}
              transcriptIsStale={transcriptIsStale}
              transcriptMutationBlocked={transcriptMutationBlocked}
            />
          )}

          {waveform && (
            <WaveformCard
              waveform={waveform}
              words={words}
              viewport={effectiveViewport}
              cutBoundaries={cutBoundaries}
              playheadSeconds={playheadSeconds}
              framerate={framerate}
              onTimeClick={handleWaveformClick}
              onBoundaryDragStart={handleBoundaryDragStart}
              onBoundaryDrag={handleBoundaryDrag}
              onBoundaryDragEnd={handleBoundaryDragEnd}
              onViewportChange={handleViewportChange}
              onResetZoom={handleResetZoom}
            />
          )}

          <Card className="!p-0">
            <div
              ref={transcriptContainerRef}
              className="p-4 select-none break-words leading-7 sm:leading-8"
              style={{ userSelect: 'none' }}
            >
              <TranscriptWords
                words={words}
                activeWordIndex={activeWordIndex}
                activeWordRef={activeWordRef}
                showTimestamps={showTimestamps}
                framerate={framerate}
                autoDetectFillers={autoDetectFillers}
                isWordFiller={isWordFiller}
                isWordRepeated={isWordRepeated}
                onWordMouseDown={handleWordMouseDown}
                onWordMouseEnter={handleWordMouseEnter}
                onWordMouseUp={handleWordMouseUp}
                onDoubleClick={selectSentence}
              />
            </div>
          </Card>

          <TranscriptFooter
            keptSeconds={keptSeconds}
            totalSeconds={totalSeconds}
            muteSeconds={muteSeconds}
            cutCount={cutCount}
            muteCount={muteCount}
            cutSeconds={cutSeconds}
            hasEdits={hasEdits}
            applying={applying}
            transcriptMutationBlocked={transcriptMutationBlocked}
            onApply={handleApply}
          />
        </>
      )}

      <Modal
        open={showClearTranscriptConfirm}
        onClose={() => setShowClearTranscriptConfirm(false)}
        title="Clear Current Transcript"
      >
        <div className="space-y-4">
          <p className="text-sm text-resolve-textMuted">
            This clears the current transcript, waveform, and transcript-only analysis so you can re-transcribe from a clean slate.
            It does not change anything on your Resolve timeline.
          </p>
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setShowClearTranscriptConfirm(false)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" onClick={handleClearTranscript}>
              Clear Transcript
            </Button>
          </div>
        </div>
      </Modal>

    </div>
  )
}

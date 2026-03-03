import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { Slider } from '../common/Slider'
import { useProjectStore } from '../../stores/projectStore'
import { ProgressBar } from '../layout/ProgressBar'
import { ConfirmApply } from '../common/TimelineGuard'
import { WaveformDisplay } from './WaveformDisplay'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

type WordAction = 'keep' | 'cut' | 'mute'

interface TranscriptWord {
  word: string
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
  probability: number
  action: WordAction
  selected: boolean
  deleted?: boolean
}

interface Segment {
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
}

interface TranscriptContext {
  clip_item_id?: string
  file_path?: string
  clip_start_frame?: number
  clip_end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
}

const MERGE_GAP_SECONDS = 0.05
const SAFETY_PADDING_SECONDS = 0.08 // 80ms — protects dialogue from being clipped at cut boundaries

/** Unambiguous filler words only. No common words like "so", "like", "right", "a". */
const FILLER_WORDS = new Set([
  'um', 'uh', 'uh-huh', 'uhh', 'uhm', 'umm',
  'hmm', 'mmhmm', 'mhm', 'mm',
  'huh', 'er', 'erm', 'ah', 'ahh',
])

/**
 * LCS-based diff: finds which original words were removed in the edited text.
 * Returns an array of booleans — true if the word at that index was kept.
 */
function lcsWordDiff(originalWords: string[], editedWords: string[]): boolean[] {
  const m = originalWords.length
  const n = editedWords.length

  // Build LCS table
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (normalizeText(originalWords[i - 1]) === normalizeText(editedWords[j - 1])) {
        dp[i][j] = dp[i - 1][j - 1] + 1
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1])
      }
    }
  }

  // Backtrack to find which original words are in the LCS
  const kept = new Array(m).fill(false)
  let i = m
  let j = n
  while (i > 0 && j > 0) {
    if (normalizeText(originalWords[i - 1]) === normalizeText(editedWords[j - 1])) {
      kept[i - 1] = true
      i--
      j--
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--
    } else {
      j--
    }
  }

  return kept
}

function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'") // smart single quotes → straight
    .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"') // smart double quotes → straight
    .replace(/[\u2013\u2014\u2015]/g, '-') // en/em dash → hyphen
    .replace(/\u2026/g, '...') // ellipsis character → dots
    .replace(/[.,!?;:'"()\-\[\]{}]/g, '') // remove punctuation
    .trim()
}

function normalizeWord(raw: any): TranscriptWord {
  const rawAction = typeof raw?.action === 'string' ? raw.action : null
  const action: WordAction =
    rawAction === 'cut' || rawAction === 'mute' || rawAction === 'keep'
      ? rawAction
      : raw?.deleted
        ? 'cut'
        : 'keep'

  return {
    word: String(raw?.word || ''),
    start: Number(raw?.start || 0),
    end: Number(raw?.end || 0),
    start_frame:
      typeof raw?.start_frame === 'number' ? raw.start_frame : undefined,
    end_frame: typeof raw?.end_frame === 'number' ? raw.end_frame : undefined,
    source_start_frame:
      typeof raw?.source_start_frame === 'number' ? raw.source_start_frame : undefined,
    source_end_frame:
      typeof raw?.source_end_frame === 'number' ? raw.source_end_frame : undefined,
    probability: Number(raw?.probability || 0),
    action,
    selected: Boolean(raw?.selected),
  }
}

function mergeWordSegments(
  words: TranscriptWord[],
  include: (word: TranscriptWord) => boolean
): Segment[] {
  const segments: Segment[] = []
  let currentStart: number | null = null
  let currentEnd: number | null = null
  let currentStartFrame: number | undefined
  let currentEndFrame: number | undefined
  let currentSourceStartFrame: number | undefined
  let currentSourceEndFrame: number | undefined

  for (const word of words) {
    if (include(word)) {
      if (currentStart === null || currentEnd === null) {
        currentStart = word.start
        currentEnd = word.end
        currentStartFrame =
          typeof word.start_frame === 'number' ? word.start_frame : undefined
        currentEndFrame =
          typeof word.end_frame === 'number' ? word.end_frame : undefined
        currentSourceStartFrame =
          typeof word.source_start_frame === 'number' ? word.source_start_frame : undefined
        currentSourceEndFrame =
          typeof word.source_end_frame === 'number' ? word.source_end_frame : undefined
      } else if (word.start - currentEnd <= MERGE_GAP_SECONDS) {
        currentEnd = Math.max(currentEnd, word.end)
        if (typeof word.end_frame === 'number') {
          currentEndFrame = word.end_frame
        }
        if (typeof word.source_end_frame === 'number') {
          currentSourceEndFrame = word.source_end_frame
        }
      } else {
        segments.push({
          start: currentStart,
          end: currentEnd,
          start_frame: currentStartFrame,
          end_frame: currentEndFrame,
          source_start_frame: currentSourceStartFrame,
          source_end_frame: currentSourceEndFrame,
        })
        currentStart = word.start
        currentEnd = word.end
        currentStartFrame =
          typeof word.start_frame === 'number' ? word.start_frame : undefined
        currentEndFrame =
          typeof word.end_frame === 'number' ? word.end_frame : undefined
        currentSourceStartFrame =
          typeof word.source_start_frame === 'number' ? word.source_start_frame : undefined
        currentSourceEndFrame =
          typeof word.source_end_frame === 'number' ? word.source_end_frame : undefined
      }
    } else if (currentStart !== null && currentEnd !== null) {
      segments.push({
        start: currentStart,
        end: currentEnd,
        start_frame: currentStartFrame,
        end_frame: currentEndFrame,
        source_start_frame: currentSourceStartFrame,
        source_end_frame: currentSourceEndFrame,
      })
      currentStart = null
      currentEnd = null
      currentStartFrame = undefined
      currentEndFrame = undefined
      currentSourceStartFrame = undefined
      currentSourceEndFrame = undefined
    }
  }

  if (currentStart !== null && currentEnd !== null) {
    segments.push({
      start: currentStart,
      end: currentEnd,
      start_frame: currentStartFrame,
      end_frame: currentEndFrame,
      source_start_frame: currentSourceStartFrame,
      source_end_frame: currentSourceEndFrame,
    })
  }

  return segments
}

const TE_OP_NAME = 'transcript_editor'

export function TranscriptEditor() {
  const {
    transcriptWords: storedWords,
    transcriptLanguage: storedLanguage,
    transcriptContext: storedContext,
    transcriptWaveform: storedWaveform,
    silenceSettings: storedSilenceSettings,
    setTranscriptWords: storeWords,
    setTranscriptLanguage: storeLanguage,
    setTranscriptContext: storeContext,
    setTranscriptWaveform: storeWaveform,
    setSilenceSettings: storeSilenceSettings,
  } = useFeatureDataStore()

  const teOp = useOperationStore((s) => s.operations[TE_OP_NAME])
  const transcribing = teOp?.status === 'running'

  const [words, setWords] = useState<TranscriptWord[]>(
    storedWords.map((word) => normalizeWord(word))
  )
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [language, setLanguage] = useState(storedLanguage)
  const [transcriptContext, setTranscriptContext] = useState<TranscriptContext | null>(storedContext)
  const [selectionAnchor, setSelectionAnchor] = useState<number | null>(null)
  const [dragStart, setDragStart] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [waveform, setWaveform] = useState<{ amplitudes: number[]; duration: number } | null>(storedWaveform)
  const [activeWordIndex, setActiveWordIndex] = useState<number | null>(null)
  const [isFollowing, setIsFollowing] = useState(true)
  const [showTimestamps, setShowTimestamps] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [findingFillers, setFindingFillers] = useState(false)
  const [findingSilences, setFindingSilences] = useState(false)
  const [showSilenceSettings, setShowSilenceSettings] = useState(false)
  const [silenceThreshold, setSilenceThreshold] = useState(storedSilenceSettings.threshold) // dB
  const [silenceMinDuration, setSilenceMinDuration] = useState(storedSilenceSettings.minDuration) // ms
  const [silencePadding, setSilencePadding] = useState(Math.max(storedSilenceSettings.padding, 150)) // ms — floor of 150 to protect dialogue
  const [autoDetectFillers, setAutoDetectFillers] = useState(true)
  const { framerate } = useProjectStore()
  const [operationBlocked, setOperationBlocked] = useState(false)
  const { progress, message, elapsed, eta } = useProgress('transcript_editor')
  const abortRef = useRef<AbortController | null>(null)
  const activeWordRef = useRef<HTMLSpanElement | null>(null)
  const transcriptContainerRef = useRef<HTMLDivElement | null>(null)
  const lastClickTimeRef = useRef<number>(0)
  const pendingAutoDetectRef = useRef(false)

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

  // Sync waveform to store so it survives page switches
  useEffect(() => {
    storeWaveform(waveform)
  }, [waveform, storeWaveform])

  // Sync silence settings to store
  useEffect(() => {
    storeSilenceSettings({ threshold: silenceThreshold, minDuration: silenceMinDuration, padding: silencePadding })
  }, [silenceThreshold, silenceMinDuration, silencePadding, storeSilenceSettings])

  // Pickup: if transcription completed while component was unmounted
  useEffect(() => {
    if (teOp?.status === 'complete' && teOp.result) {
      const data = teOp.result
      if (data.words) {
        const normalized = data.words.map((word: any) => normalizeWord(word))
        pendingAutoDetectRef.current = true
        setWords(normalized)
        if (data.transcript_context) {
          setTranscriptContext(data.transcript_context)
        }
      }
      useOperationStore.getState().clearResult(TE_OP_NAME)
    }
    if (teOp?.status === 'error' && teOp.error) {
      setResult({ error: teOp.error })
    }
  }, [teOp?.status, teOp?.result, teOp?.error])

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

  // Annotation pipeline: mark fillers and repeated words for highlighting
  const { fillerCount, repeatedCount, filteredText } = useMemo(() => {
    if (words.length === 0) return { fillerCount: 0, repeatedCount: 0, filteredText: '' }

    let fillers = 0
    let repeats = 0
    const textParts: string[] = []

    for (let i = 0; i < words.length; i++) {
      const w = words[i]
      if (w.action === 'cut') continue // excluded from filtered text

      const norm = w.word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()

      if (autoDetectFillers && FILLER_WORDS.has(norm) && w.word !== '[silence]') {
        fillers++
      }

      if (autoDetectFillers && i > 0 && norm.length > 1) {
        const prevNorm = words[i - 1].word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()
        if (norm === prevNorm) repeats++
      }

      textParts.push(w.word)
    }

    return { fillerCount: fillers, repeatedCount: repeats, filteredText: textParts.join(' ') }
  }, [words, autoDetectFillers])

  // Helper: check if a word is a filler
  const isWordFiller = useCallback((word: TranscriptWord) => {
    const norm = word.word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()
    return autoDetectFillers && FILLER_WORDS.has(norm) && word.word !== '[silence]'
  }, [autoDetectFillers])

  // Helper: check if a word is a repeat of its predecessor
  const isWordRepeated = useCallback((index: number) => {
    if (!autoDetectFillers || index === 0) return false
    const norm = words[index].word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()
    if (norm.length <= 1) return false
    const prevNorm = words[index - 1].word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()
    return norm === prevNorm
  }, [words, autoDetectFillers])

  // Auto-detect silences + fillers after transcription completes
  useEffect(() => {
    if (!pendingAutoDetectRef.current || words.length === 0) return
    pendingAutoDetectRef.current = false

    // Auto-run silence detection (calls backend for audio-level analysis)
    handleFindSilences()
  }, [words]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll Resolve playhead and highlight the current word in real time (always active)
  useEffect(() => {
    if (words.length === 0) return

    let cancelled = false
    let lastSeconds = -1
    let prevSec = -1
    let prevTime = 0

    const poll = async () => {
      if (cancelled) return

      // Skip polling during cooldown after user click (let Resolve settle)
      if (Date.now() - lastClickTimeRef.current < 1000) return

      try {
        const data = await apiFetch('/api/resolve/playhead', { method: 'GET' })
        if (cancelled || data?.seconds == null) return

        const sec = data.seconds as number
        const now = Date.now()

        // Detect play/pause: if playhead moved since last poll
        if (prevSec >= 0 && now - prevTime < 300) {
          setIsPlaying(Math.abs(sec - prevSec) > 0.02)
        }
        prevSec = sec
        prevTime = now

        if (Math.abs(sec - lastSeconds) < 0.005) return
        lastSeconds = sec

        // Binary search for the word at this timestamp
        let foundIndex: number | null = null
        let lo = 0
        let hi = words.length - 1
        while (lo <= hi) {
          const mid = Math.floor((lo + hi) / 2)
          if (sec < words[mid].start) {
            hi = mid - 1
          } else if (sec > words[mid].end) {
            lo = mid + 1
          } else {
            foundIndex = mid
            break
          }
        }

        // If between words, snap to the nearest upcoming word
        if (foundIndex === null && lo < words.length && sec < words[lo].start) {
          const gap = words[lo].start - sec
          if (gap < 0.5) foundIndex = lo
        }

        setActiveWordIndex(foundIndex)
      } catch {
        // Silently fail — Resolve may not be connected
      }
    }

    const interval = setInterval(poll, 100)
    poll()

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [words])

  // Auto-scroll to the active word
  useEffect(() => {
    if (activeWordIndex !== null && activeWordRef.current && isFollowing) {
      activeWordRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
  }, [activeWordIndex, isFollowing])

  // Spacebar play/pause shortcut (with cooldown to prevent double-toggle)
  const lastSpaceRef = useRef<number>(0)
  useEffect(() => {
    if (words.length === 0) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return

      e.preventDefault()

      // Cooldown: ignore if pressed within 500ms
      const now = Date.now()
      if (now - lastSpaceRef.current < 500) return
      lastSpaceRef.current = now

      apiFetch('/api/resolve/transport', {
        method: 'POST',
        body: JSON.stringify({ action: 'toggle' }),
      }).catch(() => {})
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [words.length])

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
    useOperationStore.getState().startOperation(TE_OP_NAME)
    setResult(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const body: any = { language: language || null }
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
      } else {
        const normalized = (data.words || []).map((word: any) => normalizeWord(word))
        const ctx = data.transcript_context || null

        // Write to Zustand stores directly (works even if unmounted)
        useFeatureDataStore.getState().setTranscriptWords(normalized)
        useFeatureDataStore.getState().setTranscriptContext(ctx)
        useOperationStore.getState().completeOperation(TE_OP_NAME, {
          words: data.words,
          transcript_context: ctx,
        })

        // Update local state for the UI
        pendingAutoDetectRef.current = true
        setWords(normalized)
        setTranscriptContext(ctx)

        // Fetch waveform data in background — also writes to store
        apiFetch('/api/waveform/analyze?samples=800', { method: 'POST' })
          .then((wf) => {
            if (wf.amplitudes) {
              const waveformData = { amplitudes: wf.amplitudes, duration: wf.duration }
              useFeatureDataStore.getState().setTranscriptWaveform(waveformData)
              setWaveform(waveformData)
            }
          })
          .catch(() => {})
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
    }
  }

  const handleTranscribeSelection = async () => {
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
    try {
      // Resolve scripting API: toggle play/pause by sending a keyboard shortcut
      // or use the transport control endpoint
      await apiFetch('/api/resolve/transport', {
        method: 'POST',
        body: JSON.stringify({ action: isPlaying ? 'stop' : 'play' }),
      })
    } catch {
      // Silently fail
    }
  }

  const handleWordMouseDown = (
    index: number,
    event: React.MouseEvent<HTMLSpanElement>
  ) => {
    if (event.button !== 0) return

    event.preventDefault()
    lastClickTimeRef.current = Date.now()

    const additive = event.metaKey || event.ctrlKey

    if (event.shiftKey && selectionAnchor !== null) {
      setSelectionRange(selectionAnchor, index, additive)
      setSelectionAnchor(index)
      void handleJumpToWord(words[index])
      // Sync in/out after shift-click
      setTimeout(() => setWords((c) => { syncSelectionToInOut(c); return c }), 0)
      return
    }

    if (additive) {
      setWords((prev) => {
        const next = prev.map((word, i) =>
          i === index ? { ...word, selected: !word.selected } : word
        )
        syncSelectionToInOut(next)
        return next
      })
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
    // Sync selection to Resolve in/out marks (use timeout to get the latest state)
    setTimeout(() => {
      setWords((current) => {
        syncSelectionToInOut(current)
        return current
      })
    }, 0)
  }

  const selectSentence = (index: number) => {
    let start = index
    let end = index

    while (start > 0 && !words[start - 1].word.match(/[.!?]$/)) start--
    while (end < words.length - 1 && !words[end].word.match(/[.!?]$/)) end++

    setSelectionRange(start, end)
    setSelectionAnchor(index)
    // Sync in/out after sentence selection
    setTimeout(() => setWords((c) => { syncSelectionToInOut(c); return c }), 0)
  }

  const applyActionToSelection = (action: WordAction) => {
    setWords((prev) =>
      prev.map((word) =>
        word.selected && word.action !== action ? { ...word, action } : word
      )
    )
  }

  const handleSelectAllWords = () => {
    const allSelected = words.length > 0 && words.every((w) => w.selected)
    setWords((prev) => {
      const next = prev.map((word) => ({ ...word, selected: !allSelected }))
      if (!allSelected) syncSelectionToInOut(next)
      return next
    })
  }

  const handleKeepAll = () => {
    setWords((prev) => prev.map((word) => ({ ...word, action: 'keep' })))
  }

  const handleFindFillers = () => {
    if (words.length === 0) return
    setFindingFillers(true)

    let markedCount = 0
    let skippedCount = 0

    setWords((prev) =>
      prev.map((w, i) => {
        if (w.word === '[silence]' || w.action === 'cut') return w
        const norm = w.word.toLowerCase().replace(/[.,!?;:'"()\-\[\]{}]/g, '').trim()
        if (!FILLER_WORDS.has(norm)) return w

        // "Avoid harsh cuts" — check gaps to neighboring words
        // If the filler is jammed right against dialogue with no gap, skip it
        const prevWord = i > 0 ? prev[i - 1] : null
        const nextWord = i < prev.length - 1 ? prev[i + 1] : null
        const gapBefore = prevWord ? w.start - prevWord.end : Infinity
        const gapAfter = nextWord ? nextWord.start - w.end : Infinity

        if (gapBefore < 0.03 || gapAfter < 0.03) {
          skippedCount++
          return w
        }

        markedCount++
        return { ...w, action: 'cut' as WordAction }
      })
    )

    if (markedCount === 0 && skippedCount > 0) {
      toast.info(`Found ${skippedCount} filler word${skippedCount !== 1 ? 's' : ''} but skipped — too close to dialogue (would cause harsh cuts)`)
    } else if (markedCount === 0) {
      toast.info('No filler words detected')
    } else {
      const skipMsg = skippedCount > 0 ? ` (${skippedCount} skipped to avoid harsh cuts)` : ''
      toast.success(`Marked ${markedCount} filler word${markedCount !== 1 ? 's' : ''} for cutting${skipMsg}`)
    }

    setFindingFillers(false)
  }

  const handleFindSilences = async () => {
    if (words.length < 2) return
    setFindingSilences(true)

    const minGap = silenceMinDuration / 1000 // convert ms to seconds
    const pad = silencePadding / 1000 // convert ms to seconds

    // First remove any previously inserted [silence] markers
    const cleanWords = words.filter(w => w.word !== '[silence]')

    try {
      // Call backend silence detection for audio-level analysis
      const data = await apiFetch('/api/silence/analyze', {
        method: 'POST',
        body: JSON.stringify({
          threshold_db: silenceThreshold,
          min_duration_ms: silenceMinDuration,
          padding_ms: silencePadding,
        }),
      })

      const backendSilences: Array<{ start: number; end: number; duration: number }> =
        (data?.segments || []).filter((s: any) => s.duration >= minGap)

      // Also find gaps between transcript words as a fallback
      const gapSilences: Array<{ start: number; end: number }> = []
      for (let i = 0; i < cleanWords.length - 1; i++) {
        const gapStart = cleanWords[i].end
        const gapEnd = cleanWords[i + 1].start
        if (gapEnd - gapStart >= minGap) {
          gapSilences.push({ start: gapStart, end: gapEnd })
        }
      }

      // Merge backend + gap silences: use all backend detections plus any
      // transcript gaps that don't overlap with backend detections
      const allSilences: Array<{ start: number; end: number }> = [
        ...backendSilences.map(s => ({ start: s.start, end: s.end })),
      ]
      for (const gap of gapSilences) {
        const overlaps = allSilences.some(
          s => gap.start < s.end + 0.1 && gap.end > s.start - 0.1
        )
        if (!overlaps) allSilences.push(gap)
      }
      allSilences.sort((a, b) => a.start - b.start)

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
          const cutStart = Math.max(sil.start, wordEnd) + Math.max(pad, SAFETY_PADDING_SECONDS)
          const cutEnd = Math.min(sil.end, nextWordStart) - Math.max(pad, SAFETY_PADDING_SECONDS)
          if (cutEnd - cutStart > 0.02) { // must be > 20ms to be worth cutting
            silenceCount++
            const cutDuration = cutEnd - cutStart
            silenceTotal += cutDuration
            newWords.push({
              word: '[silence]',
              start: cutStart,
              end: cutEnd,
              probability: 1,
              action: 'cut',
              selected: false,
            })
          }
          silIdx++
        }
      }

      if (silenceCount === 0) {
        toast.info(`No silences found at ${silenceThreshold}dB / ${silenceMinDuration}ms`)
      } else {
        setWords(newWords)
        toast.success(
          `Marked ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} for cutting (${silenceTotal.toFixed(1)}s total)`
        )
      }
    } catch {
      // Backend unavailable — fall back to gap-only detection
      const newWords: TranscriptWord[] = []
      let silenceCount = 0
      let silenceTotal = 0

      for (let i = 0; i < cleanWords.length; i++) {
        newWords.push(cleanWords[i])
        if (i < cleanWords.length - 1) {
          const gapStart = cleanWords[i].end
          const gapEnd = cleanWords[i + 1].start
          if (gapEnd - gapStart >= minGap) {
            const cutStart = gapStart + Math.max(pad, SAFETY_PADDING_SECONDS)
            const cutEnd = gapEnd - Math.max(pad, SAFETY_PADDING_SECONDS)
            if (cutEnd - cutStart > 0.02) { // must be > 20ms to be worth cutting
              silenceCount++
              silenceTotal += cutEnd - cutStart
              newWords.push({
                word: '[silence]',
                start: cutStart,
                end: cutEnd,
                probability: 1,
                action: 'cut',
                selected: false,
              })
            }
          }
        }
      }

      if (silenceCount === 0) {
        toast.info(`No silences longer than ${silenceMinDuration}ms found`)
      } else {
        setWords(newWords)
        toast.success(
          `Marked ${silenceCount} silence${silenceCount !== 1 ? 's' : ''} for cutting (${silenceTotal.toFixed(1)}s total)`
        )
      }
    } finally {
      setFindingSilences(false)
    }
  }

  // Set Resolve in/out marks when selection changes (debounced to avoid rapid-fire)
  const inOutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const syncSelectionToInOut = useCallback((currentWords: TranscriptWord[]) => {
    if (inOutTimerRef.current) clearTimeout(inOutTimerRef.current)
    inOutTimerRef.current = setTimeout(async () => {
      const selected = currentWords.filter((w) => w.selected)
      if (selected.length === 0) return
      const inSec = Math.min(...selected.map((w) => w.start))
      const outSec = Math.max(...selected.map((w) => w.end))
      try {
        await apiFetch('/api/resolve/in-out', {
          method: 'POST',
          body: JSON.stringify({ in_seconds: inSec, out_seconds: outSec }),
        })
      } catch {
        // Silently fail
      }
    }, 400)
  }, [])

  const handleApply = async () => {
    setApplying(true)
    setResult(null)

    try {
      const fps = parseFloat(framerate) || 24
      const padFrames = Math.ceil(SAFETY_PADDING_SECONDS * fps)
      const rawCutSegments = mergeWordSegments(words, (word) => word.action === 'cut')
      const mutedSegments = mergeWordSegments(words, (word) => word.action === 'mute')

      // Apply safety padding to cut segments — shrink each cut region by 80ms on both
      // sides so we never clip into the audio of adjacent kept/muted words.
      // mergeWordSegments already merges adjacent cuts, so boundaries are always
      // at kept↔cut transitions where this padding matters most.
      const cutSegments = rawCutSegments
        .map((seg) => ({
          ...seg,
          start: seg.start + SAFETY_PADDING_SECONDS,
          end: seg.end - SAFETY_PADDING_SECONDS,
          start_frame: typeof seg.start_frame === 'number' ? seg.start_frame + padFrames : seg.start_frame,
          end_frame: typeof seg.end_frame === 'number' ? seg.end_frame - padFrames : seg.end_frame,
          source_start_frame: typeof seg.source_start_frame === 'number' ? seg.source_start_frame + padFrames : seg.source_start_frame,
          source_end_frame: typeof seg.source_end_frame === 'number' ? seg.source_end_frame - padFrames : seg.source_end_frame,
        }))
        .filter((seg) => seg.end - seg.start > 0.02) // drop segments smaller than 20ms

      const data = await apiFetch('/api/transcript-editor/apply', {
        method: 'POST',
        body: JSON.stringify({
          cut_segments: cutSegments,
          muted_segments: mutedSegments,
          transcript_context: transcriptContext,
        }),
      })
      if (data?.success) {
        setWords(prev =>
          prev
            .filter(w => w.action !== 'cut')
            .map(w => ({ ...w, action: 'keep' as WordAction, selected: false }))
        )
        // Update transcript context with new timeline bounds so words track correctly
        if (data.updated_transcript_context) {
          setTranscriptContext(data.updated_transcript_context)
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

  const handleWaveformClick = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds }),
      })
    } catch {
      // Silently fail
    }
  }

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleExportTranscript = () => {
    if (words.length === 0) return

    // Build text with timestamps injected every ~10 seconds
    const lines: string[] = []
    let currentLine = ''
    let lastTimestamp = -Infinity

    // Filter out cut words and silence markers from export
    const keptWords = words.filter((w) => w.action !== 'cut' && w.word !== '[silence]')

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
      // Strip timestamp markers like [0:30] or [1:02:30] before comparing
      const cleanedText = editedText.replace(/\[\d+:\d{2}(:\d{2})?\]/g, '')
      const editedWords = cleanedText.split(/\s+/).filter((w) => w.length > 0)
      const originalWords = words.map((w) => w.word)

      if (editedWords.length === 0) {
        toast.error('Imported file is empty')
        return
      }

      const kept = lcsWordDiff(originalWords, editedWords)
      const keptCount = kept.filter((k) => k).length
      const removedCount = kept.filter((k) => !k).length

      if (removedCount === 0) {
        toast.info('No differences found — transcript matches')
        return
      }

      // Safety check: if the LCS matched very few words, text normalization
      // probably failed (e.g. Unicode smart quotes vs straight quotes).
      // Abort rather than risk cutting the entire timeline.
      const matchRate = keptCount / originalWords.length
      if (matchRate < 0.1) {
        toast.error(
          `Import aborted: only ${keptCount} of ${originalWords.length} words matched. ` +
            `The edited file may have formatting differences that prevent matching.`
        )
        return
      }

      // Build cut segments by marking removed words, then using mergeWordSegments
      // with ALL words (not pre-filtered) so gaps between cut words are respected.
      const importWords = words.map((w, idx) => ({
        ...w,
        action: kept[idx] ? ('keep' as WordAction) : ('cut' as WordAction),
      }))
      const cutSegments = mergeWordSegments(importWords, (w) => w.action === 'cut')

      const cutWordsList = words.filter((_, idx) => !kept[idx])
      const cutSeconds = cutWordsList.reduce((sum, w) => sum + (w.end - w.start), 0)

      toast.info(`Importing: cutting ${removedCount} words (${cutSeconds.toFixed(1)}s)...`)

      // Apply directly to the timeline
      setApplying(true)
      setResult(null)
      try {
        const data = await apiFetch('/api/transcript-editor/apply', {
          method: 'POST',
          body: JSON.stringify({
            cut_segments: cutSegments,
            muted_segments: [],
            transcript_context: transcriptContext,
          }),
        })
        if (data?.success) {
          setWords([])
          setTranscriptContext(null)
          setResult({
            ...data,
            message: `${data.message} Re-transcribe to get updated timestamps.`,
          })
          toast.success(data.message || 'Imported edits applied successfully')
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

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Transcript Editor</h2>
        <p className="feature-description">
          Edit your video by editing text. Select words and mark them as cut or mute. Press spacebar to play/pause.
        </p>
      </div>

      <Card>
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="text-sm text-resolve-textDim mb-2 block">Language</label>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
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
          </div>
          {transcribing ? (
            abortRef.current ? (
              <Button onClick={handleCancel} variant="ghost" size="lg">
                Cancel
              </Button>
            ) : (
              <Button disabled size="lg" variant="ghost">
                Transcription in progress...
              </Button>
            )
          ) : (
            <div className="flex items-center gap-2">
              <Button onClick={() => handleTranscribe()} size="lg">
                {words.length > 0 ? 'Re-Transcribe' : 'Transcribe'}
              </Button>
              <Button
                onClick={handleTranscribeSelection}
                variant="ghost"
                size="lg"
                title="Transcribe only the in/out range set on your DaVinci Resolve timeline (I and O keys)"
              >
                Transcribe Selection
              </Button>
            </div>
          )}
        </div>
        {words.length > 0 && !transcribing && (
          <p className="mt-3 text-xs text-resolve-textMuted">
            Re-transcribe replaces the current transcript only after the new transcription finishes.
          </p>
        )}
      </Card>

      {transcribing && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {words.length > 0 && (
        <>
          {/* Toolbar */}
          <Card className="!py-2.5 space-y-2">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={handlePlayPause}
                className={isPlaying
                  ? 'bg-resolve-success/20 hover:bg-resolve-success/30 text-resolve-success border border-resolve-success/40'
                  : 'bg-resolve-accent hover:bg-resolve-accentHover text-white'}
                title="Spacebar"
              >
                {isPlaying ? 'Pause' : 'Play'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsFollowing((f) => !f)}
                className={isFollowing ? 'border border-resolve-accent/50 text-resolve-accent' : ''}
                title="Auto-scroll transcript with playback"
              >
                {isFollowing ? 'Scrolling' : 'Scroll'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowTimestamps((t) => !t)}
                className={showTimestamps ? 'border border-resolve-accent/50 text-resolve-accent' : ''}
              >
                Timestamps
              </Button>
              <div className="w-px h-5 bg-resolve-border mx-1" />
              <Button
                variant="danger"
                size="sm"
                onClick={() => applyActionToSelection('cut')}
                disabled={selectedCount === 0}
              >
                Cut
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => applyActionToSelection('mute')}
                disabled={selectedCount === 0}
                className="border border-resolve-warning/30 text-resolve-warning hover:bg-resolve-warning/10"
              >
                Mute
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => applyActionToSelection('keep')}
                disabled={selectedCount === 0}
              >
                Keep
              </Button>
              <div className="w-px h-5 bg-resolve-border mx-1" />
              <Button
                variant="ghost"
                size="sm"
                onClick={handleFindFillers}
                loading={findingFillers}
                title="Detect filler words (um, uh, uhh, hmm, er, ah) and mark them for cutting. Skips fillers that are too close to dialogue to avoid harsh cuts."
              >
                Find Fillers
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setAutoDetectFillers(f => !f)}
                className={autoDetectFillers ? 'border border-orange-500/50 text-orange-400' : ''}
                title="Highlight filler words and repeated consecutive words in the transcript"
              >
                Highlight
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowSilenceSettings(s => !s)}
                className={showSilenceSettings ? 'border border-resolve-accent/50 text-resolve-accent' : ''}
                title="Show silence detection settings"
              >
                Silences
              </Button>
              <div className="flex-1" />
              <Button variant="ghost" size="sm" onClick={handleSelectAllWords}>
                {words.length > 0 && words.every((w) => w.selected) ? 'Deselect All' : 'Select All'}
              </Button>
              <Button variant="ghost" size="sm" onClick={handleKeepAll}>
                Reset All
              </Button>
            </div>
            <div className="flex items-center justify-between text-xs text-resolve-textMuted">
              <div className="flex items-center gap-3">
                <span>{selectedCount} selected</span>
                {cutCount > 0 && <span className="text-resolve-error">{cutCount} cut</span>}
                {muteCount > 0 && <span className="text-resolve-warning">{muteCount} muted</span>}
                {autoDetectFillers && fillerCount > 0 && <span className="text-orange-400">{fillerCount} filler{fillerCount !== 1 ? 's' : ''}</span>}
                {autoDetectFillers && repeatedCount > 0 && <span className="text-orange-400">{repeatedCount} repeat{repeatedCount !== 1 ? 's' : ''}</span>}
              </div>
              <div className="flex items-center gap-2">
                <button onClick={handleExportTranscript} className="hover:text-resolve-text transition-colors">
                  Export
                </button>
                <span className="text-resolve-border">|</span>
                <button onClick={() => fileInputRef.current?.click()} className="hover:text-resolve-text transition-colors">
                  Import
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,text/plain"
                  onChange={handleImportTranscript}
                  className="hidden"
                />
              </div>
            </div>
            {showSilenceSettings && (
              <div className="pt-2 mt-2 border-t border-resolve-border space-y-3">
                <div className="grid grid-cols-3 gap-4">
                  <Slider
                    label="Threshold"
                    value={silenceThreshold}
                    onChange={setSilenceThreshold}
                    min={-60}
                    max={-20}
                    unit=" dB"
                  />
                  <Slider
                    label="Min Duration"
                    value={silenceMinDuration}
                    onChange={setSilenceMinDuration}
                    min={100}
                    max={3000}
                    step={50}
                    unit=" ms"
                  />
                  <Slider
                    label="Padding"
                    value={silencePadding}
                    onChange={setSilencePadding}
                    min={0}
                    max={500}
                    step={25}
                    unit=" ms"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-resolve-textMuted">
                    {silenceThreshold}dB
                    {' · '}Min: {silenceMinDuration}ms ({Math.round((silenceMinDuration / 1000) * (parseFloat(framerate) || 24))}f)
                    {' · '}Pad: {silencePadding}ms ({Math.round((silencePadding / 1000) * (parseFloat(framerate) || 24))}f)
                  </span>
                  <Button
                    size="sm"
                    onClick={handleFindSilences}
                    loading={findingSilences}
                  >
                    Find Silences
                  </Button>
                </div>
              </div>
            )}
          </Card>

          {waveform && (
            <Card className="!p-3">
              <WaveformDisplay
                amplitudes={waveform.amplitudes}
                duration={waveform.duration}
                words={words}
                onTimeClick={handleWaveformClick}
              />
            </Card>
          )}

          <Card className="!p-4">
            <div
              ref={transcriptContainerRef}
              className="leading-8 select-none"
              style={{ userSelect: 'none' }}
            >
              {(() => {
                const elements: React.ReactNode[] = []
                let lastTimestampSec = -Infinity

                for (let index = 0; index < words.length; index++) {
                  const word = words[index]
                  const isActive = index === activeWordIndex

                  // Insert timestamp badge every ~10 seconds
                  if (showTimestamps && word.start - lastTimestampSec >= 10) {
                    lastTimestampSec = word.start
                    elements.push(
                      <span
                        key={`ts-${index}`}
                        className="inline-block mx-1 px-1.5 py-0.5 rounded bg-resolve-accent/15 text-resolve-accent text-[10px] font-mono align-middle select-none cursor-default"
                        title={`Timestamp: ${formatTimestamp(word.start)}`}
                      >
                        {formatTimestamp(word.start)}
                      </span>
                    )
                  }

                  const isFiller = isWordFiller(word)
                  const isRepeated = isWordRepeated(index)
                  const isFillerOrRepeat = isFiller || isRepeated

                  const actionClass =
                    word.action === 'cut'
                      ? 'bg-resolve-error/20 text-resolve-error line-through opacity-70 hover:opacity-90'
                      : word.action === 'mute'
                        ? 'bg-resolve-warning/20 text-resolve-warning hover:bg-resolve-warning/30'
                        : isFillerOrRepeat
                          ? 'bg-orange-500/20 text-orange-300 hover:bg-orange-500/30'
                          : 'text-resolve-text hover:bg-resolve-accent/10'

                  // Build display text and tooltip for silence markers
                  const isSilenceMarker = word.word === '[silence]'
                  const silenceDuration = word.end - word.start
                  const fps = parseFloat(framerate) || 24
                  const silenceFrames = Math.round(silenceDuration * fps)
                  const silenceLabel = isSilenceMarker
                    ? `[silence ${silenceDuration.toFixed(2)}s / ${silenceFrames}f]`
                    : word.word
                  const fillerTag = isFiller ? ' [filler]' : isRepeated ? ' [repeated]' : ''
                  const wordTitle = isSilenceMarker
                    ? `Silence: ${silenceDuration.toFixed(2)}s (${silenceFrames} frames) | ${formatTimestamp(word.start)} - ${formatTimestamp(word.end)}`
                    : `${formatTimestamp(word.start)} - ${formatTimestamp(word.end)}${fillerTag}`

                  elements.push(
                    <span
                      key={index}
                      ref={isActive ? activeWordRef : undefined}
                      onMouseDown={(event) => handleWordMouseDown(index, event)}
                      onMouseEnter={() => handleWordMouseEnter(index)}
                      onMouseUp={handleWordMouseUp}
                      onDoubleClick={(event) => {
                        event.preventDefault()
                        selectSentence(index)
                      }}
                      className={`inline-block px-0.5 py-0.5 mx-0.5 rounded cursor-pointer text-sm ${
                        isSilenceMarker ? 'font-mono text-[11px] ' : ''
                      }${
                        isActive
                          ? 'bg-resolve-accent text-white font-semibold transition-all duration-75 ease-out'
                          : `transition-all duration-100 ${actionClass}`
                      } ${
                        word.selected && !isActive
                          ? 'ring-1 ring-resolve-accent ring-inset bg-resolve-accent/10'
                          : ''
                      }`}
                      title={wordTitle}
                    >
                      {silenceLabel}
                    </span>
                  )
                }

                return elements
              })()}
            </div>
          </Card>

          <Card className="!py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-resolve-textDim">
                <span className="text-resolve-text font-medium">
                  {formatDuration(keptSeconds)}
                </span>{' '}
                kept of <span>{formatDuration(totalSeconds)}</span>
                {muteSeconds > 0 && (
                  <span className="ml-2 text-resolve-warning">
                    ({muteSeconds.toFixed(1)}s muted)
                  </span>
                )}
              </div>
              <ConfirmApply
                onConfirm={handleApply}
                loading={applying}
                disabled={!hasEdits}
                description={`This will apply ${cutCount} cut words (${cutSeconds.toFixed(1)}s removed) and ${muteCount} muted words (${muteSeconds.toFixed(1)}s silenced).`}
              >
                Apply Edits
              </ConfirmApply>
            </div>
          </Card>
        </>
      )}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <div className="flex items-center justify-between gap-3">
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
      {result?.success && (
        <Card className="border-resolve-success/30">
          <p className="text-sm text-resolve-success">{result.message}</p>
        </Card>
      )}
    </div>
  )
}

function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

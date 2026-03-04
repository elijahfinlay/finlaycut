// Transcript Editor — shared types, constants, and pure utility functions.

export type WordAction = 'keep' | 'cut' | 'mute'

export interface WaveformViewport {
  startTime: number
  endTime: number
}

export interface CutBoundary {
  time: number
  wordIndexBefore: number
  wordIndexAfter: number
  side: 'start' | 'end' // start of cut region vs end of cut region
}

export interface TranscriptWord {
  word: string
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
  file_path?: string
  clip_item_id?: string
  kind?: string
  speaker?: string
  speaker_index?: number
  probability: number
  action: WordAction
  selected: boolean
  deleted?: boolean
}

export interface Segment {
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
  kind?: string
  label?: string
}

export interface TranscriptContext {
  clip_item_id?: string
  file_path?: string
  clip_start_frame?: number
  clip_end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
}

export interface TranscriptSession {
  session_id: string
  timeline_name: string | null
  timeline_signature: string
  last_synced_at: string
  sync_state: 'fresh' | 'stale' | 'reconcilable' | 'requires_retranscribe'
  stale_reason?: 'external_timeline_change' | 'signature_mismatch' | 'ambiguous_sync' | 'content_changed'
  origin: 'transcribe' | 'transcript_apply' | 'reconcile'
}

export interface SpeakerSummary {
  speaker: string
  speaker_index?: number
  total_duration: number
  segment_count: number
  word_count: number
  avg_dbfs?: number | null
  peak_dbfs?: number | null
  relative_db?: number | null
  selection_score?: number
  is_primary_suggestion?: boolean
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const MERGE_GAP_SECONDS = 0.05
export const SAFETY_PADDING_SECONDS = 0.08 // 80ms — protects dialogue from being clipped at cut boundaries
export const MIN_FILLER_FRAMES = 3
export const FILLER_ONSET_EXPAND_FRAMES = 1
export const FILLER_RELEASE_PAD_FRAMES = 1

/** Unambiguous hesitation fillers only. Common lexical fillers stay backend-driven. */
export const FILLER_WORDS = new Set([
  'um', 'uh', 'uh-huh', 'uhh', 'uhm', 'umm',
  'hmm', 'hm', 'mmhmm', 'mhm', 'mm', 'mmm',
  'huh', 'er', 'erm', 'ah', 'ahh',
])
export const FILLER_PATTERNS = [
  /^u+h+$/i,
  /^u+m+h*$/i,
  /^u+h+m*$/i,
  /^u+m+$/i,
  /^h+m+$/i,
  /^m+h+m+$/i,
  /^e+r+m*$/i,
  /^a+h+$/i,
]
export const SPEAKER_BADGE_STYLES = [
  'bg-cyan-500/15 text-cyan-200 border border-cyan-500/30',
  'bg-emerald-500/15 text-emerald-200 border border-emerald-500/30',
  'bg-amber-500/15 text-amber-200 border border-amber-500/30',
  'bg-fuchsia-500/15 text-fuchsia-200 border border-fuchsia-500/30',
]

export const TE_OP_NAME = 'transcript_editor'

// ---------------------------------------------------------------------------
// Pure utility functions
// ---------------------------------------------------------------------------

/**
 * LCS-based diff: finds which original words were removed in the edited text.
 * Returns an array of booleans — true if the word at that index was kept.
 */
export function lcsWordDiff(originalWords: string[], editedWords: string[]): boolean[] {
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

export function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'") // smart single quotes → straight
    .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"') // smart double quotes → straight
    .replace(/[\u2013\u2014\u2015]/g, '-') // en/em dash → hyphen
    .replace(/\u2026/g, '...') // ellipsis character → dots
    .replace(/[.,!?;:'"()\-\[\]{}]/g, '') // remove punctuation
    .trim()
}

export function normalizeSpokenToken(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'")
    .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"')
    .replace(/[\u2013\u2014\u2015]/g, '-')
    .replace(/\u2026/g, '...')
    .replace(/[^a-z-]/g, '')
    .trim()
}

export function isSilenceMarker(word: Pick<TranscriptWord, 'word' | 'kind'>): boolean {
  return word.kind === 'silence' || word.word === '[silence]'
}

export function isAudioFillerMarker(word: Pick<TranscriptWord, 'word' | 'kind'>): boolean {
  return word.kind === 'audio_hesitation' || word.word === '[filler]'
}

export function isAnalysisMarker(word: Pick<TranscriptWord, 'word' | 'kind'>): boolean {
  return isSilenceMarker(word) || isAudioFillerMarker(word)
}

export function humanizeSpeakerLabel(speaker?: string, speakerIndex?: number): string {
  if (typeof speakerIndex === 'number') return `Speaker ${speakerIndex + 1}`
  if (!speaker) return 'Speaker'
  const match = speaker.match(/(\d+)\s*$/)
  if (match) return `Speaker ${Number(match[1])}`
  return speaker.replace(/_/g, ' ')
}

export function speakerBadgeStyle(speakerIndex?: number): string {
  if (typeof speakerIndex !== 'number') return SPEAKER_BADGE_STYLES[0]
  return SPEAKER_BADGE_STYLES[Math.abs(speakerIndex) % SPEAKER_BADGE_STYLES.length]
}

export function sortTranscriptWords(words: TranscriptWord[]): TranscriptWord[] {
  return [...words].sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start
    if (a.end !== b.end) return a.end - b.end
    return a.word.localeCompare(b.word)
  })
}

export function shrinkSegmentForSafety(
  segment: Segment,
  fps: number,
  maxPadSeconds: number = SAFETY_PADDING_SECONDS,
  minVisibleFrames: number = 1
): Segment | null {
  const duration = Math.max(0, segment.end - segment.start)
  if (duration <= 0) return null

  const maxPadFrames = Math.max(0, Math.ceil(maxPadSeconds * fps))
  const maxPadByDuration = Math.max(0, Math.floor((duration * fps) * 0.35))
  const maxPadByVisibility = Math.max(
    0,
    Math.floor((((duration * fps) - minVisibleFrames) / 2))
  )
  const padFrames = Math.min(maxPadFrames, maxPadByDuration, maxPadByVisibility)
  const padSeconds = padFrames / fps

  const nextStart = segment.start + padSeconds
  const nextEnd = segment.end - padSeconds
  if (nextEnd <= nextStart) return null

  let nextStartFrame = segment.start_frame
  let nextEndFrame = segment.end_frame
  let nextSourceStartFrame = segment.source_start_frame
  let nextSourceEndFrame = segment.source_end_frame

  if (typeof segment.start_frame === 'number') nextStartFrame = segment.start_frame + padFrames
  if (typeof segment.end_frame === 'number') nextEndFrame = segment.end_frame - padFrames
  if (typeof segment.source_start_frame === 'number') nextSourceStartFrame = segment.source_start_frame + padFrames
  if (typeof segment.source_end_frame === 'number') nextSourceEndFrame = segment.source_end_frame - padFrames

  if (
    typeof nextStartFrame === 'number' &&
    typeof nextEndFrame === 'number' &&
    nextEndFrame <= nextStartFrame
  ) {
    return null
  }

  return {
    ...segment,
    start: nextStart,
    end: nextEnd,
    start_frame: nextStartFrame,
    end_frame: nextEndFrame,
    source_start_frame: nextSourceStartFrame,
    source_end_frame: nextSourceEndFrame,
  }
}

export function adjustFillerSegmentForRemoval(
  segment: Segment,
  fps: number,
  onsetExpandFrames: number = FILLER_ONSET_EXPAND_FRAMES,
  releasePadFrames: number = FILLER_RELEASE_PAD_FRAMES,
  minVisibleFrames: number = 1
): Segment | null {
  const derivedSpanFrames =
    typeof segment.start_frame === 'number' && typeof segment.end_frame === 'number'
      ? Math.max(0, segment.end_frame - segment.start_frame)
      : Math.max(0, Math.round((segment.end - segment.start) * fps))
  if (derivedSpanFrames <= 0) return null

  const maxLeadExpand = Math.max(0, derivedSpanFrames - minVisibleFrames)
  const leadFrames = Math.min(Math.max(0, onsetExpandFrames), maxLeadExpand)
  const maxTailPad = Math.max(0, derivedSpanFrames - minVisibleFrames - leadFrames)
  const tailFrames = Math.min(Math.max(0, releasePadFrames), maxTailPad)

  const nextStart = Math.max(0, segment.start - leadFrames / fps)
  const nextEnd = Math.max(nextStart, segment.end - tailFrames / fps)
  if (nextEnd <= nextStart) return null

  let nextStartFrame = segment.start_frame
  let nextEndFrame = segment.end_frame
  let nextSourceStartFrame = segment.source_start_frame
  let nextSourceEndFrame = segment.source_end_frame

  if (typeof segment.start_frame === 'number') nextStartFrame = Math.max(0, segment.start_frame - leadFrames)
  if (typeof segment.end_frame === 'number') nextEndFrame = segment.end_frame - tailFrames
  if (typeof segment.source_start_frame === 'number') nextSourceStartFrame = Math.max(0, segment.source_start_frame - leadFrames)
  if (typeof segment.source_end_frame === 'number') nextSourceEndFrame = segment.source_end_frame - tailFrames

  if (
    typeof nextStartFrame === 'number' &&
    typeof nextEndFrame === 'number' &&
    nextEndFrame <= nextStartFrame
  ) {
    return null
  }

  return {
    ...segment,
    start: Number(nextStart.toFixed(4)),
    end: Number(nextEnd.toFixed(4)),
    start_frame: nextStartFrame,
    end_frame: nextEndFrame,
    source_start_frame: nextSourceStartFrame,
    source_end_frame: nextSourceEndFrame,
  }
}

export function isLikelyFillerWord(
  word: TranscriptWord,
  prevWord?: TranscriptWord | null,
  nextWord?: TranscriptWord | null
): boolean {
  if (isAnalysisMarker(word)) return false

  const norm = normalizeSpokenToken(word.word)
  if (!norm) return false
  if (FILLER_WORDS.has(norm) || FILLER_PATTERNS.some((pattern) => pattern.test(norm))) {
    return true
  }

  const duration = Math.max(0, word.end - word.start)
  const frameSpan =
    typeof word.start_frame === 'number' && typeof word.end_frame === 'number'
      ? Math.max(0, word.end_frame - word.start_frame)
      : null
  const gapBefore = prevWord ? Math.max(0, word.start - prevWord.end) : Infinity
  const gapAfter = nextWord ? Math.max(0, nextWord.start - word.end) : Infinity
  const hesitationLike = /^(?:mm+|hm+|mhm+|hmm+|umm+|uhh+|uhm+|er+|erm+|ahh+)$/.test(norm)

  return (
    hesitationLike &&
    duration <= 0.85 &&
    (duration >= 0.08 || (frameSpan !== null && frameSpan >= MIN_FILLER_FRAMES)) &&
    (gapBefore >= 0.02 || gapAfter >= 0.02 || !prevWord || !nextWord)
  )
}

export function wordOverlapsSegment(word: TranscriptWord, segment: Segment): boolean {
  if (
    typeof word.start_frame === 'number' &&
    typeof word.end_frame === 'number' &&
    typeof segment.start_frame === 'number' &&
    typeof segment.end_frame === 'number'
  ) {
    return word.end_frame > segment.start_frame && word.start_frame < segment.end_frame
  }

  return word.end > segment.start && word.start < segment.end
}

export function findActiveWordIndex(words: TranscriptWord[], seconds: number): number | null {
  if (words.length === 0) return null

  let lo = 0
  let hi = words.length - 1
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2)
    if (seconds < words[mid].start) {
      hi = mid - 1
    } else if (seconds > words[mid].end) {
      lo = mid + 1
    } else {
      return mid
    }
  }

  const prevIndex = lo - 1
  const nextIndex = lo
  const prevWord = prevIndex >= 0 ? words[prevIndex] : null
  const nextWord = nextIndex < words.length ? words[nextIndex] : null
  const prevGap = prevWord ? Math.abs(seconds - prevWord.end) : Number.POSITIVE_INFINITY
  const nextGap = nextWord ? Math.abs(nextWord.start - seconds) : Number.POSITIVE_INFINITY

  if (prevGap <= nextGap && prevGap < 0.18) return prevIndex
  if (nextGap < 0.35) return nextIndex
  return null
}

export function getRemovedSecondsBefore(cutSegments: Segment[], seconds: number): number {
  let removed = 0
  for (const seg of cutSegments) {
    if (seg.start >= seconds) break
    removed += Math.max(0, Math.min(seg.end, seconds) - seg.start)
    if (seg.end >= seconds) break
  }
  return removed
}

export function retimeWordsAfterCuts(
  words: TranscriptWord[],
  cutSegments: Segment[],
  fps: number,
  timelineStartFrame?: number
): TranscriptWord[] {
  const sortedCuts = [...cutSegments].sort((a, b) => a.start - b.start)

  return words
    .filter((word) => word.action !== 'cut')
    .map((word) => {
      const removedBeforeStart = getRemovedSecondsBefore(sortedCuts, word.start)
      const removedBeforeEnd = getRemovedSecondsBefore(sortedCuts, word.end)
      const nextStart = Math.max(0, word.start - removedBeforeStart)
      const nextEnd = Math.max(nextStart, word.end - removedBeforeEnd)

      return {
        ...word,
        start: Number(nextStart.toFixed(4)),
        end: Number(nextEnd.toFixed(4)),
        start_frame:
          typeof timelineStartFrame === 'number'
            ? timelineStartFrame + Math.round(nextStart * fps)
            : word.start_frame,
        end_frame:
          typeof timelineStartFrame === 'number'
            ? timelineStartFrame + Math.round(nextEnd * fps)
            : word.end_frame,
        action: 'keep' as WordAction,
        selected: false,
      }
    })
}

export function normalizeWord(raw: any): TranscriptWord {
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
    file_path: typeof raw?.file_path === 'string' ? raw.file_path : undefined,
    clip_item_id: typeof raw?.clip_item_id === 'string' ? raw.clip_item_id : undefined,
    kind: typeof raw?.kind === 'string' ? raw.kind : undefined,
    speaker: typeof raw?.speaker === 'string' ? raw.speaker : undefined,
    speaker_index:
      typeof raw?.speaker_index === 'number' ? raw.speaker_index : undefined,
    probability: Number(raw?.probability || 0),
    action,
    selected: Boolean(raw?.selected),
  }
}

export function isTranscriptSessionStale(session: TranscriptSession | null): boolean {
  if (!session) return false
  return session.sync_state !== 'fresh'
}

export function mergeWordSegments(
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

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function formatPreciseTimestamp(seconds: number): string {
  const totalCentiseconds = Math.max(0, Math.round(seconds * 100))
  const h = Math.floor(totalCentiseconds / 360000)
  const m = Math.floor((totalCentiseconds % 360000) / 6000)
  const s = Math.floor((totalCentiseconds % 6000) / 100)
  const cs = totalCentiseconds % 100
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`
  }
  return `${m}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`
}

export function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

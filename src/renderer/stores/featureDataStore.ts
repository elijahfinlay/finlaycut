import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Persists feature-specific data across sidebar tab switches.
 * Each feature stores its analysis results here so switching back
 * to a tab doesn't lose your work.
 */

interface TranscriptWord {
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
  action?: 'keep' | 'cut' | 'mute'
  selected?: boolean
  deleted?: boolean
}

interface TranscriptContext {
  clip_item_id?: string
  file_path?: string
  clip_start_frame?: number
  clip_end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
}

interface TranscriptSession {
  session_id: string
  timeline_name: string | null
  timeline_signature: string
  last_synced_at: string
  sync_state: 'fresh' | 'stale' | 'reconcilable' | 'requires_retranscribe'
  stale_reason?: 'external_timeline_change' | 'signature_mismatch' | 'ambiguous_sync' | 'content_changed'
  origin: 'transcribe' | 'transcript_apply' | 'reconcile'
}

interface FillerDetection {
  word: string
  start: number
  end: number
  start_frame?: number
  end_frame?: number
  source_start_frame?: number
  source_end_frame?: number
  kind?: string
  boundary_source?: string
  context: string
  selected: boolean
}

interface SilenceSegment {
  start: number
  end: number
  duration: number
  start_frame?: number
  end_frame?: number
  kind?: string
  boundary_source?: string
  selected: boolean
}

interface FeatureDataState {
  // Transcript Editor
  transcriptWords: TranscriptWord[]
  transcriptLanguage: string
  transcriptContext: TranscriptContext | null
  transcriptSession: TranscriptSession | null
  transcriptSpeakers: any[]
  setTranscriptWords: (words: TranscriptWord[]) => void
  setTranscriptLanguage: (lang: string) => void
  setTranscriptContext: (context: TranscriptContext | null) => void
  setTranscriptSession: (session: TranscriptSession | null) => void
  setTranscriptSpeakers: (speakers: any[]) => void

  // Transcript Editor — waveform (survives page switches)
  transcriptWaveform: { amplitudes: number[]; duration: number } | null
  setTranscriptWaveform: (wf: { amplitudes: number[]; duration: number } | null) => void

  // Filler Word Removal
  fillerDetections: FillerDetection[]
  setFillerDetections: (detections: FillerDetection[]) => void

  // Silence Removal
  silenceSegments: SilenceSegment[]
  silenceSettings: { threshold: number; minDuration: number; minFrames: number; padding: number }
  setSilenceSegments: (segments: SilenceSegment[]) => void
  setSilenceSettings: (settings: { threshold: number; minDuration: number; minFrames: number; padding: number }) => void

  // Caption Generator
  captions: any[]
  srtContent: string
  setCaptions: (captions: any[]) => void
  setSrtContent: (srt: string) => void

  // Chapter Detection
  chapters: any[]
  setChapters: (chapters: any[]) => void

  // Dynamic Zoom
  zoomPoints: any[]
  setZoomPoints: (points: any[]) => void

  // Viral Clips
  viralClips: any[]
  setViralClips: (clips: any[]) => void

  // B-Roll
  brollSuggestions: any[]
  setBrollSuggestions: (suggestions: any[]) => void

  // Repetition
  repetitionGroups: any[]
  setRepetitionGroups: (groups: any[]) => void

  // Podcast
  podcastTracks: any[]
  setPodcastTracks: (tracks: any[]) => void

  // Clear all data (e.g. when timeline changes)
  clearAll: () => void
}

export const useFeatureDataStore = create<FeatureDataState>()(
  persist(
    (set) => ({
      // Transcript
      transcriptWords: [],
      transcriptLanguage: '',
      transcriptContext: null,
      transcriptSession: null,
      transcriptSpeakers: [],
      setTranscriptWords: (words) => set({ transcriptWords: words }),
      setTranscriptLanguage: (lang) => set({ transcriptLanguage: lang }),
      setTranscriptContext: (context) => set({ transcriptContext: context }),
      setTranscriptSession: (session) => set({ transcriptSession: session }),
      setTranscriptSpeakers: (speakers) => set({ transcriptSpeakers: speakers }),

      // Waveform
      transcriptWaveform: null,
      setTranscriptWaveform: (wf) => set({ transcriptWaveform: wf }),

      // Filler
      fillerDetections: [],
      setFillerDetections: (detections) => set({ fillerDetections: detections }),

      // Silence
      silenceSegments: [],
      silenceSettings: { threshold: -40, minDuration: 300, minFrames: 14, padding: 100 },
      setSilenceSegments: (segments) => set({ silenceSegments: segments }),
      setSilenceSettings: (settings) => set({ silenceSettings: settings }),

      // Captions
      captions: [],
      srtContent: '',
      setCaptions: (captions) => set({ captions }),
      setSrtContent: (srt) => set({ srtContent: srt }),

      // Chapters
      chapters: [],
      setChapters: (chapters) => set({ chapters }),

      // Zoom
      zoomPoints: [],
      setZoomPoints: (points) => set({ zoomPoints: points }),

      // Viral
      viralClips: [],
      setViralClips: (clips) => set({ viralClips: clips }),

      // B-Roll
      brollSuggestions: [],
      setBrollSuggestions: (suggestions) => set({ brollSuggestions: suggestions }),

      // Repetition
      repetitionGroups: [],
      setRepetitionGroups: (groups) => set({ repetitionGroups: groups }),

      // Podcast
      podcastTracks: [],
      setPodcastTracks: (tracks) => set({ podcastTracks: tracks }),

      // Clear
      clearAll: () => set({
        transcriptWords: [],
        transcriptLanguage: '',
        transcriptContext: null,
        transcriptSession: null,
        transcriptSpeakers: [],
        transcriptWaveform: null,
        fillerDetections: [],
        silenceSegments: [],
        silenceSettings: { threshold: -40, minDuration: 300, minFrames: 14, padding: 100 },
        captions: [],
        srtContent: '',
        chapters: [],
        zoomPoints: [],
        viralClips: [],
        brollSuggestions: [],
        repetitionGroups: [],
        podcastTracks: [],
      }),
    }),
    {
      name: 'finlaycut-feature-data',
      partialize: (state) => ({
        transcriptWords: state.transcriptWords,
        transcriptLanguage: state.transcriptLanguage,
        transcriptContext: state.transcriptContext,
        transcriptSession: state.transcriptSession,
        transcriptSpeakers: state.transcriptSpeakers,
        transcriptWaveform: state.transcriptWaveform,
        fillerDetections: state.fillerDetections,
        silenceSegments: state.silenceSegments,
        silenceSettings: state.silenceSettings,
        captions: state.captions,
        srtContent: state.srtContent,
        chapters: state.chapters,
        zoomPoints: state.zoomPoints,
        viralClips: state.viralClips,
        brollSuggestions: state.brollSuggestions,
        repetitionGroups: state.repetitionGroups,
        podcastTracks: state.podcastTracks,
      }),
    }
  )
)

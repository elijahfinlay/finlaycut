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

interface FillerDetection {
  word: string
  start: number
  end: number
  context: string
  selected: boolean
}

interface SilenceSegment {
  start: number
  end: number
  duration: number
  selected: boolean
}

interface FeatureDataState {
  // Transcript Editor
  transcriptWords: TranscriptWord[]
  transcriptLanguage: string
  transcriptContext: TranscriptContext | null
  setTranscriptWords: (words: TranscriptWord[]) => void
  setTranscriptLanguage: (lang: string) => void
  setTranscriptContext: (context: TranscriptContext | null) => void

  // Filler Word Removal
  fillerDetections: FillerDetection[]
  setFillerDetections: (detections: FillerDetection[]) => void

  // Silence Removal
  silenceSegments: SilenceSegment[]
  silenceSettings: { threshold: number; minDuration: number; padding: number }
  setSilenceSegments: (segments: SilenceSegment[]) => void
  setSilenceSettings: (settings: { threshold: number; minDuration: number; padding: number }) => void

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
      setTranscriptWords: (words) => set({ transcriptWords: words }),
      setTranscriptLanguage: (lang) => set({ transcriptLanguage: lang }),
      setTranscriptContext: (context) => set({ transcriptContext: context }),

      // Filler
      fillerDetections: [],
      setFillerDetections: (detections) => set({ fillerDetections: detections }),

      // Silence
      silenceSegments: [],
      silenceSettings: { threshold: -40, minDuration: 300, padding: 100 },
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
        fillerDetections: [],
        silenceSegments: [],
        silenceSettings: { threshold: -40, minDuration: 300, padding: 100 },
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

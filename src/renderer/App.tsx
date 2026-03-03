import React, { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import { TranscriptEditor } from './components/features/TranscriptEditor'
import { SilenceRemoval } from './components/features/SilenceRemoval'
// FillerWordRemoval is now integrated into TranscriptEditor ("Find Fillers" button)
import { CaptionGenerator } from './components/features/CaptionGenerator'
import { DynamicZoom } from './components/features/DynamicZoom'
import { ChapterDetection } from './components/features/ChapterDetection'
import { PodcastEditor } from './components/features/PodcastEditor'
import { RepetitionRemoval } from './components/features/RepetitionRemoval'
import { ViralClipDetector } from './components/features/ViralClipDetector'
import { BRollSuggestions } from './components/features/BRollSuggestions'
import { AudioEnhancement } from './components/features/AudioEnhancement'
import { ThumbnailGenerator } from './components/features/ThumbnailGenerator'
import { SettingsPanel } from './components/features/SettingsPanel'
import { UndoHistory } from './components/features/UndoHistory'
import { ExportPanel } from './components/features/ExportPanel'
import { SceneDetector } from './components/features/SceneDetector'
import { useResolveStatus } from './hooks/useResolveStatus'
import { useProjectStore } from './stores/projectStore'
import { useFeatureDataStore } from './stores/featureDataStore'
import { useSettingsStore } from './stores/settingsStore'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { Onboarding } from './components/features/Onboarding'

const ONBOARDING_KEY = 'finlaycut-onboarding-complete'

const FEATURE_MAP: Record<string, React.ComponentType> = {
  transcript: TranscriptEditor,
  silence: SilenceRemoval,
  filler: TranscriptEditor, // Filler detection is now part of the Transcript Editor
  captions: CaptionGenerator,
  zoom: DynamicZoom,
  chapters: ChapterDetection,
  podcast: PodcastEditor,
  takes: RepetitionRemoval,
  viral: ViralClipDetector,
  broll: BRollSuggestions,
  audio: AudioEnhancement,
  thumbnail: ThumbnailGenerator,
  export: ExportPanel,
  scenes: SceneDetector,
  settings: SettingsPanel,
  history: UndoHistory,
}

export default function App() {
  const { status, refresh } = useResolveStatus()
  const { setStatus, setRefreshResolve, activeFeature } = useProjectStore()
  const { clearAll } = useFeatureDataStore()
  const initSettings = useSettingsStore((s) => s.initSettings)
  const timelineKeyRef = useRef<string | null>(null)
  const [showOnboarding, setShowOnboarding] = useState(
    () => !localStorage.getItem(ONBOARDING_KEY)
  )

  // Load settings from backend on app startup
  useEffect(() => {
    initSettings()
  }, [initSettings])

  // Global keyboard shortcuts (Cmd+1-9 for features, Cmd+B sidebar toggle)
  useKeyboardShortcuts()

  useEffect(() => {
    setStatus(status)
  }, [status, setStatus])

  useEffect(() => {
    setRefreshResolve(refresh)
  }, [refresh, setRefreshResolve])

  useEffect(() => {
    const nextKey =
      status.connected && status.project?.name && status.timeline?.name
        ? `${status.project.name}::${status.timeline.name}`
        : null

    if (timelineKeyRef.current !== null && timelineKeyRef.current !== nextKey) {
      clearAll()
    }
    timelineKeyRef.current = nextKey
  }, [status.connected, status.project?.name, status.timeline?.name, clearAll])

  const handleOnboardingComplete = () => {
    localStorage.setItem(ONBOARDING_KEY, 'true')
    setShowOnboarding(false)
  }

  const ActiveFeatureComponent = FEATURE_MAP[activeFeature] || SilenceRemoval

  if (showOnboarding) {
    return (
      <div className="h-screen bg-resolve-bg">
        <Onboarding onComplete={handleOnboardingComplete} />
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-resolve-bg">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <ActiveFeatureComponent />
        </main>
      </div>
    </div>
  )
}

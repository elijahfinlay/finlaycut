import React, { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import { TranscriptEditor } from './components/features/TranscriptEditor'
import { SilenceRemoval } from './components/features/SilenceRemoval'
import { FillerWordRemoval } from './components/features/FillerWordRemoval'
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
import { ClipSearch } from './components/features/ClipSearch'
import { type ResolveStatus, useResolveStatus } from './hooks/useResolveStatus'
import { useProjectStore } from './stores/projectStore'
import { useFeatureDataStore } from './stores/featureDataStore'
import { useOperationStore } from './stores/operationStore'
import { useSettingsStore } from './stores/settingsStore'
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts'
import { Onboarding } from './components/features/Onboarding'

const ONBOARDING_KEY = 'finlaycut-onboarding-complete'

function getResolveStatusKey(status: ResolveStatus | null | undefined): string | null {
  if (!status?.connected) return null
  const projectName = status.project?.name
  const timelineName = status.timeline?.name
  if (!projectName || !timelineName) return null
  return `${projectName}::${timelineName}`
}

const FEATURE_MAP: Record<string, React.ComponentType> = {
  transcript: TranscriptEditor,
  silence: SilenceRemoval,
  filler: FillerWordRemoval,
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
  'clip-search': ClipSearch,
  settings: SettingsPanel,
  history: UndoHistory,
}

export default function App() {
  const { status, refresh } = useResolveStatus()
  const {
    setStatus,
    setRefreshResolve,
    setPendingResolveChange,
    activeFeature,
  } = useProjectStore()
  const { clearAll, clearNonTranscriptData, transcriptSession, transcriptWords } = useFeatureDataStore()
  const transcriptionRunning = useOperationStore(
    (s) => s.operations.transcript_editor?.status === 'running'
  )
  const initSettings = useSettingsStore((s) => s.initSettings)
  const timelineKeyRef = useRef<string | null>(null)
  const statusOverrideRef = useRef(status)
  const [statusOverride, setStatusOverride] = useState(status)
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
    statusOverrideRef.current = statusOverride
  }, [statusOverride])

  useEffect(() => {
    const activeStatus = statusOverrideRef.current
    const activeKey = getResolveStatusKey(activeStatus)
    const nextKey = getResolveStatusKey(status)

    if (!activeKey || !activeStatus.connected) {
      setStatusOverride(status)
      setPendingResolveChange(null)
      return
    }

    if (!status.connected) {
      if (transcriptionRunning && activeStatus.connected) {
        return
      }
      setStatusOverride(status)
      setPendingResolveChange(null)
      return
    }

    if (!nextKey) {
      if (transcriptionRunning && activeStatus.connected) {
        return
      }
      return
    }

    if (activeKey === nextKey) {
      setStatusOverride(status)
      setPendingResolveChange(null)
      return
    }

    setPendingResolveChange(status)
  }, [status, setPendingResolveChange, transcriptionRunning])

  useEffect(() => {
    setStatus(statusOverride)
  }, [statusOverride, setStatus])

  useEffect(() => {
    setRefreshResolve(async () => {
      const nextStatus = await refresh()
      if (!nextStatus) {
        setStatusOverride({
          connected: false,
          project: null,
          timeline: null,
        })
        setPendingResolveChange(null)
        return
      }
      setStatusOverride(nextStatus)
      setPendingResolveChange(null)
    })
  }, [refresh, setPendingResolveChange, setRefreshResolve])

  useEffect(() => {
    if (transcriptionRunning) {
      return
    }

    const nextKey =
      statusOverride.connected && statusOverride.project?.name && statusOverride.timeline?.name
        ? `${statusOverride.project.name}::${statusOverride.timeline.name}`
        : null

    if (timelineKeyRef.current !== null && timelineKeyRef.current !== nextKey) {
      if (transcriptSession || transcriptWords.length > 0) {
        clearNonTranscriptData()
      } else {
        clearAll()
      }
      useOperationStore.getState().clearAll()
    }
    timelineKeyRef.current = nextKey
  }, [
    statusOverride.connected,
    statusOverride.project?.name,
    statusOverride.timeline?.name,
    clearAll,
    clearNonTranscriptData,
    transcriptSession,
    transcriptWords.length,
    transcriptionRunning,
  ])

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
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <ActiveFeatureComponent />
        </main>
      </div>
    </div>
  )
}

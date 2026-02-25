import React, { useEffect } from 'react'
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
import { useResolveStatus } from './hooks/useResolveStatus'
import { useProjectStore } from './stores/projectStore'

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
  settings: SettingsPanel,
  history: UndoHistory,
}

export default function App() {
  const { status } = useResolveStatus()
  const { setStatus, activeFeature } = useProjectStore()

  useEffect(() => {
    setStatus(status)
  }, [status, setStatus])

  const ActiveFeatureComponent = FEATURE_MAP[activeFeature] || SilenceRemoval

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

import { create } from 'zustand'

interface ProjectState {
  connected: boolean
  projectName: string | null
  timelineName: string | null
  timelineCount: number
  videoTracks: number
  audioTracks: number
  framerate: string
  resolution: string
  duration: number
  activeFeature: string
  setStatus: (status: any) => void
  setActiveFeature: (feature: string) => void
}

export const useProjectStore = create<ProjectState>((set) => ({
  connected: false,
  projectName: null,
  timelineName: null,
  timelineCount: 0,
  videoTracks: 0,
  audioTracks: 0,
  framerate: '',
  resolution: '',
  duration: 0,
  activeFeature: 'silence',
  setStatus: (status) =>
    set({
      connected: status.connected,
      projectName: status.project?.name || null,
      timelineName: status.timeline?.name || null,
      timelineCount: status.project?.timeline_count || 0,
      videoTracks: status.timeline?.video_tracks || 0,
      audioTracks: status.timeline?.audio_tracks || 0,
      framerate: status.timeline?.framerate || '',
      resolution: status.timeline?.resolution || '',
      duration: status.timeline?.duration || 0
    }),
  setActiveFeature: (feature) => set({ activeFeature: feature })
}))

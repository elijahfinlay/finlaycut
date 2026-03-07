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
  sidebarCollapsed: boolean
  hasPendingResolveChange: boolean
  pendingProjectName: string | null
  pendingTimelineName: string | null
  refreshResolve: (() => Promise<void>) | null
  setStatus: (status: any) => void
  setPendingResolveChange: (status: any | null) => void
  setActiveFeature: (feature: string) => void
  setRefreshResolve: (fn: () => Promise<void>) => void
  toggleSidebar: () => void
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
  activeFeature: 'transcript',
  sidebarCollapsed: false,
  hasPendingResolveChange: false,
  pendingProjectName: null,
  pendingTimelineName: null,
  refreshResolve: null,
  setRefreshResolve: (fn) => set({ refreshResolve: fn }),
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
  setPendingResolveChange: (status) =>
    set({
      hasPendingResolveChange: Boolean(status?.connected),
      pendingProjectName: status?.project?.name || null,
      pendingTimelineName: status?.timeline?.name || null,
    }),
  setActiveFeature: (feature) => set({ activeFeature: feature }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}))

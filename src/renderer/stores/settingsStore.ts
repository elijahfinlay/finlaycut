import { create } from 'zustand'

interface SettingsState {
  aiBackend: 'ollama' | 'claude'
  ollamaModel: string
  whisperModel: string
  claudeApiKey: string
  pexelsApiKey: string
  silenceThresholdDb: number
  silenceMinDurationMs: number
  silencePaddingMs: number
  fillerWords: string[]
  cacheLimitGb: number
  backupRetentionDays: number
  setAiBackend: (backend: 'ollama' | 'claude') => void
  updateSettings: (partial: Partial<SettingsState>) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  aiBackend: 'ollama',
  ollamaModel: 'llama3',
  whisperModel: 'base',
  claudeApiKey: '',
  pexelsApiKey: '',
  silenceThresholdDb: -40,
  silenceMinDurationMs: 300,
  silencePaddingMs: 100,
  fillerWords: ['um', 'uh', 'like', 'you know', 'basically', 'actually', 'literally', 'right', 'so', 'well', 'I mean', 'kind of', 'sort of', 'er', 'ah'],
  cacheLimitGb: 2.0,
  backupRetentionDays: 30,
  setAiBackend: (backend) => set({ aiBackend: backend }),
  updateSettings: (partial) => set(partial)
}))

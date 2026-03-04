import { create } from 'zustand'
import { apiFetch } from '../hooks/useApi'

interface SettingsState {
  aiBackend: 'ollama' | 'claude'
  ollamaModel: string
  claudeModel: string
  whisperModel: string
  claudeApiKey: string
  pexelsApiKey: string
  silenceThresholdDb: number
  silenceMinDurationMs: number
  silenceMinDurationFrames: number
  silencePaddingMs: number
  fillerWords: string[]
  cacheLimitGb: number
  backupRetentionDays: number
  _initialized: boolean
  setAiBackend: (backend: 'ollama' | 'claude') => void
  updateSettings: (partial: Partial<SettingsState>) => void
  initSettings: () => Promise<void>
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  aiBackend: 'ollama',
  ollamaModel: 'llama3',
  claudeModel: 'claude-sonnet-4-5-20250514',
  whisperModel: 'base',
  claudeApiKey: '',
  pexelsApiKey: '',
  silenceThresholdDb: -40,
  silenceMinDurationMs: 300,
  silenceMinDurationFrames: 14,
  silencePaddingMs: 100,
  fillerWords: ['um', 'uh', 'uh-huh', 'uhh', 'uhm', 'umm', 'hmm', 'mmhmm', 'mhm', 'mm', 'huh', 'er', 'erm', 'ah', 'ahh'],
  cacheLimitGb: 2.0,
  backupRetentionDays: 30,
  _initialized: false,
  setAiBackend: (backend) => set({ aiBackend: backend }),
  updateSettings: (partial) => set(partial),
  initSettings: async () => {
    if (get()._initialized) return
    try {
      const data = await apiFetch('/api/settings/')
      set({
        aiBackend: data.ai_backend,
        ollamaModel: data.ollama_model,
        claudeModel: data.claude_model || 'claude-sonnet-4-5-20250514',
        whisperModel: data.whisper_model,
        silenceThresholdDb: data.silence_threshold_db,
        silenceMinDurationMs: data.silence_min_duration_ms,
        silenceMinDurationFrames: Math.max(14, data.silence_min_duration_frames ?? 14),
        silencePaddingMs: data.silence_padding_ms,
        fillerWords: data.filler_words,
        cacheLimitGb: data.cache_limit_gb,
        backupRetentionDays: data.backup_retention_days,
        _initialized: true,
      })
    } catch {
      // Backend not ready yet — use defaults
    }
  },
}))

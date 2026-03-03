import React, { useState, useEffect } from 'react'
import { Button } from '../common/Button'
import { Slider } from '../common/Slider'
import { Card } from '../common/Card'
import { apiFetch } from '../../hooks/useApi'
import { useSettingsStore } from '../../stores/settingsStore'

export function SettingsPanel() {
  const settings = useSettingsStore()
  const [cacheSize, setCacheSize] = useState<string>('0 MB')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    loadSettings()
    loadCacheSize()
  }, [])

  const loadSettings = async () => {
    try {
      const data = await apiFetch('/api/settings/')
      // Only update non-secret fields from server. Don't touch API keys
      // since the server doesn't return them (only has_claude_key: bool).
      settings.updateSettings({
        aiBackend: data.ai_backend,
        ollamaModel: data.ollama_model,
        claudeModel: data.claude_model || 'claude-sonnet-4-5-20250514',
        whisperModel: data.whisper_model,
        silenceThresholdDb: data.silence_threshold_db,
        silenceMinDurationMs: data.silence_min_duration_ms,
        silencePaddingMs: data.silence_padding_ms,
        fillerWords: data.filler_words,
        cacheLimitGb: data.cache_limit_gb,
        backupRetentionDays: data.backup_retention_days,
      })
    } catch {
      // Use defaults
    }
  }

  const loadCacheSize = async () => {
    try {
      const data = await apiFetch('/api/settings/cache-size')
      setCacheSize(`${data.size_mb} MB`)
    } catch {
      // Ignore
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage('')
    try {
      // Send empty strings as empty strings (not null) so the backend can clear them
      await apiFetch('/api/settings/', {
        method: 'PUT',
        body: JSON.stringify({
          ai_backend: settings.aiBackend,
          claude_api_key: settings.claudeApiKey,
          claude_model: settings.claudeModel,
          ollama_model: settings.ollamaModel,
          whisper_model: settings.whisperModel,
          silence_threshold_db: settings.silenceThresholdDb,
          silence_min_duration_ms: settings.silenceMinDurationMs,
          silence_padding_ms: settings.silencePaddingMs,
          filler_words: settings.fillerWords,
          pexels_api_key: settings.pexelsApiKey,
          cache_limit_gb: settings.cacheLimitGb,
          backup_retention_days: settings.backupRetentionDays,
        })
      })
      setMessage('Settings saved')
    } catch (err: any) {
      setMessage(`Error: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleClearCache = async () => {
    try {
      await apiFetch('/api/settings/clear-cache', { method: 'POST' })
      loadCacheSize()
      setMessage('Cache cleared')
    } catch {
      setMessage('Failed to clear cache')
    }
  }

  return (
    <div className="feature-panel">
      <h2 className="feature-title">Settings</h2>

      {/* AI Backend */}
      <Card>
        <h3 className="text-sm font-semibold text-resolve-text mb-4">AI Backend</h3>
        <div className="space-y-4">
          <div className="flex gap-3">
            {(['ollama', 'claude'] as const).map((backend) => (
              <button
                key={backend}
                onClick={() => settings.setAiBackend(backend)}
                className={`flex-1 py-3 rounded-lg text-sm font-medium transition-colors ${
                  settings.aiBackend === backend
                    ? 'bg-resolve-accent text-white'
                    : 'bg-resolve-bg text-resolve-textDim hover:bg-resolve-surfaceLight'
                }`}
              >
                {backend === 'ollama' ? 'Ollama (Local, Free)' : 'Claude API'}
              </button>
            ))}
          </div>

          {settings.aiBackend === 'ollama' && (
            <div>
              <label className="text-sm text-resolve-textDim block mb-1">Ollama Model</label>
              <select
                value={settings.ollamaModel}
                onChange={(e) => settings.updateSettings({ ollamaModel: e.target.value })}
                className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
              >
                <option value="llama3">Llama 3</option>
                <option value="llama3:8b">Llama 3 8B</option>
                <option value="mistral">Mistral</option>
                <option value="mixtral">Mixtral</option>
                <option value="gemma2">Gemma 2</option>
              </select>
            </div>
          )}

          {settings.aiBackend === 'claude' && (
            <div className="space-y-3">
              <div>
                <label className="text-sm text-resolve-textDim block mb-1">Claude API Key</label>
                <input
                  type="text"
                  value={settings.claudeApiKey}
                  onChange={(e) => settings.updateSettings({ claudeApiKey: e.target.value })}
                  placeholder="sk-ant-..."
                  className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text placeholder:text-resolve-textMuted font-mono"
                />
              </div>
              <div>
                <label className="text-sm text-resolve-textDim block mb-1">Claude Model</label>
                <select
                  value={settings.claudeModel}
                  onChange={(e) => settings.updateSettings({ claudeModel: e.target.value })}
                  className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
                >
                  <option value="claude-sonnet-4-5-20250514">Claude Sonnet 4.5</option>
                  <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (Fastest)</option>
                  <option value="claude-opus-4-6">Claude Opus 4.6 (Most capable)</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Whisper */}
      <Card>
        <h3 className="text-sm font-semibold text-resolve-text mb-4">Whisper Model</h3>
        <select
          value={settings.whisperModel}
          onChange={(e) => settings.updateSettings({ whisperModel: e.target.value })}
          className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
        >
          <option value="tiny">Tiny (~39MB) - Fastest</option>
          <option value="base">Base (~140MB) - Balanced</option>
          <option value="small">Small (~460MB) - Better accuracy</option>
          <option value="medium">Medium (~1.5GB) - High accuracy</option>
          <option value="large">Large (~3GB) - Best accuracy</option>
        </select>
      </Card>

      {/* Defaults */}
      <Card>
        <h3 className="text-sm font-semibold text-resolve-text mb-4">Default Settings</h3>
        <div className="space-y-5">
          <Slider
            label="Silence Threshold"
            value={settings.silenceThresholdDb}
            onChange={(v) => settings.updateSettings({ silenceThresholdDb: v })}
            min={-60}
            max={-20}
            unit=" dB"
          />
          <Slider
            label="Min Silence Duration"
            value={settings.silenceMinDurationMs}
            onChange={(v) => settings.updateSettings({ silenceMinDurationMs: v })}
            min={100}
            max={2000}
            step={50}
            unit=" ms"
          />
          <Slider
            label="Backup Retention"
            value={settings.backupRetentionDays}
            onChange={(v) => settings.updateSettings({ backupRetentionDays: v })}
            min={1}
            max={90}
            unit=" days"
          />
        </div>
      </Card>

      {/* API Keys */}
      <Card>
        <h3 className="text-sm font-semibold text-resolve-text mb-4">API Keys</h3>
        <div>
          <label className="text-sm text-resolve-textDim block mb-1">Pexels API Key (for B-Roll)</label>
          <input
            type="text"
            value={settings.pexelsApiKey}
            onChange={(e) => settings.updateSettings({ pexelsApiKey: e.target.value })}
            placeholder="Enter Pexels API key..."
            className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text placeholder:text-resolve-textMuted font-mono"
          />
          <p className="text-xs text-resolve-textMuted mt-1">
            Free at pexels.com/api. 200 requests/hour.
          </p>
        </div>
      </Card>

      {/* Cache */}
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-resolve-text">Cache</h3>
            <p className="text-xs text-resolve-textMuted">Current size: {cacheSize}</p>
          </div>
          <Button variant="danger" size="sm" onClick={handleClearCache}>
            Clear Cache
          </Button>
        </div>
      </Card>

      {/* Save */}
      <div className="flex items-center gap-3">
        <Button onClick={handleSave} loading={saving} size="lg">
          Save Settings
        </Button>
        {message && (
          <span className={`text-sm ${message.startsWith('Error') ? 'text-resolve-error' : 'text-resolve-success'}`}>
            {message}
          </span>
        )}
      </div>
    </div>
  )
}

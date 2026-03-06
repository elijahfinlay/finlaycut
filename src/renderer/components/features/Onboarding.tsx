import React, { useEffect, useState } from 'react'
import { Button } from '../common/Button'
import { apiFetch } from '../../hooks/useApi'

interface OnboardingProps {
  onComplete: () => void
}

type BackendDiagnosticsState = Awaited<ReturnType<Window['api']['getBackendDiagnostics']>>

function phaseLabel(phase: BackendDiagnosticsState['phase']): string {
  switch (phase) {
    case 'idle':
      return 'Idle'
    case 'preparing-runtime':
      return 'Preparing runtime'
    case 'creating-venv':
      return 'Creating Python environment'
    case 'installing-dependencies':
      return 'Installing dependencies'
    case 'starting-backend':
      return 'Starting backend'
    case 'ready':
      return 'Backend ready'
    case 'error':
      return 'Startup failed'
    default:
      return phase
  }
}

function phaseTone(phase: BackendDiagnosticsState['phase']): string {
  if (phase === 'ready') return 'border-resolve-success/30 bg-resolve-success/10'
  if (phase === 'error') return 'border-resolve-error/30 bg-resolve-error/10'
  return 'border-resolve-border bg-resolve-surface'
}

export function Onboarding({ onComplete }: OnboardingProps) {
  const [checking, setChecking] = useState(false)
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [diagnostics, setDiagnostics] = useState<BackendDiagnosticsState | null>(null)
  const [retryingBackend, setRetryingBackend] = useState(false)

  useEffect(() => {
    let cancelled = false

    const loadDiagnostics = async () => {
      try {
        const next = await window.api.getBackendDiagnostics()
        if (!cancelled) {
          setDiagnostics(next)
        }
      } catch {
        if (!cancelled) {
          setDiagnostics(null)
        }
      }
    }

    void loadDiagnostics()
    const interval = window.setInterval(() => {
      void loadDiagnostics()
    }, 1500)

    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  const handleCheckConnection = async () => {
    setChecking(true)
    try {
      await apiFetch('/api/resolve/reconnect', { method: 'POST' })
      const data = await apiFetch('/api/resolve/status')
      if (data.connected) {
        setStatus('success')
      } else {
        setStatus('error')
      }
    } catch {
      setStatus('error')
    } finally {
      setChecking(false)
    }
  }

  const handleRetryBackend = async () => {
    setRetryingBackend(true)
    try {
      const next = await window.api.retryBackendStart()
      setDiagnostics(next)
    } catch {
      try {
        const next = await window.api.getBackendDiagnostics()
        setDiagnostics(next)
      } catch {
        // Ignore fallback diagnostics failure.
      }
    } finally {
      setRetryingBackend(false)
    }
  }

  const backendReady = diagnostics?.phase === 'ready'
  const backendBusy =
    diagnostics?.phase === 'preparing-runtime'
    || diagnostics?.phase === 'creating-venv'
    || diagnostics?.phase === 'installing-dependencies'
    || diagnostics?.phase === 'starting-backend'
  const recentLogs = diagnostics?.logs.slice(-6) ?? []

  return (
    <div className="h-full flex items-center justify-center p-8">
      <div className="max-w-lg w-full space-y-8">
        {/* Header */}
        <div className="text-center space-y-3">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-resolve-accent to-sky-400 bg-clip-text text-transparent">
            Welcome to FinlayCut
          </h1>
          <p className="text-resolve-textDim text-sm">
            AI-powered editing automation for DaVinci Resolve
          </p>
        </div>

        {/* Setup steps */}
        <div className="bg-resolve-surface border border-resolve-border rounded-xl p-6 space-y-5">
          <h2 className="text-sm font-semibold text-resolve-text">Quick Setup</h2>

          <div className="space-y-4">
            <div className="flex gap-3">
              <div className="w-6 h-6 rounded-full bg-resolve-accent/15 text-resolve-accent flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
                1
              </div>
              <div>
                <p className="text-sm text-resolve-text font-medium">Open DaVinci Resolve</p>
                <p className="text-xs text-resolve-textMuted mt-0.5">
                  Make sure Resolve is running with a project open
                </p>
              </div>
            </div>

            <div className="flex gap-3">
              <div className="w-6 h-6 rounded-full bg-resolve-accent/15 text-resolve-accent flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
                2
              </div>
              <div>
                <p className="text-sm text-resolve-text font-medium">Enable External Scripting</p>
                <p className="text-xs text-resolve-textMuted mt-0.5">
                  Resolve {'>'} Preferences {'>'} General {'>'} External scripting using: <strong className="text-resolve-text">Local</strong>
                </p>
              </div>
            </div>

            <div className="flex gap-3">
              <div className="w-6 h-6 rounded-full bg-resolve-accent/15 text-resolve-accent flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
                3
              </div>
              <div>
                <p className="text-sm text-resolve-text font-medium">Open a timeline</p>
                <p className="text-xs text-resolve-textMuted mt-0.5">
                  Select or create a timeline with at least one clip
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className={`border rounded-xl p-6 space-y-4 ${phaseTone(diagnostics?.phase ?? 'idle')}`}>
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <h2 className="text-sm font-semibold text-resolve-text">Backend Runtime</h2>
              <p className="text-xs text-resolve-textMuted">
                {diagnostics?.summary ?? 'Checking local backend status…'}
              </p>
            </div>
            <span className="text-xs font-medium text-resolve-textDim">
              {phaseLabel(diagnostics?.phase ?? 'idle')}
            </span>
          </div>

          {backendBusy && (
            <p className="text-xs text-resolve-textMuted">
              First launch on a new Mac can take a few minutes while FinlayCut prepares its Python runtime.
            </p>
          )}

          {diagnostics?.lastError && (
            <p className="text-xs text-resolve-error whitespace-pre-wrap">
              {diagnostics.lastError}
            </p>
          )}

          {(diagnostics?.pythonPath || diagnostics?.ffmpegPath) && (
            <div className="grid grid-cols-1 gap-2 text-xs text-resolve-textMuted">
              {diagnostics.pythonPath && (
                <p>
                  Python: <span className="text-resolve-textDim break-all">{diagnostics.pythonPath}</span>
                </p>
              )}
              {diagnostics.ffmpegPath && (
                <p>
                  ffmpeg: <span className="text-resolve-textDim break-all">{diagnostics.ffmpegPath}</span>
                </p>
              )}
            </div>
          )}

          {recentLogs.length > 0 && (
            <div className="rounded-lg bg-resolve-bg border border-resolve-border px-3 py-2 max-h-36 overflow-auto">
              <pre className="text-[11px] leading-relaxed text-resolve-textMuted whitespace-pre-wrap font-mono">
                {recentLogs.join('\n')}
              </pre>
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button
              onClick={handleRetryBackend}
              variant="ghost"
              size="sm"
              loading={retryingBackend}
              disabled={backendBusy}
            >
              Retry Backend
            </Button>
            {!backendReady && (
              <span className="text-xs text-resolve-textMuted">
                Resolve connection checks are available once the backend is ready.
              </span>
            )}
          </div>
        </div>

        {/* Connection check */}
        <div className="flex flex-col items-center gap-3">
          <Button
            onClick={handleCheckConnection}
            loading={checking}
            size="lg"
            disabled={!backendReady}
          >
            Check Connection
          </Button>

          {status === 'success' && (
            <p className="text-sm text-resolve-success">Connected to DaVinci Resolve!</p>
          )}
          {status === 'error' && (
            <p className="text-sm text-resolve-error">
              Could not connect. Check that Resolve is open with scripting set to Local.
            </p>
          )}

          <button
            onClick={onComplete}
            className="text-xs text-resolve-textMuted hover:text-resolve-textDim transition-colors"
          >
            {status === 'success' ? 'Get Started' : 'Skip for now'}
          </button>
        </div>
      </div>
    </div>
  )
}

import React, { useState } from 'react'
import { Button } from '../common/Button'
import { apiFetch } from '../../hooks/useApi'

interface OnboardingProps {
  onComplete: () => void
}

export function Onboarding({ onComplete }: OnboardingProps) {
  const [checking, setChecking] = useState(false)
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle')

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

        {/* Connection check */}
        <div className="flex flex-col items-center gap-3">
          <Button
            onClick={handleCheckConnection}
            loading={checking}
            size="lg"
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

import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

export function AudioEnhancement() {
  const [enhancing, setEnhancing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('audio_enhance')

  const handleEnhance = async () => {
    setEnhancing(true)
    setResult(null)
    try {
      const data = await apiFetch('/api/audio/enhance', { method: 'POST' })
      setResult(data)
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setEnhancing(false)
    }
  }

  return (
    <div className="feature-panel">
      <TimelineBanner />
      <div>
        <h2 className="feature-title">Smart Audio Enhancement</h2>
        <p className="feature-description">
          Leverages DaVinci Resolve's built-in audio tools including Voice Isolation
          and Fairlight audio processing to automatically clean up your audio.
        </p>
      </div>

      <Card>
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-resolve-text">What it does:</h3>
          <ul className="space-y-2 text-sm text-resolve-textDim">
            <li className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-resolve-accent" />
              Normalize dialogue volume levels
            </li>
            <li className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-resolve-accent" />
              Apply Voice Isolation (if available in Studio)
            </li>
            <li className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-resolve-accent" />
              Reduce background noise
            </li>
          </ul>
        </div>
      </Card>

      <Button onClick={handleEnhance} loading={enhancing} size="lg">
        Enhance Audio
      </Button>

      {enhancing && <ProgressBar progress={progress} message={message} />}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
      {result?.success && (
        <Card className="border-resolve-success/30">
          <p className="text-sm text-resolve-success">{result.message}</p>
          <p className="text-xs text-resolve-textMuted mt-1">
            Processed {result.clips_processed} clips
          </p>
        </Card>
      )}
    </div>
  )
}

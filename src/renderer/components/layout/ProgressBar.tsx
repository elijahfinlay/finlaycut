import React from 'react'
import { clsx } from 'clsx'

interface ProgressBarProps {
  progress: number // 0 to 1
  message?: string
  elapsed?: number // ms
  eta?: number | null // ms
  className?: string
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

export function ProgressBar({ progress, message, elapsed, eta, className }: ProgressBarProps) {
  const pct = Math.round(progress * 100)

  return (
    <div className={clsx('space-y-2', className)}>
      <div className="flex items-center justify-between text-sm">
        <span className="text-resolve-textDim">{message || 'Processing...'}</span>
        <div className="flex items-center gap-3">
          {elapsed != null && elapsed > 2000 && (
            <span className="text-resolve-textMuted font-mono text-xs">
              {formatDuration(elapsed)}
              {eta != null && eta > 1000 ? ` / ~${formatDuration(eta)} left` : progress > 0 ? ' elapsed' : ''}
            </span>
          )}
          <span className="text-resolve-accent font-mono">{pct}%</span>
        </div>
      </div>
      <div className="w-full h-2 bg-resolve-border rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-resolve-accent to-orange-400 rounded-full transition-all duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

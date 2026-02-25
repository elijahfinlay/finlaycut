import React from 'react'
import { clsx } from 'clsx'

interface ProgressBarProps {
  progress: number // 0 to 1
  message?: string
  className?: string
}

export function ProgressBar({ progress, message, className }: ProgressBarProps) {
  const pct = Math.round(progress * 100)

  return (
    <div className={clsx('space-y-2', className)}>
      <div className="flex items-center justify-between text-sm">
        <span className="text-resolve-textDim">{message || 'Processing...'}</span>
        <span className="text-resolve-accent font-mono">{pct}%</span>
      </div>
      <div className="w-full h-2 bg-resolve-border rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-resolve-accent to-purple-400 rounded-full transition-all duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

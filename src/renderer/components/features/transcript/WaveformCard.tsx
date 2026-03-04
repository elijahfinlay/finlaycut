import React from 'react'
import { Card } from '../../common/Card'
import { WaveformDisplay } from '../WaveformDisplay'
import type { TranscriptWord, WaveformViewport, CutBoundary } from './types'
import { formatPreciseTimestamp } from './types'

export interface WaveformCardProps {
  waveform: { amplitudes: number[]; duration: number }
  words: TranscriptWord[]
  viewport?: WaveformViewport | null
  cutBoundaries?: CutBoundary[]
  playheadSeconds?: number
  framerate?: string
  onTimeClick: (seconds: number) => void
  onBoundaryDragStart?: (boundary: CutBoundary) => void
  onBoundaryDrag?: (boundary: CutBoundary, newTime: number) => void
  onBoundaryDragEnd?: () => void
  onViewportChange?: (viewport: WaveformViewport | null) => void
  onResetZoom?: () => void
}

function formatTimecodeWithFrames(seconds: number, fps: number): string {
  const totalFrames = Math.round(seconds * fps)
  const f = totalFrames % fps
  const totalSec = Math.floor(totalFrames / fps)
  const s = totalSec % 60
  const m = Math.floor(totalSec / 60) % 60
  const h = Math.floor(totalSec / 3600)
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}:${String(f).padStart(2, '0')}`
  }
  return `${m}:${String(s).padStart(2, '0')}:${String(f).padStart(2, '0')}`
}

export function WaveformCard({
  waveform,
  words,
  viewport,
  cutBoundaries,
  playheadSeconds,
  framerate,
  onTimeClick,
  onBoundaryDragStart,
  onBoundaryDrag,
  onBoundaryDragEnd,
  onViewportChange,
  onResetZoom,
}: WaveformCardProps) {
  const fps = parseFloat(framerate || '24') || 24

  return (
    <Card className="!p-3">
      {viewport && (
        <div className="mb-2 flex items-center justify-between text-xs text-slate-400">
          <span>
            {formatPreciseTimestamp(viewport.startTime)} – {formatPreciseTimestamp(viewport.endTime)}
          </span>
          <button
            type="button"
            className="rounded px-2 py-0.5 text-xs text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-200"
            onClick={onResetZoom}
          >
            Show Full Timeline
          </button>
        </div>
      )}
      <WaveformDisplay
        amplitudes={waveform.amplitudes}
        duration={waveform.duration}
        words={words}
        viewport={viewport}
        cutBoundaries={cutBoundaries}
        onTimeClick={onTimeClick}
        onBoundaryDragStart={onBoundaryDragStart}
        onBoundaryDrag={onBoundaryDrag}
        onBoundaryDragEnd={onBoundaryDragEnd}
        onViewportChange={onViewportChange}
        playheadSeconds={playheadSeconds}
        fps={fps}
      />
      {playheadSeconds != null && playheadSeconds > 0 && (
        <div className="mt-1.5 flex items-center justify-between text-xs tabular-nums">
          <span className="text-white/80 font-medium">
            {formatTimecodeWithFrames(playheadSeconds, fps)}
          </span>
          <span className="text-slate-500">
            {formatPreciseTimestamp(waveform.duration)}
          </span>
        </div>
      )}
    </Card>
  )
}

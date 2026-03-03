import React, { useRef, useEffect, useMemo } from 'react'

interface WaveformDisplayProps {
  amplitudes: number[]
  duration: number
  words?: Array<{
    start: number
    end: number
    action: 'keep' | 'cut' | 'mute'
    selected: boolean
  }>
  onTimeClick?: (seconds: number) => void
}

const COLORS = {
  keep: 'rgba(96, 165, 250, 0.7)',      // blue
  cut: 'rgba(239, 68, 68, 0.5)',         // red
  mute: 'rgba(234, 179, 8, 0.5)',        // yellow
  selected: 'rgba(96, 165, 250, 0.15)',  // light blue bg
  background: 'transparent',
  baseline: 'rgba(148, 163, 184, 0.2)',  // gray
}

export function WaveformDisplay({ amplitudes, duration, words, onTimeClick }: WaveformDisplayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Build a lookup: for each amplitude sample index, find the matching word action
  const wordLookup = useMemo(() => {
    if (!words || words.length === 0 || amplitudes.length === 0 || duration <= 0) return null

    const sampleDuration = duration / amplitudes.length
    const lookup: Array<{ action: 'keep' | 'cut' | 'mute'; selected: boolean }> = []

    for (let i = 0; i < amplitudes.length; i++) {
      const sampleTime = i * sampleDuration
      let matched = false

      for (const word of words) {
        if (sampleTime >= word.start && sampleTime < word.end) {
          lookup.push({ action: word.action, selected: word.selected })
          matched = true
          break
        }
      }

      if (!matched) {
        lookup.push({ action: 'keep', selected: false })
      }
    }

    return lookup
  }, [amplitudes, duration, words])

  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container || amplitudes.length === 0) return

    const dpr = window.devicePixelRatio || 1
    const rect = container.getBoundingClientRect()
    const width = rect.width
    const height = 80

    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, height)

    const barWidth = Math.max(1, width / amplitudes.length)
    const centerY = height / 2

    // Draw baseline
    ctx.strokeStyle = COLORS.baseline
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, centerY)
    ctx.lineTo(width, centerY)
    ctx.stroke()

    // Draw waveform bars
    for (let i = 0; i < amplitudes.length; i++) {
      const x = (i / amplitudes.length) * width
      const amp = amplitudes[i]
      const barHeight = amp * (height * 0.8)

      // Draw selection highlight background
      if (wordLookup && wordLookup[i]?.selected) {
        ctx.fillStyle = COLORS.selected
        ctx.fillRect(x, 0, barWidth + 0.5, height)
      }

      // Get color from word action
      let color = COLORS.keep
      if (wordLookup && wordLookup[i]) {
        color = COLORS[wordLookup[i].action]
      }

      ctx.fillStyle = color
      ctx.fillRect(x, centerY - barHeight / 2, Math.max(barWidth - 0.5, 0.5), barHeight)
    }
  }, [amplitudes, wordLookup])

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onTimeClick || duration <= 0) return
    const canvas = canvasRef.current
    if (!canvas) return

    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    const ratio = x / rect.width
    const seconds = ratio * duration
    onTimeClick(seconds)
  }

  if (amplitudes.length === 0) return null

  return (
    <div ref={containerRef} className="w-full">
      <canvas
        ref={canvasRef}
        className="w-full cursor-pointer rounded"
        onClick={handleClick}
        title="Click to jump playhead"
      />
    </div>
  )
}

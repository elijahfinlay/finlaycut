import React, { useRef, useEffect, useMemo, useCallback, useState } from 'react'
import type { WaveformViewport, CutBoundary } from './transcript/types'

interface WaveformDisplayProps {
  amplitudes: number[]
  duration: number
  words?: Array<{
    start: number
    end: number
    action: 'keep' | 'cut' | 'mute'
    selected: boolean
  }>
  viewport?: WaveformViewport | null
  cutBoundaries?: CutBoundary[]
  playheadSeconds?: number
  fps?: number
  onTimeClick?: (seconds: number) => void
  onBoundaryDragStart?: (boundary: CutBoundary) => void
  onBoundaryDrag?: (boundary: CutBoundary, newTime: number) => void
  onBoundaryDragEnd?: () => void
  onViewportChange?: (viewport: WaveformViewport | null) => void
}

const COLORS = {
  keep: 'rgba(96, 165, 250, 0.7)',      // blue
  cut: 'rgba(239, 68, 68, 0.5)',         // red
  mute: 'rgba(234, 179, 8, 0.5)',        // yellow
  selected: 'rgba(96, 165, 250, 0.15)',  // light blue bg
  background: 'transparent',
  baseline: 'rgba(148, 163, 184, 0.2)',  // gray
}

const HANDLE_HIT_RADIUS = 10
const HANDLE_RADIUS = 6
const MIN_VIEWPORT_DURATION = 0.5

function formatDragTimecode(seconds: number, fps: number): string {
  const clamped = Math.max(0, seconds)
  const totalFrames = Math.round(clamped * fps)
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

export function WaveformDisplay({
  amplitudes,
  duration,
  words,
  viewport,
  cutBoundaries,
  playheadSeconds,
  fps = 24,
  onTimeClick,
  onBoundaryDragStart,
  onBoundaryDrag,
  onBoundaryDragEnd,
  onViewportChange,
}: WaveformDisplayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const playheadCanvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dragBoundary, setDragBoundary] = useState<CutBoundary | null>(null)
  const [dragInfo, setDragInfo] = useState<{ time: number; x: number } | null>(null)
  const [hoveringHandle, setHoveringHandle] = useState(false)
  const [containerWidth, setContainerWidth] = useState(0)
  const dragStartXRef = useRef<number>(0)
  const didDragRef = useRef(false)
  const draggingPlayheadRef = useRef(false)

  // Track container width so canvases redraw on resize
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width)
      }
    })
    ro.observe(container)
    setContainerWidth(container.getBoundingClientRect().width)
    return () => ro.disconnect()
  }, [])

  const isZoomed = viewport != null

  // Effective time range for rendering
  const viewStart = viewport?.startTime ?? 0
  const viewEnd = viewport?.endTime ?? duration
  const viewDuration = viewEnd - viewStart

  // Keep current viewport in a ref so the wheel handler doesn't need to re-register constantly
  const viewStateRef = useRef({ viewStart, viewEnd, viewDuration })
  viewStateRef.current = { viewStart, viewEnd, viewDuration }

  // Convert canvas X position to time
  const xToTime = useCallback(
    (x: number, canvasWidth: number) => {
      return viewStart + (x / canvasWidth) * viewDuration
    },
    [viewStart, viewDuration],
  )

  // Convert time to canvas X position
  const timeToX = useCallback(
    (time: number, canvasWidth: number) => {
      return ((time - viewStart) / viewDuration) * canvasWidth
    },
    [viewStart, viewDuration],
  )

  // Find the nearest boundary handle to a canvas X position
  const findNearBoundary = useCallback(
    (clientX: number): CutBoundary | null => {
      if (!cutBoundaries || cutBoundaries.length === 0) return null
      const container = containerRef.current
      if (!container) return null
      const rect = container.getBoundingClientRect()
      const x = clientX - rect.left

      let closest: CutBoundary | null = null
      let closestDist = HANDLE_HIT_RADIUS

      for (const b of cutBoundaries) {
        const bx = timeToX(b.time, rect.width)
        const dist = Math.abs(x - bx)
        if (dist < closestDist) {
          closestDist = dist
          closest = b
        }
      }
      return closest
    },
    [cutBoundaries, timeToX],
  )

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

  // Draw the waveform
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || amplitudes.length === 0 || containerWidth <= 0) return

    const dpr = window.devicePixelRatio || 1
    const width = containerWidth
    const height = 96

    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, height)

    // Calculate the slice of amplitudes to render based on viewport
    const sampleDuration = duration / amplitudes.length
    const startSample = Math.max(0, Math.floor(viewStart / sampleDuration))
    const endSample = Math.min(amplitudes.length, Math.ceil(viewEnd / sampleDuration))
    const visibleCount = endSample - startSample

    if (visibleCount <= 0) return

    const barWidth = Math.max(1, width / visibleCount)
    const centerY = height / 2

    // Draw baseline
    ctx.strokeStyle = COLORS.baseline
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, centerY)
    ctx.lineTo(width, centerY)
    ctx.stroke()

    // Draw waveform bars
    for (let vi = 0; vi < visibleCount; vi++) {
      const i = startSample + vi
      const x = (vi / visibleCount) * width
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

    // Draw cut boundary handles
    if (cutBoundaries && cutBoundaries.length > 0) {
      for (const b of cutBoundaries) {
        const bx = timeToX(b.time, width)
        if (bx < -HANDLE_RADIUS || bx > width + HANDLE_RADIUS) continue

        // Vertical dashed line
        ctx.save()
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)'
        ctx.lineWidth = 1.5
        ctx.setLineDash([4, 3])
        ctx.beginPath()
        ctx.moveTo(bx, 0)
        ctx.lineTo(bx, height)
        ctx.stroke()
        ctx.restore()

        // Glow behind circle
        ctx.save()
        ctx.shadowColor = b.side === 'start' ? 'rgba(239, 68, 68, 0.6)' : 'rgba(96, 165, 250, 0.6)'
        ctx.shadowBlur = 8
        ctx.beginPath()
        ctx.arc(bx, centerY, HANDLE_RADIUS, 0, Math.PI * 2)
        ctx.fillStyle = b.side === 'start' ? 'rgba(239, 68, 68, 0.95)' : 'rgba(96, 165, 250, 0.95)'
        ctx.fill()
        ctx.restore()

        // White border on circle
        ctx.beginPath()
        ctx.arc(bx, centerY, HANDLE_RADIUS, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)'
        ctx.lineWidth = 2
        ctx.stroke()

        // Arrow indicator inside circle
        ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
        ctx.font = '9px sans-serif'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(b.side === 'start' ? '\u25C0' : '\u25B6', bx, centerY)
      }
    }
  }, [amplitudes, wordLookup, isZoomed, viewStart, viewEnd, duration, cutBoundaries, timeToX, containerWidth])

  // Draw playhead overlay (separate canvas so it can update without redrawing waveform)
  useEffect(() => {
    const canvas = playheadCanvasRef.current
    if (!canvas || containerWidth <= 0) return
    if (playheadSeconds == null || duration <= 0) return

    const dpr = window.devicePixelRatio || 1
    const width = containerWidth
    const height = 96

    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, height)

    const px = ((playheadSeconds - viewStart) / viewDuration) * width
    if (px < -1 || px > width + 1) return

    // Playhead line
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(px, 0)
    ctx.lineTo(px, height)
    ctx.stroke()

    // Small triangle at top
    ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
    ctx.beginPath()
    ctx.moveTo(px - 4, 0)
    ctx.lineTo(px + 4, 0)
    ctx.lineTo(px, 6)
    ctx.closePath()
    ctx.fill()
  }, [playheadSeconds, duration, viewStart, viewDuration, isZoomed, containerWidth])

  // Wheel handler for pan and zoom (uses non-passive listener for preventDefault)
  useEffect(() => {
    const overlay = playheadCanvasRef.current
    if (!overlay || !onViewportChange || duration <= 0) return

    const handleWheel = (e: WheelEvent) => {
      const vs = viewStateRef.current
      const rect = overlay.getBoundingClientRect()

      if (e.metaKey || e.ctrlKey) {
        // Zoom: Cmd/Ctrl + scroll
        e.preventDefault()
        const zoomFactor = e.deltaY > 0 ? 1.3 : 0.77
        const mouseX = e.clientX - rect.left
        const mouseTime = vs.viewStart + (mouseX / rect.width) * vs.viewDuration
        const newDuration = Math.max(MIN_VIEWPORT_DURATION, Math.min(duration, vs.viewDuration * zoomFactor))

        // If zoomed nearly all the way out, snap to full
        if (newDuration >= duration * 0.95) {
          onViewportChange(null)
          return
        }

        const ratio = vs.viewDuration > 0 ? (mouseTime - vs.viewStart) / vs.viewDuration : 0.5
        let s = mouseTime - ratio * newDuration
        let end = s + newDuration
        if (s < 0) { end += -s; s = 0 }
        if (end > duration) { s -= end - duration; end = duration; s = Math.max(0, s) }
        onViewportChange({ startTime: s, endTime: end })
      } else if (vs.viewDuration < duration * 0.95) {
        // Pan: regular scroll when zoomed
        e.preventDefault()
        const delta = e.deltaX !== 0 ? e.deltaX : e.deltaY
        const panAmount = (delta / rect.width) * vs.viewDuration * 0.5
        let s = vs.viewStart + panAmount
        let end = vs.viewEnd + panAmount
        if (s < 0) { end += -s; s = 0 }
        if (end > duration) { s -= end - duration; end = duration }
        onViewportChange({ startTime: Math.max(0, s), endTime: Math.min(duration, end) })
      }
    }

    overlay.addEventListener('wheel', handleWheel, { passive: false })
    return () => overlay.removeEventListener('wheel', handleWheel)
  }, [duration, onViewportChange])

  const endDrag = useCallback(() => {
    if (dragBoundary && didDragRef.current) {
      onBoundaryDragEnd?.()
    }
    setDragBoundary(null)
    setDragInfo(null)
  }, [dragBoundary, onBoundaryDragEnd])

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const near = findNearBoundary(e.clientX)
      if (near && onBoundaryDrag) {
        setDragBoundary(near)
        dragStartXRef.current = e.clientX
        didDragRef.current = false
        e.preventDefault()
        return
      }

      // Start playhead drag
      if (onTimeClick && viewDuration > 0) {
        draggingPlayheadRef.current = true
        const container = containerRef.current
        if (!container) return
        const rect = container.getBoundingClientRect()
        const x = e.clientX - rect.left
        const seconds = xToTime(x, rect.width)
        onTimeClick(seconds)
        e.preventDefault()
      }
    },
    [findNearBoundary, onBoundaryDrag, onTimeClick, viewDuration, xToTime],
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (dragBoundary && onBoundaryDrag) {
        const container = containerRef.current
        if (!container) return
        const rect = container.getBoundingClientRect()
        const x = e.clientX - rect.left
        const newTime = xToTime(x, rect.width)

        if (!didDragRef.current && Math.abs(e.clientX - dragStartXRef.current) > 3) {
          didDragRef.current = true
          onBoundaryDragStart?.(dragBoundary)
        }

        if (didDragRef.current) {
          onBoundaryDrag(dragBoundary, newTime)
          setDragInfo({ time: newTime, x })
        }
        return
      }

      // Playhead drag
      if (draggingPlayheadRef.current && onTimeClick) {
        e.preventDefault()
        const container = containerRef.current
        if (!container) return
        const rect = container.getBoundingClientRect()
        const x = e.clientX - rect.left
        const seconds = xToTime(x, rect.width)
        onTimeClick(seconds)
        return
      }

      // Hover cursor
      const near = findNearBoundary(e.clientX)
      setHoveringHandle(near != null)
    },
    [dragBoundary, onBoundaryDrag, onBoundaryDragStart, findNearBoundary, xToTime, onTimeClick],
  )

  const handleMouseUp = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (dragBoundary) {
        endDrag()
        return
      }

      if (draggingPlayheadRef.current) {
        draggingPlayheadRef.current = false
        // Final position update
        if (onTimeClick && viewDuration > 0) {
          const container = containerRef.current
          if (!container) return
          const rect = container.getBoundingClientRect()
          const x = e.clientX - rect.left
          const seconds = xToTime(x, rect.width)
          onTimeClick(seconds)
        }
      }
    },
    [dragBoundary, endDrag, onTimeClick, viewDuration, xToTime],
  )

  const handleMouseLeave = useCallback(() => {
    if (dragBoundary) endDrag()
    draggingPlayheadRef.current = false
    setHoveringHandle(false)
    setDragInfo(null)
  }, [dragBoundary, endDrag])

  if (amplitudes.length === 0) return null

  const cursorClass = dragBoundary || hoveringHandle ? 'cursor-ew-resize' : 'cursor-pointer'

  return (
    <div ref={containerRef} className="relative w-full">
      <canvas
        ref={canvasRef}
        className="block rounded"
        style={{ width: containerWidth > 0 ? containerWidth : '100%', height: 96 }}
      />
      <canvas
        ref={playheadCanvasRef}
        className={`absolute top-0 left-0 rounded ${cursorClass}`}
        style={{ width: containerWidth > 0 ? containerWidth : '100%', height: 96 }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        title={isZoomed ? 'Drag handles to adjust · Scroll to pan · Cmd+Scroll to zoom' : 'Click to seek · Cmd+Scroll to zoom'}
      />
      {dragInfo && (
        <div
          className="pointer-events-none absolute -top-7 rounded bg-black/80 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-white/90 shadow-lg backdrop-blur-sm"
          style={{ left: dragInfo.x, transform: 'translateX(-50%)' }}
        >
          {formatDragTimecode(dragInfo.time, fps)}
        </div>
      )}
    </div>
  )
}

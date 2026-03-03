import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

interface Chapter {
  title: string
  start_seconds: number
  summary: string
  selected: boolean
}

const OP_NAME = 'chapters'

export function ChapterDetection() {
  const {
    chapters: storedChapters,
    setChapters: storeChapters,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [chapters, setChapters] = useState<Chapter[]>(storedChapters)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Persist across tab switches
  useEffect(() => {
    storeChapters(chapters)
  }, [chapters, storeChapters])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.chapters) setChapters(op.result.chapters)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleDetect = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setChapters([])
    setResult(null)
    try {
      const data = await apiFetch('/api/chapters/detect', { method: 'POST' })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setChapters(data.chapters || [])
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setChapters(data.chapters || [])
      }
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setResult({ error: err.message })
      toast.error(err.message)
    }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      const data = await apiFetch('/api/chapters/apply', {
        method: 'POST',
        body: JSON.stringify({ chapters })
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Chapter markers added')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleCopyYouTube = async () => {
    try {
      const data = await apiFetch('/api/chapters/export-youtube', {
        method: 'POST',
        body: JSON.stringify(chapters.filter((c) => c.selected))
      })
      if (data.text) {
        navigator.clipboard.writeText(data.text)
        toast.success('Copied YouTube chapters to clipboard')
      }
    } catch {
      toast.error('Failed to export chapters')
    }
  }

  const toggleChapter = (index: number) => {
    setChapters((prev) =>
      prev.map((c, i) => (i === index ? { ...c, selected: !c.selected } : c))
    )
  }

  const updateTitle = (index: number, title: string) => {
    setChapters((prev) =>
      prev.map((c, i) => (i === index ? { ...c, title } : c))
    )
  }

  const handleJumpToChapter = async (seconds: number) => {
    try {
      await apiFetch('/api/resolve/playhead', {
        method: 'POST',
        body: JSON.stringify({ seconds })
      })
    } catch {
      // Ignore jump errors
    }
  }

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Chapter Detection</h2>
        <p className="feature-description">
          AI analyzes your transcript to find natural topic changes. Creates timeline markers
          with chapter titles and exports YouTube-ready timestamps.
        </p>
      </div>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Detecting...
        </Button>
      ) : (
        <Button onClick={handleDetect} size="lg">
          Detect Chapters
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {chapters.length > 0 && (
        <Card>
          <div className="space-y-4">
            <h3 className="text-sm font-semibold text-resolve-text">
              Detected Chapters ({chapters.length})
            </h3>

            <div className="space-y-2">
              {chapters.map((ch, i) => (
                <div
                  key={i}
                  onClick={() => handleJumpToChapter(ch.start_seconds)}
                  className={`flex items-start gap-3 p-3 rounded-lg transition-colors ${
                    ch.selected ? 'bg-blue-500/10' : 'bg-resolve-surfaceLight opacity-50'
                  }`}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleChapter(i)
                    }}
                    className={`mt-1 w-4 h-4 rounded border flex-shrink-0 ${
                      ch.selected ? 'bg-blue-500 border-blue-500' : 'border-resolve-border'
                    }`}
                  >
                    {ch.selected && (
                      <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-resolve-textMuted">
                        {formatTime(ch.start_seconds)}
                      </span>
                      <input
                        type="text"
                        value={ch.title}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => updateTitle(i, e.target.value)}
                        className="flex-1 bg-transparent border-b border-transparent hover:border-resolve-border focus:border-resolve-accent text-sm text-resolve-text outline-none transition-colors"
                      />
                    </div>
                    {ch.summary && (
                      <p className="text-xs text-resolve-textMuted mt-1">{ch.summary}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="flex gap-3">
              <ConfirmApply onConfirm={handleApply} loading={applying}>
                Add Markers
              </ConfirmApply>
              <Button variant="ghost" onClick={handleCopyYouTube}>
                Copy YouTube Chapters
              </Button>
            </div>
          </div>
        </Card>
      )}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
      {result?.success && (
        <Card className="border-resolve-success/30">
          <p className="text-sm text-resolve-success">{result.message}</p>
        </Card>
      )}
    </div>
  )
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

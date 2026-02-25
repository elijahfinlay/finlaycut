import React, { useState } from 'react'
import { Button } from '../common/Button'
import { TimelineBanner, ConfirmApply } from '../common/TimelineGuard'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface Chapter {
  title: string
  start_seconds: number
  summary: string
  selected: boolean
}

export function ChapterDetection() {
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [detecting, setDetecting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message } = useProgress('chapters')

  const handleDetect = async () => {
    setDetecting(true)
    setChapters([])
    setResult(null)
    try {
      const data = await apiFetch('/api/chapters/detect', { method: 'POST' })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setChapters(data.chapters || [])
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setDetecting(false)
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
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setApplying(false)
    }
  }

  const handleCopyYouTube = async () => {
    const data = await apiFetch('/api/chapters/export-youtube', {
      method: 'POST',
      body: JSON.stringify(chapters.filter((c) => c.selected))
    })
    if (data.text) {
      navigator.clipboard.writeText(data.text)
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

  return (
    <div className="feature-panel">
      <TimelineBanner />
      <div>
        <h2 className="feature-title">Chapter Detection</h2>
        <p className="feature-description">
          AI analyzes your transcript to find natural topic changes. Creates timeline markers
          with chapter titles and exports YouTube-ready timestamps.
        </p>
      </div>

      <Button onClick={handleDetect} loading={detecting} size="lg">
        Detect Chapters
      </Button>

      {detecting && <ProgressBar progress={progress} message={message} />}

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
                  className={`flex items-start gap-3 p-3 rounded-lg transition-colors ${
                    ch.selected ? 'bg-blue-500/10' : 'bg-resolve-surfaceLight opacity-50'
                  }`}
                >
                  <button
                    onClick={() => toggleChapter(i)}
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

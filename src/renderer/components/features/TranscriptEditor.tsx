import React, { useState, useCallback } from 'react'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

interface TranscriptWord {
  word: string
  start: number
  end: number
  probability: number
  deleted: boolean
}

export function TranscriptEditor() {
  const [words, setWords] = useState<TranscriptWord[]>([])
  const [transcribing, setTranscribing] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [language, setLanguage] = useState('')
  const [selectionStart, setSelectionStart] = useState<number | null>(null)
  const { progress, message } = useProgress('transcript_editor')

  const handleTranscribe = async () => {
    setTranscribing(true)
    setWords([])
    setResult(null)
    try {
      const data = await apiFetch('/api/transcript-editor/transcribe', {
        method: 'POST',
        body: JSON.stringify({ language: language || null })
      })
      if (data.error) {
        setResult({ error: data.error })
      } else {
        setWords(
          (data.words || []).map((w: any) => ({ ...w, deleted: false }))
        )
      }
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setTranscribing(false)
    }
  }

  const toggleWord = (index: number) => {
    // If shift-clicking, select a range
    setWords((prev) =>
      prev.map((w, i) => (i === index ? { ...w, deleted: !w.deleted } : w))
    )
  }

  const handleWordMouseDown = (index: number) => {
    setSelectionStart(index)
  }

  const handleWordMouseUp = (index: number) => {
    if (selectionStart !== null && selectionStart !== index) {
      const from = Math.min(selectionStart, index)
      const to = Math.max(selectionStart, index)
      // Check if the range is mostly deleted or not
      const rangeWords = words.slice(from, to + 1)
      const mostlyDeleted = rangeWords.filter((w) => w.deleted).length > rangeWords.length / 2
      setWords((prev) =>
        prev.map((w, i) =>
          i >= from && i <= to ? { ...w, deleted: !mostlyDeleted } : w
        )
      )
    }
    setSelectionStart(null)
  }

  const selectSentence = (index: number) => {
    // Find sentence boundaries (periods, question marks, etc.)
    let start = index
    let end = index
    while (start > 0 && !words[start - 1].word.match(/[.!?]$/)) start--
    while (end < words.length - 1 && !words[end].word.match(/[.!?]$/)) end++

    const sentenceWords = words.slice(start, end + 1)
    const mostlyDeleted = sentenceWords.filter((w) => w.deleted).length > sentenceWords.length / 2

    setWords((prev) =>
      prev.map((w, i) =>
        i >= start && i <= end ? { ...w, deleted: !mostlyDeleted } : w
      )
    )
  }

  const handleApply = async () => {
    setApplying(true)
    setResult(null)
    try {
      // Send the kept segments (non-deleted) to the backend
      const segments: { start: number; end: number }[] = []
      let currentStart: number | null = null

      for (let i = 0; i < words.length; i++) {
        if (!words[i].deleted) {
          if (currentStart === null) currentStart = words[i].start
        } else {
          if (currentStart !== null) {
            segments.push({ start: currentStart, end: words[i - 1].end })
            currentStart = null
          }
        }
      }
      // Don't forget the last segment
      if (currentStart !== null && words.length > 0) {
        segments.push({ start: currentStart, end: words[words.length - 1].end })
      }

      const data = await apiFetch('/api/transcript-editor/apply', {
        method: 'POST',
        body: JSON.stringify({ kept_segments: segments })
      })
      setResult(data)
    } catch (err: any) {
      setResult({ error: err.message })
    } finally {
      setApplying(false)
    }
  }

  const handleSelectAll = () => {
    setWords((prev) => prev.map((w) => ({ ...w, deleted: true })))
  }

  const handleDeselectAll = () => {
    setWords((prev) => prev.map((w) => ({ ...w, deleted: false })))
  }

  const deletedCount = words.filter((w) => w.deleted).length
  const deletedSeconds = words
    .filter((w) => w.deleted)
    .reduce((acc, w) => acc + (w.end - w.start), 0)
  const totalSeconds = words.length > 0 ? words[words.length - 1].end - words[0].start : 0

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Transcript Editor</h2>
        <p className="feature-description">
          Edit your video by editing text. Transcribe your footage, then click or
          drag to select words you want to cut. The video gets trimmed to match.
        </p>
      </div>

      {/* Language + Transcribe */}
      <Card>
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="text-sm text-resolve-textDim mb-2 block">Language</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text"
            >
              <option value="">Auto-detect</option>
              <option value="en">English</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
              <option value="de">German</option>
              <option value="ja">Japanese</option>
              <option value="ko">Korean</option>
              <option value="zh">Chinese</option>
              <option value="pt">Portuguese</option>
              <option value="it">Italian</option>
            </select>
          </div>
          <Button onClick={handleTranscribe} loading={transcribing} size="lg">
            Transcribe
          </Button>
        </div>
      </Card>

      {transcribing && <ProgressBar progress={progress} message={message} />}

      {/* Transcript */}
      {words.length > 0 && (
        <>
          {/* Toolbar */}
          <Card className="!py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-sm text-resolve-textDim">
                  {deletedCount} words selected for removal
                </span>
                {deletedCount > 0 && (
                  <span className="text-xs text-resolve-error font-mono">
                    -{deletedSeconds.toFixed(1)}s
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="sm" onClick={handleDeselectAll}>
                  Keep All
                </Button>
                <Button variant="ghost" size="sm" onClick={handleSelectAll}>
                  Delete All
                </Button>
              </div>
            </div>
          </Card>

          {/* Instructions */}
          <div className="text-xs text-resolve-textMuted space-y-1 px-1">
            <p>Click a word to delete/keep it. Drag across words to select a range. Double-click to select a whole sentence.</p>
          </div>

          {/* The transcript itself */}
          <Card className="!p-4">
            <div
              className="leading-8 select-none"
              style={{ userSelect: 'none' }}
            >
              {words.map((word, i) => (
                <span
                  key={i}
                  onMouseDown={() => handleWordMouseDown(i)}
                  onMouseUp={() => handleWordMouseUp(i)}
                  onClick={() => {
                    if (selectionStart === null || selectionStart === i) toggleWord(i)
                  }}
                  onDoubleClick={(e) => {
                    e.preventDefault()
                    selectSentence(i)
                  }}
                  className={`inline-block px-0.5 py-0.5 mx-0.5 rounded cursor-pointer transition-all duration-100 text-sm
                    ${
                      word.deleted
                        ? 'bg-resolve-error/20 text-resolve-error line-through opacity-60 hover:opacity-80'
                        : 'hover:bg-resolve-accent/10 text-resolve-text'
                    }
                  `}
                  title={`${formatTime(word.start)} - ${formatTime(word.end)}`}
                >
                  {word.word}
                </span>
              ))}
            </div>
          </Card>

          {/* Stats + Apply */}
          <Card className="!py-3">
            <div className="flex items-center justify-between">
              <div className="text-sm text-resolve-textDim">
                <span className="text-resolve-text font-medium">
                  {formatDuration(totalSeconds - deletedSeconds)}
                </span>
                {' '}kept out of{' '}
                <span>{formatDuration(totalSeconds)}</span>
              </div>
              <Button
                onClick={handleApply}
                variant="success"
                loading={applying}
                disabled={deletedCount === 0}
              >
                Apply Cuts ({deletedCount} words)
              </Button>
            </div>
          </Card>
        </>
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

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

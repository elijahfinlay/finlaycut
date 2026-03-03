import React, { useState, useEffect } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { ConfirmApply } from '../common/TimelineGuard'
import { Slider } from '../common/Slider'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useOperationStore } from '../../stores/operationStore'

const OP_NAME = 'captions'

export function CaptionGenerator() {
  const {
    captions: storedCaptions,
    srtContent: storedSrt,
    setCaptions: storeCaptions,
    setSrtContent: storeSrt,
  } = useFeatureDataStore()

  const op = useOperationStore((s) => s.operations[OP_NAME])
  const isRunning = op?.status === 'running'

  const [language, setLanguage] = useState('')
  const [fontSize, setFontSize] = useState(48)
  const [position, setPosition] = useState('bottom')
  const [animation, setAnimation] = useState('none')
  const [captions, setCaptions] = useState<any[]>(storedCaptions)
  const [srtContent, setSrtContent] = useState(storedSrt)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)

  // Persist captions across tab switches
  useEffect(() => {
    storeCaptions(captions)
  }, [captions, storeCaptions])

  useEffect(() => {
    storeSrt(srtContent)
  }, [srtContent, storeSrt])

  // Pickup: if operation completed while unmounted
  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      if (op.result.captions) setCaptions(op.result.captions)
      if (op.result.srt) setSrtContent(op.result.srt)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setResult({ error: op.error })
    }
  }, [op?.status, op?.result, op?.error])

  const handleGenerate = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setCaptions([])
    setResult(null)
    try {
      const data = await apiFetch('/api/captions/generate', {
        method: 'POST',
        body: JSON.stringify({
          language: language || null,
          style: {
            font_size: fontSize,
            position,
            animation
          },
          words_per_caption: 5,
          max_chars_per_line: 42
        })
      })
      if (data.error) {
        useOperationStore.getState().failOperation(OP_NAME, data.error)
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        useFeatureDataStore.getState().setCaptions(data.captions || [])
        useFeatureDataStore.getState().setSrtContent(data.srt || '')
        useOperationStore.getState().completeOperation(OP_NAME, data)
        setCaptions(data.captions || [])
        setSrtContent(data.srt || '')
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
      const data = await apiFetch('/api/captions/apply', {
        method: 'POST',
        body: JSON.stringify({
          captions,
          style: { font_size: fontSize, position, animation },
          srt: srtContent
        })
      })
      setResult(data)
      if (data.success) toast.success(data.message || 'Captions applied successfully')
      else if (data.error) toast.error(data.error)
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setApplying(false)
    }
  }

  const handleJumpToCaption = async (seconds: number) => {
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
        <h2 className="feature-title">Automated Captions</h2>
        <p className="feature-description">
          AI-powered transcription in 50+ languages. Generate styled subtitles with
          customizable appearance and word-by-word highlight animations.
        </p>
      </div>

      {/* Settings */}
      <Card>
        <div className="space-y-5">
          <div>
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
              <option value="ru">Russian</option>
              <option value="ar">Arabic</option>
              <option value="hi">Hindi</option>
            </select>
          </div>

          <Slider
            label="Font Size"
            value={fontSize}
            onChange={setFontSize}
            min={24}
            max={96}
            unit="px"
          />

          <div>
            <label className="text-sm text-resolve-textDim mb-2 block">Position</label>
            <div className="flex gap-2">
              {['top', 'center', 'bottom'].map((pos) => (
                <button
                  key={pos}
                  onClick={() => setPosition(pos)}
                  className={`flex-1 py-2 rounded-lg text-sm capitalize transition-colors ${
                    position === pos
                      ? 'bg-resolve-accent text-white'
                      : 'bg-resolve-bg text-resolve-textDim hover:bg-resolve-surfaceLight'
                  }`}
                >
                  {pos}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-sm text-resolve-textDim mb-2 block">Animation</label>
            <div className="flex gap-2">
              {[
                { value: 'none', label: 'None' },
                { value: 'word_highlight', label: 'Word Highlight' },
                { value: 'karaoke', label: 'Karaoke' }
              ].map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setAnimation(opt.value)}
                  className={`flex-1 py-2 rounded-lg text-sm transition-colors ${
                    animation === opt.value
                      ? 'bg-resolve-accent text-white'
                      : 'bg-resolve-bg text-resolve-textDim hover:bg-resolve-surfaceLight'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {isRunning ? (
        <Button disabled size="lg" variant="ghost">
          Generating...
        </Button>
      ) : (
        <Button onClick={handleGenerate} size="lg">
          Generate Captions
        </Button>
      )}

      {isRunning && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {/* Caption Preview */}
      {captions.length > 0 && (
        <Card>
          <div className="space-y-4">
              <h3 className="text-sm font-semibold text-resolve-text">
                Preview ({captions.length} captions)
              </h3>
              <div className="max-h-64 overflow-y-auto space-y-1 font-mono text-sm">
                {captions.slice(0, 30).map((cap, i) => (
                  <button
                    key={i}
                    onClick={() => handleJumpToCaption(cap.start)}
                    className="w-full flex items-center gap-3 px-3 py-1.5 rounded bg-resolve-bg hover:bg-resolve-surfaceLight text-left transition-colors"
                  >
                    <span className="text-resolve-textMuted text-xs w-16 flex-shrink-0">
                      {formatTime(cap.start)}
                    </span>
                    <span className="text-resolve-text">{cap.text}</span>
                  </button>
                ))}
              {captions.length > 30 && (
                <p className="text-resolve-textMuted text-xs px-3">
                  ...and {captions.length - 30} more
                </p>
              )}
            </div>

            <div className="flex gap-3">
              <ConfirmApply onConfirm={handleApply} loading={applying}>
                Apply to Timeline
              </ConfirmApply>
              <Button
                variant="ghost"
                onClick={() => navigator.clipboard.writeText(srtContent)}
              >
                Copy SRT
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

import React, { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch, getBaseUrl } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'

type ExportFormat = 'edl' | 'fcpxml'

export function ExportPanel() {
  const [format, setFormat] = useState<ExportFormat>('edl')
  const [exporting, setExporting] = useState(false)
  const [result, setResult] = useState<any>(null)
  const { progress, message, elapsed, eta } = useProgress('export')

  const handleExport = async () => {
    setExporting(true)
    setResult(null)
    try {
      const data = await apiFetch('/api/export/generate', {
        method: 'POST',
        body: JSON.stringify({ format }),
      })
      if (data.error) {
        setResult({ error: data.error })
        toast.error(data.error)
      } else {
        setResult(data)
        toast.success(`Exported ${data.clip_count} clips as ${format.toUpperCase()}`)
      }
    } catch (err: any) {
      setResult({ error: err.message })
      toast.error(err.message)
    } finally {
      setExporting(false)
    }
  }

  const handleDownload = () => {
    if (!result?.filename) return
    const url = `${getBaseUrl()}/api/export/download/${encodeURIComponent(result.filename)}`
    window.open(url, '_blank')
  }

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Export Timeline</h2>
        <p className="feature-description">
          Export your timeline as EDL or FCPXML for use in other editing software
          like Premiere Pro, Final Cut Pro, or Avid.
        </p>
      </div>

      <Card>
        <label className="text-sm text-resolve-textDim mb-2 block">Export Format</label>
        <div className="flex gap-3">
          <button
            onClick={() => setFormat('edl')}
            className={`flex-1 px-4 py-3 rounded-lg border text-sm font-medium transition-all ${
              format === 'edl'
                ? 'bg-resolve-accent/15 border-resolve-accent text-resolve-accent'
                : 'bg-resolve-bg border-resolve-border text-resolve-textDim hover:border-resolve-textMuted'
            }`}
          >
            <div>EDL</div>
            <div className="text-xs mt-1 font-normal opacity-70">
              Universal format, works with most NLEs
            </div>
          </button>
          <button
            onClick={() => setFormat('fcpxml')}
            className={`flex-1 px-4 py-3 rounded-lg border text-sm font-medium transition-all ${
              format === 'fcpxml'
                ? 'bg-resolve-accent/15 border-resolve-accent text-resolve-accent'
                : 'bg-resolve-bg border-resolve-border text-resolve-textDim hover:border-resolve-textMuted'
            }`}
          >
            <div>FCPXML</div>
            <div className="text-xs mt-1 font-normal opacity-70">
              Final Cut Pro XML, richer metadata
            </div>
          </button>
        </div>
      </Card>

      <Button onClick={handleExport} loading={exporting} size="lg">
        Export as {format.toUpperCase()}
      </Button>

      {exporting && <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />}

      {result && !result.error && (
        <Card className="border-resolve-success/30">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-resolve-success font-medium">Export Complete</p>
                <p className="text-xs text-resolve-textMuted mt-1">
                  {result.timeline_name} — {result.clip_count} clips as {result.format?.toUpperCase()}
                </p>
              </div>
              <Button size="sm" onClick={handleDownload}>
                Download
              </Button>
            </div>
            <p className="text-xs text-resolve-textMuted font-mono break-all">
              {result.filename}
            </p>
          </div>
        </Card>
      )}

      {result?.error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{result.error}</p>
        </Card>
      )}
    </div>
  )
}

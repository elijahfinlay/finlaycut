import React, { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { ProgressBar } from '../layout/ProgressBar'
import { apiFetch } from '../../hooks/useApi'
import { useProgress } from '../../hooks/useWebSocket'
import { useOperationStore } from '../../stores/operationStore'
import { useFeatureDataStore } from '../../stores/featureDataStore'
import { useProjectStore } from '../../stores/projectStore'

const OP_NAME = 'clip_search'
const GEMINI_API_KEY_URL = 'https://aistudio.google.com/app/apikey'

interface TimelineOption {
  index: number
  name: string
  is_current?: boolean
}

function formatTimecode(seconds: number, fps: number): string {
  const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 24
  const totalFrames = Math.max(0, Math.round(seconds * safeFps))
  const frames = totalFrames % Math.round(safeFps)
  const totalSeconds = Math.floor(totalFrames / Math.round(safeFps))
  const secs = totalSeconds % 60
  const mins = Math.floor(totalSeconds / 60) % 60
  const hours = Math.floor(totalSeconds / 3600)
  return `${hours.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs
    .toString()
    .padStart(2, '0')}:${frames.toString().padStart(2, '0')}`
}

function formatScope(scope: any): string {
  if (!scope) return 'No analysis scope'
  const label = scope.mode === 'in_out' ? 'In/Out range' : 'Full timeline'
  return `${label} - ${scope.in_seconds.toFixed(1)}s to ${scope.out_seconds.toFixed(1)}s`
}

export function ClipSearch() {
  const op = useOperationStore((s) => s.operations[OP_NAME])
  const { progress, message, elapsed, eta } = useProgress(OP_NAME)
  const query = useFeatureDataStore((s) => s.clipSearchQuery)
  const results = useFeatureDataStore((s) => s.clipSearchResults)
  const status = useFeatureDataStore((s) => s.clipSearchStatus)
  const setClipSearchQuery = useFeatureDataStore((s) => s.setClipSearchQuery)
  const setClipSearchResults = useFeatureDataStore((s) => s.setClipSearchResults)
  const setClipSearchStatus = useFeatureDataStore((s) => s.setClipSearchStatus)
  const {
    hasPendingResolveChange,
    pendingTimelineName,
    timelineName,
    framerate,
    refreshResolve,
    setActiveFeature,
  } = useProjectStore()

  const [loadingStatus, setLoadingStatus] = useState(false)
  const [querying, setQuerying] = useState(false)
  const [overlaying, setOverlaying] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [switchingTimeline, setSwitchingTimeline] = useState(false)
  const [timelines, setTimelines] = useState<TimelineOption[]>([])
  const [selectedTimelineIndex, setSelectedTimelineIndex] = useState('')
  const [error, setError] = useState<string | null>(null)

  const isRunning = op?.status === 'running'
  const fps = Number.parseFloat(framerate || '24') || 24
  const analysis = status?.analysis
  const providerReady = status?.has_provider_key
  const selectedTimeline = timelines.find((timeline) => String(timeline.index) === selectedTimelineIndex)
  const currentTimelineLabel = selectedTimeline?.name || timelineName || 'Current timeline'
  const timelineActionsDisabled =
    hasPendingResolveChange || isRunning || querying || overlaying || clearing || switchingTimeline

  const loadStatus = async (loadResults = true) => {
    if (hasPendingResolveChange) return
    setLoadingStatus(true)
    setError(null)
    try {
      const data = await apiFetch('/api/clip-search/status')
      if (data.error) {
        throw new Error(data.error)
      }
      setClipSearchStatus({
        analysis_key: data.analysis_key,
        current_scope: data.current_scope,
        analysis: data.analysis,
        transcript_available: data.transcript_available,
        vision_provider: data.vision_provider,
        has_provider_key: data.has_provider_key,
      })
      if (loadResults && data.analysis) {
        const queried = await apiFetch('/api/clip-search/query', {
          method: 'POST',
          body: JSON.stringify({ query, limit: 250 }),
        })
        if (!queried.error) {
          setClipSearchResults(queried.results || [])
        } else {
          setClipSearchResults([])
        }
      } else if (loadResults) {
        setClipSearchResults([])
      }
    } catch (err: any) {
      setError(err.message || 'Could not load clip search status')
    } finally {
      setLoadingStatus(false)
    }
  }

  const loadTimelines = async () => {
    if (hasPendingResolveChange) return
    try {
      const data = await apiFetch('/api/resolve/timelines')
      const availableTimelines = Array.isArray(data.timelines) ? data.timelines : []
      setTimelines(availableTimelines)

      const currentTimeline =
        availableTimelines.find((timeline: TimelineOption) => timeline.is_current) ||
        availableTimelines.find((timeline: TimelineOption) => timeline.name === timelineName)

      if (currentTimeline) {
        setSelectedTimelineIndex(String(currentTimeline.index))
      } else if (availableTimelines.length === 1) {
        setSelectedTimelineIndex(String(availableTimelines[0].index))
      } else if (availableTimelines.length === 0) {
        setSelectedTimelineIndex('')
      }
    } catch (err: any) {
      setError(err.message || 'Could not load Resolve timelines')
    }
  }

  useEffect(() => {
    void loadTimelines()
    void loadStatus(true)
  }, [hasPendingResolveChange, timelineName])

  useEffect(() => {
    if (op?.status === 'complete' && op.result) {
      setClipSearchResults(op.result.results || [])
      void loadStatus(false)
      useOperationStore.getState().clearResult(OP_NAME)
    }
    if (op?.status === 'error' && op.error) {
      setError(op.error)
    }
  }, [op?.status, op?.result, op?.error, setClipSearchResults])

  const handleAnalyze = async () => {
    useOperationStore.getState().startOperation(OP_NAME)
    setError(null)
    try {
      const data = await apiFetch('/api/clip-search/analyze', {
        method: 'POST',
        body: JSON.stringify({ track_index: 1 }),
      })
      if (data.error) {
        throw new Error(data.error)
      }
      useOperationStore.getState().completeOperation(OP_NAME, data)
      setClipSearchResults(data.results || [])
      await loadStatus(false)
      toast.success(`Analyzed ${data.clip_count || 0} clips`)
    } catch (err: any) {
      useOperationStore.getState().failOperation(OP_NAME, err.message)
      setError(err.message)
      toast.error(err.message)
    }
  }

  const handleSearch = async () => {
    if (hasPendingResolveChange) return
    setQuerying(true)
    setError(null)
    try {
      const data = await apiFetch('/api/clip-search/query', {
        method: 'POST',
        body: JSON.stringify({ query, limit: 250 }),
      })
      if (data.error) {
        throw new Error(data.error)
      }
      setClipSearchResults(data.results || [])
      if (data.analysis) {
        setClipSearchStatus({
          analysis_key: data.analysis_key,
          current_scope: status?.current_scope || null,
          analysis: data.analysis,
          transcript_available: data.transcript_available,
          vision_provider: status?.vision_provider,
          has_provider_key: status?.has_provider_key,
        })
      }
    } catch (err: any) {
      setError(err.message || 'Search failed')
      toast.error(err.message || 'Search failed')
    } finally {
      setQuerying(false)
    }
  }

  const handleSelectTimeline = async (nextValue: string) => {
    setSelectedTimelineIndex(nextValue)
    if (!nextValue || hasPendingResolveChange || nextValue === selectedTimelineIndex) {
      return
    }

    setSwitchingTimeline(true)
    setError(null)
    try {
      const timelineIndex = Number.parseInt(nextValue, 10)
      const data = await apiFetch('/api/resolve/timeline/select', {
        method: 'POST',
        body: JSON.stringify({ timeline_index: timelineIndex }),
      })
      if (data.error || data.success === false) {
        throw new Error(data.error || `Could not switch to timeline ${timelineIndex}`)
      }
      if (refreshResolve) {
        await refreshResolve()
      }
      setClipSearchQuery('')
      setClipSearchResults([])
      setClipSearchStatus(null)
      toast.success(`Switched to ${data.timeline_name || 'the selected timeline'}`)
    } catch (err: any) {
      setError(err.message || 'Could not switch timeline')
      toast.error(err.message || 'Could not switch timeline')
      await loadTimelines()
    } finally {
      setSwitchingTimeline(false)
    }
  }

  const handleJump = async (frame: number) => {
    if (hasPendingResolveChange) return
    try {
      const data = await apiFetch('/api/resolve/playhead-frame', {
        method: 'POST',
        body: JSON.stringify({ frame }),
      })
      if (data.error) {
        throw new Error(data.error)
      }
    } catch (err: any) {
      toast.error(err.message || 'Could not jump to clip')
    }
  }

  const handleOverlay = async () => {
    if (hasPendingResolveChange) return
    setOverlaying(true)
    setError(null)
    try {
      const data = await apiFetch('/api/clip-search/overlay', {
        method: 'POST',
        body: JSON.stringify({ track_index: 1 }),
      })
      if (data.error) {
        throw new Error(data.error)
      }
      toast.success(data.message || 'Overlay imported')
    } catch (err: any) {
      setError(err.message || 'Could not create overlay')
      toast.error(err.message || 'Could not create overlay')
    } finally {
      setOverlaying(false)
    }
  }

  const handleClear = async () => {
    if (hasPendingResolveChange) return
    setClearing(true)
    setError(null)
    try {
      const data = await apiFetch('/api/clip-search/clear', {
        method: 'POST',
        body: JSON.stringify({ track_index: 1 }),
      })
      if (data.error) {
        throw new Error(data.error)
      }
      setClipSearchQuery('')
      setClipSearchResults([])
      await loadStatus(false)
      if (data.cleared) {
        toast.success(`Cleared ${data.clip_count || 0} analyzed clip${data.clip_count === 1 ? '' : 's'} from this scope`)
      } else {
        toast.info('No saved clip analysis was found for this timeline scope')
      }
    } catch (err: any) {
      setError(err.message || 'Could not clear clip analysis')
      toast.error(err.message || 'Could not clear clip analysis')
    } finally {
      setClearing(false)
    }
  }

  const handleOpenGeminiKeyPage = async () => {
    try {
      await window.api.openExternal(GEMINI_API_KEY_URL)
    } catch (err: any) {
      toast.error(err.message || 'Could not open Gemini API key page')
    }
  }

  return (
    <div className="feature-panel">
      <div>
        <h2 className="feature-title">Clip Search</h2>
        <p className="feature-description">
          Analyze visible clips, search visual labels and transcript text, and create a
          separate visual-description subtitle track.
        </p>
      </div>

      {hasPendingResolveChange && (
        <Card className="border-resolve-warning/30">
          <div className="space-y-3">
            <p className="text-sm text-resolve-warning">
              Clip Search is pinned to the previous timeline. Reconnect to
              {pendingTimelineName ? ` ${pendingTimelineName}` : ' the current Resolve timeline'} to analyze,
              jump, or overlay.
            </p>
            <p className="text-xs text-resolve-textMuted">
              Existing results stay visible until you refresh into the current timeline.
            </p>
          </div>
        </Card>
      )}

      <Card>
        <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-resolve-text">Analysis Status</h3>
              <p className="text-xs text-resolve-textMuted">
                Timeline: {currentTimelineLabel}
              </p>
              <p className="text-xs text-resolve-textMuted">
                {status?.current_scope ? formatScope(status.current_scope) : 'No scope loaded'}
              </p>
              {analysis && (
                <p className="text-xs text-resolve-textMuted">
                  {analysis.clip_count} clip{analysis.clip_count === 1 ? '' : 's'} indexed
                  {status?.transcript_available ? ' - transcript linked' : ' - visual labels only'}
                </p>
              )}
            </div>
            <div className="min-w-[16rem] space-y-2">
              <label className="block text-xs font-medium uppercase tracking-wide text-resolve-textMuted">
                Timeline To Analyze
              </label>
              <select
                value={selectedTimelineIndex}
                onChange={(e) => void handleSelectTimeline(e.target.value)}
                disabled={timelineActionsDisabled || timelines.length === 0}
                className="w-full rounded-lg border border-resolve-border bg-resolve-bg px-3 py-2 text-sm text-resolve-text"
              >
                {timelines.length === 0 && (
                  <option value="">
                    {timelineName || 'No timelines available'}
                  </option>
                )}
                {timelines.map((timeline) => (
                  <option key={timeline.index} value={timeline.index}>
                    {timeline.name}
                  </option>
                ))}
              </select>
              <p className="text-xs text-resolve-textMuted">
                Selecting another timeline switches Resolve and refreshes Clip Search to that timeline.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {!providerReady && (
              <>
                <Button variant="ghost" size="sm" onClick={() => setActiveFeature('settings')}>
                  Add Gemini Key
                </Button>
                <Button variant="ghost" size="sm" onClick={() => void handleOpenGeminiKeyPage()}>
                  Get API Key
                </Button>
              </>
            )}
            <Button
              variant="danger"
              onClick={handleClear}
              loading={clearing}
              disabled={timelineActionsDisabled || !analysis}
            >
              Clear Current Analysis
            </Button>
            <Button
              onClick={handleAnalyze}
              size="lg"
              loading={isRunning}
              disabled={timelineActionsDisabled || !providerReady}
            >
              {analysis ? 'Reanalyze Clips' : 'Analyze Clips'}
            </Button>
          </div>
        </div>
      </Card>

      {isRunning && (
        <ProgressBar progress={progress} message={message} elapsed={elapsed} eta={eta} />
      )}

      <Card>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="text"
              value={query}
              onChange={(e) => setClipSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !hasPendingResolveChange) {
                  void handleSearch()
                }
              }}
              disabled={timelineActionsDisabled || !analysis}
              placeholder={analysis ? 'Search dog, interview, sunset, title card...' : 'Analyze clips to enable search'}
              className="flex-1 min-w-[16rem] bg-resolve-bg border border-resolve-border rounded-lg px-3 py-2 text-sm text-resolve-text placeholder:text-resolve-textMuted"
            />
            <Button
              onClick={handleSearch}
              loading={querying}
              disabled={timelineActionsDisabled || !analysis}
            >
              Search
            </Button>
            <Button
              variant="ghost"
              onClick={handleOverlay}
              loading={overlaying}
              disabled={timelineActionsDisabled || !analysis || analysis.clip_count === 0}
            >
              Generate Overlay
            </Button>
          </div>
          {!status?.transcript_available && (
            <p className="text-xs text-resolve-textMuted">
              Transcript search is unavailable for this timeline until you run transcription.
            </p>
          )}
        </div>
      </Card>

      {loadingStatus && (
        <Card>
          <p className="text-sm text-resolve-textMuted">Loading clip-search status...</p>
        </Card>
      )}

      {results.length > 0 && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-resolve-text">Matches</h3>
            <span className="text-xs text-resolve-textMuted">{results.length} result{results.length === 1 ? '' : 's'}</span>
          </div>
          <div className="space-y-3 max-h-[36rem] overflow-y-auto">
            {results.map((result) => (
              <div
                key={result.clip_key}
                className="rounded-lg border border-resolve-border bg-resolve-bg/60 px-4 py-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-resolve-text">{result.clip_name}</span>
                      {result.shot_type && (
                        <span className="rounded bg-resolve-accent/10 px-2 py-0.5 text-[11px] text-resolve-accent">
                          {result.shot_type}
                        </span>
                      )}
                      {result.error_state && (
                        <span className="rounded bg-resolve-error/10 px-2 py-0.5 text-[11px] text-resolve-error">
                          {result.error_state}
                        </span>
                      )}
                    </div>
                    <p className="text-xs font-mono text-resolve-textMuted">
                      {formatTimecode(result.timeline_start_seconds, fps)} - {formatTimecode(result.timeline_end_seconds, fps)}
                    </p>
                    <p className="text-sm text-resolve-text">{result.summary_text}</p>
                    {result.error_message && (
                      <p className="text-xs text-resolve-error">
                        {result.error_message}
                      </p>
                    )}
                    {result.labels.length > 0 && (
                      <p className="text-xs text-resolve-textMuted">
                        {result.labels.slice(0, 6).join(', ')}
                      </p>
                    )}
                    {result.match_fields.length > 0 && (
                      <div className="space-y-1 pt-1">
                        {result.match_fields.map((field, index) => (
                          <p key={`${result.clip_key}-${field.field}-${index}`} className="text-xs text-resolve-textMuted">
                            <span className="uppercase tracking-wide text-[10px] text-resolve-accent">{field.field}</span>{' '}
                            {field.text}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleJump(result.timeline_start_frame)}
                    disabled={timelineActionsDisabled}
                  >
                    Jump
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {!loadingStatus && analysis && results.length === 0 && !isRunning && (
        <Card>
          <p className="text-sm text-resolve-textMuted">
            {query ? 'No clips matched this search.' : 'No analyzed clips available for the current scope.'}
          </p>
        </Card>
      )}

      {!analysis && !loadingStatus && (
        <Card>
          <p className="text-sm text-resolve-textMuted">
            Run clip analysis to build a searchable local index for this timeline scope.
          </p>
        </Card>
      )}

      {error && (
        <Card className="border-resolve-error/30">
          <p className="text-sm text-resolve-error">{error}</p>
        </Card>
      )}
    </div>
  )
}

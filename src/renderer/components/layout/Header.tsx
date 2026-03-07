import React, { useState } from 'react'
import { clsx } from 'clsx'
import { toast } from 'sonner'
import { useProjectStore } from '../../stores/projectStore'
import { apiFetch } from '../../hooks/useApi'

export function Header() {
  const {
    connected,
    projectName,
    timelineName,
    framerate,
    resolution,
    videoTracks,
    audioTracks,
    pendingTimelineName,
    refreshResolve,
    hasPendingResolveChange,
  } = useProjectStore()
  const [retrying, setRetrying] = useState(false)
  const [linking, setLinking] = useState(false)
  const statusDotClass = !connected
    ? 'bg-resolve-error'
    : hasPendingResolveChange
      ? 'bg-resolve-warning'
      : 'bg-resolve-success'
  const statusDotTitle = !connected
    ? 'Resolve disconnected'
    : hasPendingResolveChange
      ? 'Tool is pinned to a different timeline than the one currently open in Resolve'
      : 'Tool is synced to the currently open Resolve timeline'
  const canLinkClips = connected && Boolean(timelineName) && !hasPendingResolveChange && !linking

  const handleRetryConnect = async () => {
    if (!refreshResolve) return
    setRetrying(true)
    try {
      if (!connected) {
        await apiFetch('/api/resolve/reconnect', { method: 'POST' })
      }
      await refreshResolve()
    } catch {
      // Ignore errors and let regular polling continue.
    } finally {
      setRetrying(false)
    }
  }

  const handleLinkClips = async () => {
    if (!canLinkClips) {
      if (hasPendingResolveChange) {
        toast.info(`Reconnect to ${pendingTimelineName || 'the current Resolve timeline'} before linking clips.`)
      }
      return
    }

    setLinking(true)
    try {
      const data = await apiFetch('/api/resolve/link-clips', { method: 'POST' })
      if (data?.error || data?.success === false) {
        throw new Error(data?.error || 'Could not link clips on the current timeline')
      }
      toast.success(data?.message || 'Linked matching video and audio clips')
    } catch (err: any) {
      toast.error(err?.message || 'Could not link clips on the current timeline')
    } finally {
      setLinking(false)
    }
  }

  return (
    <header
      className="bg-resolve-surface border-b border-resolve-border flex-shrink-0 select-none"
      style={{ WebkitAppRegion: 'drag' } as any}
    >
      {/* Top row: connection + project */}
      <div className="min-h-10 flex flex-wrap items-center px-5 py-2 gap-3">
        {/* macOS traffic light spacer */}
        <div className="w-16 flex-shrink-0" />

        {/* Connection dot + label */}
        <div className="flex items-center gap-2">
          <div
            className={clsx(
              'w-2 h-2 rounded-full',
              statusDotClass
            )}
            title={statusDotTitle}
          />
          <span className="text-xs text-resolve-textMuted">
            {connected ? 'Connected' : 'Disconnected'}
          </span>
          <button
            onClick={handleRetryConnect}
            disabled={retrying}
            className={clsx(
              'px-2 py-0.5 text-xs rounded transition-colors',
              retrying
                ? 'bg-resolve-border text-resolve-textMuted cursor-wait'
                : 'bg-resolve-accent/15 text-resolve-accent hover:bg-resolve-accent/25'
            )}
            style={{ WebkitAppRegion: 'no-drag' } as any}
          >
            {retrying ? (connected ? 'Reconnecting...' : 'Retrying...') : connected ? 'Reconnect' : 'Retry'}
          </button>
        </div>

        {connected && projectName && (
          <>
            <div className="w-px h-4 bg-resolve-border" />
            <span className="text-xs text-resolve-textMuted truncate">
              {projectName}
            </span>
          </>
        )}
        {connected && (
          <button
            onClick={handleLinkClips}
            disabled={!canLinkClips}
            className={clsx(
              'px-2 py-0.5 text-xs rounded transition-colors',
              canLinkClips
                ? 'bg-resolve-success/15 text-resolve-success hover:bg-resolve-success/25'
                : 'bg-resolve-border text-resolve-textMuted cursor-not-allowed'
            )}
            title={
              !connected
                ? 'Resolve disconnected'
                : hasPendingResolveChange
                  ? 'Reconnect to the currently open Resolve timeline before linking clips'
                  : timelineName
                    ? 'Link matching video and audio clips on the current timeline'
                    : 'No timeline open in Resolve'
            }
            style={{ WebkitAppRegion: 'no-drag' } as any}
          >
            {linking ? 'Linking...' : 'Link Clips'}
          </button>
        )}
        <div className="flex-1" />
      </div>

      {/* Timeline bar — the hero element */}
      {connected ? (
        <div className="px-5 pb-3 pt-0.5">
          <div className={clsx(
            'flex flex-wrap items-center gap-3 px-4 py-2.5 rounded-lg',
            timelineName
              ? 'bg-resolve-accent/8 border border-resolve-accent/20'
              : 'bg-resolve-warning/8 border border-resolve-warning/20'
          )}>
            {/* Timeline icon */}
            <svg className={clsx('w-4 h-4 flex-shrink-0', timelineName ? 'text-resolve-accent' : 'text-resolve-warning')} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <rect x="2" y="3" width="20" height="18" rx="2" />
              <path d="M2 9h20" />
              <path d="M6 3v6" />
              <path d="M10 3v6" />
              <rect x="4" y="12" width="5" height="3" rx="0.5" fill="currentColor" opacity="0.4" />
              <rect x="11" y="12" width="7" height="3" rx="0.5" fill="currentColor" opacity="0.4" />
              <rect x="5" y="16.5" width="8" height="2.5" rx="0.5" fill="currentColor" opacity="0.2" />
            </svg>

            {timelineName ? (
              <div className="flex flex-1 flex-wrap items-center gap-3 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-resolve-textMuted font-medium uppercase tracking-wide">Timeline:</span>
                  <span className="text-sm font-semibold text-resolve-text truncate">
                    {timelineName}
                  </span>
                </div>

                {/* Meta chips */}
                <div className="flex flex-wrap items-center gap-2 xl:ml-auto">
                  {resolution && (
                    <span className="px-2 py-0.5 text-[11px] font-mono text-resolve-textMuted bg-resolve-bg/60 rounded">
                      {resolution}
                    </span>
                  )}
                  {framerate && (
                    <span className="px-2 py-0.5 text-[11px] font-mono text-resolve-textMuted bg-resolve-bg/60 rounded">
                      {framerate}fps
                    </span>
                  )}
                  <span className="px-2 py-0.5 text-[11px] font-mono text-resolve-textMuted bg-resolve-bg/60 rounded">
                    V{videoTracks} A{audioTracks}
                  </span>
                </div>
              </div>
            ) : (
              <span className="text-sm text-resolve-warning">
                No timeline open — select a timeline in Resolve
              </span>
            )}
          </div>
        </div>
      ) : (
        <div className="px-5 pb-3 pt-0.5">
          <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-resolve-error/8 border border-resolve-error/20">
            <div className="w-2 h-2 rounded-full bg-resolve-error flex-shrink-0" />
            <span className="text-sm text-resolve-error">
              Not connected to DaVinci Resolve
            </span>
            <span className="text-xs text-resolve-textMuted">
              — Open Resolve and set external scripting to Local
            </span>
          </div>
        </div>
      )}
    </header>
  )
}

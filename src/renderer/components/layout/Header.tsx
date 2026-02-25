import React from 'react'
import { clsx } from 'clsx'
import { useProjectStore } from '../../stores/projectStore'

export function Header() {
  const { connected, projectName, timelineName, framerate, resolution, videoTracks, audioTracks } =
    useProjectStore()

  return (
    <header className="h-12 bg-resolve-surface border-b border-resolve-border flex items-center px-5 gap-4 flex-shrink-0">
      {/* macOS traffic light spacer */}
      <div className="w-16 flex-shrink-0" style={{ WebkitAppRegion: 'drag' } as any} />

      {/* Connection status */}
      <div className="flex items-center gap-2">
        <div
          className={clsx(
            'w-2.5 h-2.5 rounded-full',
            connected ? 'bg-resolve-success animate-pulse' : 'bg-resolve-error'
          )}
        />
        <span className="text-sm text-resolve-textDim">
          {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      {connected && projectName && (
        <>
          <div className="w-px h-5 bg-resolve-border" />
          <span className="text-sm text-resolve-text font-medium truncate max-w-[200px]">
            {projectName}
          </span>

          {timelineName && (
            <>
              <div className="w-px h-5 bg-resolve-border" />
              <span className="text-sm text-resolve-textDim truncate max-w-[200px]">
                {timelineName}
              </span>
            </>
          )}

          {resolution && (
            <>
              <div className="w-px h-5 bg-resolve-border" />
              <span className="text-xs text-resolve-textMuted font-mono">
                {resolution} @ {framerate}fps
              </span>
            </>
          )}

          <div className="w-px h-5 bg-resolve-border" />
          <span className="text-xs text-resolve-textMuted">
            V:{videoTracks} A:{audioTracks}
          </span>
        </>
      )}

      <div className="flex-1" style={{ WebkitAppRegion: 'drag' } as any} />
    </header>
  )
}

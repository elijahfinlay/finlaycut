import React from 'react'
import { ConfirmApply } from '../../common/TimelineGuard'
import { formatDuration } from './types'

export interface TranscriptFooterProps {
  keptSeconds: number
  totalSeconds: number
  muteSeconds: number
  cutCount: number
  muteCount: number
  cutSeconds: number
  hasEdits: boolean
  applying: boolean
  transcriptMutationBlocked: boolean
  onApply: () => void
}

export function TranscriptFooter(props: TranscriptFooterProps) {
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="text-sm text-resolve-textDim">
        <span className="text-resolve-text font-medium">
          {formatDuration(props.keptSeconds)}
        </span>{' '}
        kept of <span>{formatDuration(props.totalSeconds)}</span>
        {props.muteSeconds > 0 && (
          <span className="ml-2 text-resolve-warning">
            ({props.muteSeconds.toFixed(1)}s muted)
          </span>
        )}
      </div>
      <ConfirmApply
        onConfirm={props.onApply}
        loading={props.applying}
        disabled={!props.hasEdits || props.transcriptMutationBlocked}
        description={`This will apply ${props.cutCount} cut words (${props.cutSeconds.toFixed(1)}s removed) and ${props.muteCount} muted words (${props.muteSeconds.toFixed(1)}s silenced).`}
      >
        Apply Edits
      </ConfirmApply>
    </div>
  )
}

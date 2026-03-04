import React from 'react'
import { Button } from '../../common/Button'
import type { SpeakerSummary } from './types'

export interface SpeakerPanelProps {
  speakers: SpeakerSummary[]
  onSpeakerAction: (speaker: string, mode: 'keepOnly' | 'cut' | 'keep') => void
  transcriptMutationBlocked: boolean
}

export function SpeakerPanel(props: SpeakerPanelProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-resolve-textMuted">
        <span>Speaker Selection</span>
        <span>{props.speakers.length} speaker{props.speakers.length !== 1 ? 's' : ''}</span>
      </div>
      <div className="space-y-2">
        {props.speakers.map((speaker) => (
          <div
            key={speaker.speaker}
            className="flex flex-col gap-3 rounded-lg border border-resolve-border bg-resolve-bg px-3 py-2 xl:flex-row xl:items-center xl:justify-between"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-resolve-text">{speaker.speaker}</span>
                {speaker.is_primary_suggestion && (
                  <span className="rounded bg-cyan-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-cyan-300">
                    Primary
                  </span>
                )}
              </div>
              <div className="text-xs text-resolve-textMuted">
                {speaker.word_count} words
                {' \u00b7 '}{speaker.total_duration.toFixed(1)}s
                {typeof speaker.avg_dbfs === 'number' ? ` \u00b7 ${speaker.avg_dbfs.toFixed(1)} dB avg` : ''}
                {typeof speaker.relative_db === 'number' ? ` \u00b7 ${speaker.relative_db.toFixed(1)} dB vs lead` : ''}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => props.onSpeakerAction(speaker.speaker, 'keepOnly')}
                disabled={props.transcriptMutationBlocked}
              >
                Keep Only
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={() => props.onSpeakerAction(speaker.speaker, 'cut')}
                disabled={props.transcriptMutationBlocked}
              >
                Cut
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => props.onSpeakerAction(speaker.speaker, 'keep')}
                disabled={props.transcriptMutationBlocked}
              >
                Keep
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

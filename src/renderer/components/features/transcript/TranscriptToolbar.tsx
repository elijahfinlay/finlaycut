import React from 'react'
import { Button } from '../../common/Button'
import type { SpeakerSummary } from './types'

export interface TranscriptToolbarProps {
  isPlaying: boolean
  isFollowing: boolean
  showTimestamps: boolean
  onPlayPause: () => void
  onToggleTimestamps: () => void

  selectedCount: number
  onCutSelection: () => void
  onMuteSelection: () => void
  onKeepSelection: () => void

  onFindFillers: () => void
  findingFillers: boolean
  onCutFillers: () => void
  hasDetectedFillers: boolean
  onMarkRepetitions: () => void
  findingRepetitions: boolean
  onQuickAnalyze: () => void
  quickAnalyzeRunning: boolean
  quickAnalyzeLabel: string
  autoDetectFillers: boolean
  onToggleAutoDetect: () => void
  fillerCount: number
  repeatedCount: number

  onToggleSilenceSettings: () => void
  showSilenceSettings: boolean
  onSpeakers: () => void
  analyzingSpeakers: boolean
  showSpeakers: boolean
  onToggleScriptPanel: () => void
  showScriptPanel: boolean
  onClearTranscript: () => void
  clearTranscriptDisabled: boolean

  allSelected: boolean
  onSelectAll: () => void
  onKeepAll: () => void

  cutCount: number
  muteCount: number
  heldClickAction: 'cut' | 'mute' | 'keep' | null
  primarySpeaker: SpeakerSummary | null

  onExport: () => void

  transcriptMutationBlocked: boolean
}

function ButtonGroup({ children }: { children: React.ReactNode }) {
  return (
    <div className="inline-flex items-center gap-0.5 bg-resolve-surface rounded-lg p-0.5">
      {children}
    </div>
  )
}

export function TranscriptToolbar(props: TranscriptToolbarProps) {
  return (
    <div className="space-y-2">
      {/* Row 1: Playback + Edit + Bulk */}
      <div className="flex flex-wrap items-center gap-2">
        <ButtonGroup>
          <Button
            size="sm"
            onClick={props.onPlayPause}
            className={props.isPlaying
              ? 'bg-resolve-success/20 hover:bg-resolve-success/30 text-resolve-success border border-resolve-success/40'
              : 'bg-resolve-accent hover:bg-resolve-accentHover text-white'}
            title={props.isPlaying ? 'Pause (Spacebar)' : 'Play (Spacebar)'}
          >
            {props.isPlaying
              ? (props.isFollowing ? '⏸ Following' : '⏸ Pause')
              : '▶ Play'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onToggleTimestamps}
            className={props.showTimestamps ? 'border border-resolve-accent/50 text-resolve-accent' : ''}
            title="Show word timestamps"
          >
            Time
          </Button>
        </ButtonGroup>

        <ButtonGroup>
          <Button
            variant="danger"
            size="sm"
            onClick={props.onCutSelection}
            disabled={props.selectedCount === 0 || props.transcriptMutationBlocked}
          >
            Cut
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onMuteSelection}
            disabled={props.selectedCount === 0 || props.transcriptMutationBlocked}
            className="border border-resolve-warning/30 text-resolve-warning hover:bg-resolve-warning/10"
          >
            Mute
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onKeepSelection}
            disabled={props.selectedCount === 0 || props.transcriptMutationBlocked}
            title="Hold K + click"
          >
            Keep
          </Button>
        </ButtonGroup>

        <div className="flex-1" />

        <ButtonGroup>
          <Button variant="ghost" size="sm" onClick={props.onSelectAll}>
            {props.allSelected ? 'Deselect All' : 'Select All'}
          </Button>
          <Button variant="ghost" size="sm" onClick={props.onKeepAll} disabled={props.transcriptMutationBlocked}>
            Reset
          </Button>
        </ButtonGroup>
      </div>

      {/* Row 2: Analysis tools + Export */}
      <div className="flex flex-wrap items-center gap-2">
        <ButtonGroup>
          <Button
            size="sm"
            onClick={props.onQuickAnalyze}
            loading={props.quickAnalyzeRunning}
            disabled={props.transcriptMutationBlocked}
            className="bg-resolve-accent hover:bg-resolve-accentHover text-white"
            title="Run the core cleanup pass: silences, filler words, retakes, and speakers"
          >
            {props.quickAnalyzeRunning ? props.quickAnalyzeLabel : 'Quick Analyze'}
          </Button>
        </ButtonGroup>

        <ButtonGroup>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onFindFillers}
            loading={props.findingFillers}
            disabled={props.transcriptMutationBlocked}
            title="Detect filler words (um, uh, hmm, er, ah) and mark them for cutting"
          >
            Fillers
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onCutFillers}
            disabled={props.transcriptMutationBlocked}
            className={props.hasDetectedFillers ? 'border border-orange-500/35 text-orange-300 hover:bg-orange-500/10' : ''}
            title="Mark highlighted filler candidates for cutting"
          >
            Cut Fillers
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onMarkRepetitions}
            loading={props.findingRepetitions}
            disabled={props.transcriptMutationBlocked}
            title="Find retakes and mark the non-best takes for cutting"
          >
            Retakes
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onToggleAutoDetect}
            className={props.autoDetectFillers ? 'border border-orange-500/50 text-orange-400' : ''}
            title="Highlight filler words and repeated consecutive words"
          >
            Highlight
          </Button>
        </ButtonGroup>

        <ButtonGroup>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onToggleSilenceSettings}
            disabled={props.transcriptMutationBlocked}
            className={props.showSilenceSettings ? 'border border-resolve-accent/50 text-resolve-accent' : ''}
            title="Show silence detection settings"
          >
            Silences
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onSpeakers}
            loading={props.analyzingSpeakers}
            disabled={props.transcriptMutationBlocked}
            className={props.showSpeakers ? 'border border-cyan-500/40 text-cyan-300' : ''}
            title="Detect speakers and label transcript words"
          >
            Speakers
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onToggleScriptPanel}
            disabled={props.transcriptMutationBlocked}
            className={props.showScriptPanel ? 'border border-resolve-success/40 text-resolve-success' : ''}
            title="Paste a target script and cut everything else"
          >
            Script
          </Button>
        </ButtonGroup>

        <div className="flex-1" />

        <ButtonGroup>
          <Button
            variant="ghost"
            size="sm"
            onClick={props.onClearTranscript}
            disabled={props.clearTranscriptDisabled}
            className="border border-resolve-error/30 text-resolve-error hover:bg-resolve-error/10"
            title="Clear the current transcript so you can transcribe again from a clean slate"
          >
            Clear Transcript
          </Button>
          <Button variant="ghost" size="sm" onClick={props.onExport} title="Export transcript as text">
            Export
          </Button>
        </ButtonGroup>
      </div>

      {/* Stats line */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-resolve-textMuted">
        <span>{props.selectedCount} selected</span>
        {props.cutCount > 0 && <span className="text-resolve-error">{props.cutCount} cut</span>}
        {props.muteCount > 0 && <span className="text-resolve-warning">{props.muteCount} muted</span>}
        {props.autoDetectFillers && props.fillerCount > 0 && <span className="text-orange-400">{props.fillerCount} filler{props.fillerCount !== 1 ? 's' : ''}</span>}
        {props.autoDetectFillers && props.repeatedCount > 0 && <span className="text-orange-400">{props.repeatedCount} repeat{props.repeatedCount !== 1 ? 's' : ''}</span>}
        <span className={
          !props.heldClickAction
            ? 'invisible font-medium'
            : props.heldClickAction === 'cut'
              ? 'text-resolve-error font-medium'
              : props.heldClickAction === 'mute'
                ? 'text-resolve-warning font-medium'
                : 'text-resolve-success font-medium'
        }>
          Hold mode: {props.heldClickAction === 'cut' || !props.heldClickAction ? 'Cut' : props.heldClickAction === 'mute' ? 'Mute' : 'Keep'}
        </span>
        {props.primarySpeaker && (
          <span className="text-cyan-300">
            Primary: {props.primarySpeaker.speaker}
            {typeof props.primarySpeaker.avg_dbfs === 'number' ? ` (${props.primarySpeaker.avg_dbfs.toFixed(1)} dB)` : ''}
          </span>
        )}
      </div>
    </div>
  )
}

import React from 'react'
import { Button } from '../../common/Button'
import { Slider } from '../../common/Slider'

export interface SilenceSettingsPanelProps {
  silenceThreshold: number
  silenceMinDuration: number
  silenceMinFrames: number
  silencePadding: number
  onThresholdChange: (v: number) => void
  onMinDurationChange: (v: number) => void
  onMinFramesChange: (v: number) => void
  onPaddingChange: (v: number) => void
  effectiveMinSilenceMs: number
  framerate: string
  findingSilences: boolean
  onFindSilences: () => void
  transcriptMutationBlocked: boolean
}

export function SilenceSettingsPanel(props: SilenceSettingsPanelProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-4">
        <Slider
          label="Threshold"
          value={props.silenceThreshold}
          onChange={props.onThresholdChange}
          min={-60}
          max={-20}
          unit=" dB"
        />
        <Slider
          label="Min Duration"
          value={props.silenceMinDuration}
          onChange={props.onMinDurationChange}
          min={100}
          max={3000}
          step={50}
          unit=" ms"
        />
        <Slider
          label="Min Frames"
          value={props.silenceMinFrames}
          onChange={props.onMinFramesChange}
          min={14}
          max={48}
          step={1}
          unit=" f"
        />
        <Slider
          label="Padding"
          value={props.silencePadding}
          onChange={props.onPaddingChange}
          min={0}
          max={500}
          step={25}
          unit=" ms"
        />
      </div>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <span className="text-xs text-resolve-textMuted">
          {props.silenceThreshold}dB
          {' \u00b7 '}Min floor: {props.effectiveMinSilenceMs}ms ({props.silenceMinFrames}f)
          {' \u00b7 '}Pad: {props.silencePadding}ms ({Math.round((props.silencePadding / 1000) * (parseFloat(props.framerate) || 24))}f)
        </span>
        <Button
          size="sm"
          onClick={props.onFindSilences}
          loading={props.findingSilences}
          disabled={props.transcriptMutationBlocked}
        >
          Find Silences
        </Button>
      </div>
    </div>
  )
}

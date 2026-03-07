import React from 'react'
import type { TranscriptWord } from './types'
import {
  isSilenceMarker,
  isAudioFillerMarker,
  isAnalysisMarker,
  humanizeSpeakerLabel,
  speakerBadgeStyle,
  formatTimestamp,
  formatPreciseTimestamp,
} from './types'

export interface TranscriptWordsProps {
  words: TranscriptWord[]
  activeWordIndex: number | null
  activeWordRef: React.RefObject<HTMLSpanElement>
  showTimestamps: boolean
  framerate: string
  autoDetectFillers: boolean
  isWordFiller: (word: TranscriptWord) => boolean
  isWordRepeated: (index: number) => boolean
  onWordMouseDown: (index: number, event: React.MouseEvent<HTMLSpanElement>) => void
  onWordMouseEnter: (index: number) => void
  onWordMouseUp: () => void
  onDoubleClick: (index: number) => void
}

export function TranscriptWords(props: TranscriptWordsProps) {
  const elements: React.ReactNode[] = []
  let lastTimestampSec = -Infinity
  let lastVisibleSpeaker: string | null = null

  for (let index = 0; index < props.words.length; index++) {
    const word = props.words[index]
    const isActive = index === props.activeWordIndex

    if (props.showTimestamps && word.start - lastTimestampSec >= 10) {
      lastTimestampSec = word.start
      elements.push(
        <span
          key={`ts-${index}`}
          className="inline-block mx-1 px-1.5 py-0.5 rounded bg-resolve-accent/15 text-resolve-accent text-[10px] font-mono align-middle select-none cursor-default"
          title={`Timestamp: ${formatTimestamp(word.start)}`}
        >
          {formatTimestamp(word.start)}
        </span>
      )
    }

    const isFiller = props.isWordFiller(word)
    const isRepeated = props.isWordRepeated(index)
    const isFillerOrRepeat = isFiller || isRepeated

    const actionClass =
      word.action === 'cut'
        ? 'bg-resolve-error/20 text-resolve-error line-through opacity-70 hover:opacity-90'
        : word.action === 'mute'
          ? 'bg-resolve-warning/20 text-resolve-warning hover:bg-resolve-warning/30'
          : isFillerOrRepeat
            ? 'bg-orange-500/20 text-orange-300 hover:bg-orange-500/30'
            : 'text-resolve-text hover:bg-resolve-accent/10'

    const showSpeakerChip =
      !isAnalysisMarker(word) &&
      Boolean(word.speaker) &&
      word.speaker !== lastVisibleSpeaker
    if (showSpeakerChip) {
      lastVisibleSpeaker = word.speaker || null
      elements.push(
        <span
          key={`speaker-${index}`}
          className={`mx-1 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide align-middle ${speakerBadgeStyle(word.speaker_index)}`}
          title={`Speaker turn: ${humanizeSpeakerLabel(word.speaker, word.speaker_index)}`}
        >
          {humanizeSpeakerLabel(word.speaker, word.speaker_index)}
        </span>
      )
    }

    const silenceMarker = isSilenceMarker(word)
    const fillerMarker = isAudioFillerMarker(word)
    const markerDuration = word.end - word.start
    const fps = parseFloat(props.framerate) || 24
    const markerFrames = Math.round(markerDuration * fps)
    const markerStart = formatPreciseTimestamp(word.start)
    const markerEnd = formatPreciseTimestamp(word.end)
    const audioFillerText =
      fillerMarker && word.word && word.word !== '[filler]'
        ? word.word
        : 'filler'
    const fillerTimestampLabel =
      isFiller && !silenceMarker && !fillerMarker
        ? `${markerStart}-${markerEnd}`
        : null
    const displayLabel = silenceMarker
      ? `[silence ${markerStart}-${markerEnd} / ${markerFrames}f]`
      : fillerMarker
        ? audioFillerText
        : word.word
    const displayContent: React.ReactNode =
      fillerMarker
        ? (
          <>
            <span>{displayLabel}</span>
            <span className="ml-1 rounded bg-orange-500/15 px-1 py-[1px] text-[10px] font-mono text-orange-300">
              {`${markerStart}-${markerEnd} / ${markerFrames}f`}
            </span>
          </>
        )
        : fillerTimestampLabel && !silenceMarker
        ? (
          <>
            <span>{word.word}</span>
            <span className="ml-1 rounded bg-orange-500/15 px-1 py-[1px] text-[10px] font-mono text-orange-300">
              {fillerTimestampLabel}
            </span>
          </>
        )
        : displayLabel
    const fillerTag = isFiller ? ' [filler]' : isRepeated ? ' [repeated]' : ''
    const speakerTag = word.speaker ? ` [${word.speaker}]` : ''
    const wordTitle = silenceMarker
      ? `Silence: ${markerDuration.toFixed(2)}s (${markerFrames} frames) | ${markerStart} - ${markerEnd}`
      : fillerMarker
        ? `Recovered filler "${audioFillerText}": ${markerDuration.toFixed(2)}s (${markerFrames} frames) | ${markerStart} - ${markerEnd}`
      : `${formatTimestamp(word.start)} - ${formatTimestamp(word.end)}${fillerTag}${speakerTag}`

    elements.push(
      <span
        key={index}
        ref={isActive ? props.activeWordRef : undefined}
        onMouseDown={(event) => props.onWordMouseDown(index, event)}
        onMouseEnter={() => props.onWordMouseEnter(index)}
        onMouseUp={props.onWordMouseUp}
        onDoubleClick={(event) => {
          event.preventDefault()
          props.onDoubleClick(index)
        }}
        className={`inline-block px-0.5 py-0.5 mx-0.5 rounded cursor-pointer text-xs sm:text-sm ${
          silenceMarker || fillerMarker ? 'font-mono text-[11px] ' : ''
        }${
          isActive
            ? 'bg-resolve-accent text-white font-semibold transition-all duration-75 ease-out'
            : `transition-all duration-100 ${actionClass}`
        } ${
          word.selected && !isActive
            ? 'ring-1 ring-resolve-accent ring-inset bg-resolve-accent/10'
            : ''
        }`}
        title={wordTitle}
      >
        {displayContent}
      </span>
    )
  }

  return <>{elements}</>
}

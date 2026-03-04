import React, { useRef } from 'react'
import { Button } from '../../common/Button'

export interface ScriptCutPanelProps {
  scriptText: string
  onScriptTextChange: (text: string) => void
  onApplyScript: () => void
  transcriptIsStale: boolean
  transcriptMutationBlocked: boolean
}

export function ScriptCutPanel(props: ScriptCutPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleImportFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = (e) => {
      const text = (e.target?.result as string) || ''
      props.onScriptTextChange(text)
    }
    reader.readAsText(file)
    event.target.value = ''
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="text-sm font-medium text-resolve-text">Script Cut</div>
          <div className="text-xs text-resolve-textMuted">
            Paste the script you want to keep. Everything else in the transcript gets cut.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => fileInputRef.current?.click()}
            title="Import script from a text file"
          >
            Import File
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.text"
            className="hidden"
            onChange={handleImportFile}
          />
          <Button
            size="sm"
            variant="success"
            onClick={props.onApplyScript}
            disabled={props.transcriptMutationBlocked}
          >
            Apply Script
          </Button>
        </div>
      </div>
      <textarea
        value={props.scriptText}
        onChange={(event) => props.onScriptTextChange(event.target.value)}
        placeholder="Paste the target script here..."
        disabled={props.transcriptIsStale}
        className="min-h-32 w-full rounded-lg border border-resolve-border bg-resolve-bg px-3 py-2 text-sm text-resolve-text placeholder:text-resolve-textMuted disabled:cursor-not-allowed disabled:opacity-60"
      />
    </div>
  )
}

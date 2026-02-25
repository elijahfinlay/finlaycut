import React, { useState } from 'react'
import { useProjectStore } from '../../stores/projectStore'
import { Button } from './Button'
import { Modal } from './Modal'

/**
 * Shows which timeline is active at the top of every feature panel.
 * Wraps the "Apply" action with a confirmation modal so you always
 * know exactly which timeline you're about to edit.
 */

interface TimelineGuardProps {
  children: React.ReactNode
}

export function TimelineBanner() {
  const { connected, projectName, timelineName, framerate, resolution } = useProjectStore()

  if (!connected) {
    return (
      <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-resolve-error/10 border border-resolve-error/20">
        <div className="w-2.5 h-2.5 rounded-full bg-resolve-error" />
        <span className="text-sm text-resolve-error font-medium">
          Not connected to DaVinci Resolve
        </span>
        <span className="text-xs text-resolve-textMuted">
          — Open Resolve and make sure external scripting is set to Network
        </span>
      </div>
    )
  }

  if (!timelineName) {
    return (
      <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-resolve-warning/10 border border-resolve-warning/20">
        <div className="w-2.5 h-2.5 rounded-full bg-resolve-warning" />
        <span className="text-sm text-resolve-warning font-medium">
          No timeline open
        </span>
        <span className="text-xs text-resolve-textMuted">
          — Open a timeline in Resolve to start editing
        </span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-resolve-accent/5 border border-resolve-accent/20">
      <div className="w-2.5 h-2.5 rounded-full bg-resolve-accent" />
      <div className="flex items-center gap-2">
        <span className="text-sm text-resolve-text font-medium">{timelineName}</span>
        <span className="text-xs text-resolve-textMuted">in {projectName}</span>
      </div>
      {resolution && (
        <span className="text-xs text-resolve-textMuted font-mono ml-auto">
          {resolution} @ {framerate}fps
        </span>
      )}
    </div>
  )
}

interface ConfirmApplyProps {
  onConfirm: () => void
  loading?: boolean
  disabled?: boolean
  children: React.ReactNode
  description?: string
}

export function ConfirmApply({ onConfirm, loading, disabled, children, description }: ConfirmApplyProps) {
  const [showConfirm, setShowConfirm] = useState(false)
  const { timelineName, projectName } = useProjectStore()

  const handleClick = () => {
    setShowConfirm(true)
  }

  const handleConfirm = () => {
    setShowConfirm(false)
    onConfirm()
  }

  return (
    <>
      <Button onClick={handleClick} variant="success" loading={loading} disabled={disabled} size="lg">
        {children}
      </Button>

      <Modal open={showConfirm} onClose={() => setShowConfirm(false)} title="Confirm Edit">
        <div className="space-y-4">
          <p className="text-sm text-resolve-textDim">
            This will edit the following timeline:
          </p>

          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-resolve-accent/10 border border-resolve-accent/20">
            <div className="w-2.5 h-2.5 rounded-full bg-resolve-accent" />
            <div>
              <p className="text-sm text-resolve-text font-semibold">{timelineName}</p>
              <p className="text-xs text-resolve-textMuted">{projectName}</p>
            </div>
          </div>

          {description && (
            <p className="text-sm text-resolve-textDim">{description}</p>
          )}

          <p className="text-xs text-resolve-textMuted">
            A backup of the current timeline will be created automatically before any changes are made.
          </p>

          <div className="flex gap-3 pt-2">
            <Button onClick={handleConfirm} variant="success" size="lg" className="flex-1">
              Yes, apply to this timeline
            </Button>
            <Button onClick={() => setShowConfirm(false)} variant="ghost" size="lg">
              Cancel
            </Button>
          </div>
        </div>
      </Modal>
    </>
  )
}

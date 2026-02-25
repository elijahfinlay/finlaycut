import React, { useEffect } from 'react'
import { clsx } from 'clsx'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  className?: string
}

export function Modal({ open, onClose, title, children, className }: ModalProps) {
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    if (open) {
      document.addEventListener('keydown', handleEscape)
      return () => document.removeEventListener('keydown', handleEscape)
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className={clsx(
          'relative bg-resolve-surface border border-resolve-border rounded-2xl shadow-2xl',
          'max-w-lg w-full mx-4 max-h-[80vh] overflow-hidden',
          className
        )}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-resolve-border">
          <h3 className="text-lg font-semibold text-resolve-text">{title}</h3>
          <button
            onClick={onClose}
            className="text-resolve-textMuted hover:text-resolve-text transition-colors p-1"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="px-6 py-4 overflow-y-auto max-h-[60vh]">{children}</div>
      </div>
    </div>
  )
}

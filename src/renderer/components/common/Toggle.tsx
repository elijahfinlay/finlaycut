import React from 'react'
import { clsx } from 'clsx'

interface ToggleProps {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  description?: string
}

export function Toggle({ label, checked, onChange, description }: ToggleProps) {
  return (
    <label className="flex items-center justify-between cursor-pointer group">
      <div>
        <span className="text-sm text-resolve-text group-hover:text-white transition-colors">
          {label}
        </span>
        {description && (
          <p className="text-xs text-resolve-textMuted mt-0.5">{description}</p>
        )}
      </div>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200 flex-shrink-0 ml-3',
          checked ? 'bg-resolve-accent' : 'bg-resolve-border'
        )}
      >
        <span
          className={clsx(
            'inline-block h-4 w-4 rounded-full bg-white transition-transform duration-200',
            checked ? 'translate-x-6' : 'translate-x-1'
          )}
        />
      </button>
    </label>
  )
}

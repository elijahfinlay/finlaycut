import React from 'react'
import { clsx } from 'clsx'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'success' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={clsx(
        'font-medium rounded-lg transition-all duration-200 inline-flex items-center justify-center gap-2 whitespace-nowrap shrink-0 max-w-full',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        {
          'px-3 py-1.5 text-sm': size === 'sm',
          'px-5 py-2.5 text-sm': size === 'md',
          'px-6 py-3 text-base': size === 'lg',
          'bg-resolve-accent hover:bg-resolve-accentHover text-white': variant === 'primary',
          'bg-resolve-success/20 hover:bg-resolve-success/30 text-resolve-success border border-resolve-success/30':
            variant === 'success',
          'bg-resolve-error/20 hover:bg-resolve-error/30 text-resolve-error border border-resolve-error/30':
            variant === 'danger',
          'bg-transparent hover:bg-resolve-surfaceLight text-resolve-textDim': variant === 'ghost'
        },
        className
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
}

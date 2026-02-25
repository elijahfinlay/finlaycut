import React from 'react'
import { clsx } from 'clsx'

interface CardProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
}

export function Card({ children, className, onClick }: CardProps) {
  return (
    <div
      className={clsx(
        'bg-resolve-surface rounded-xl border border-resolve-border p-5',
        onClick && 'cursor-pointer hover:border-resolve-accent/50 transition-colors',
        className
      )}
      onClick={onClick}
    >
      {children}
    </div>
  )
}

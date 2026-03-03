import React from 'react'
import { clsx } from 'clsx'

interface SkeletonProps {
  className?: string
  width?: string | number
  height?: string | number
  rounded?: boolean
}

export function Skeleton({ className, width, height, rounded }: SkeletonProps) {
  return (
    <div
      className={clsx(
        'animate-pulse bg-resolve-surfaceLight',
        rounded ? 'rounded-full' : 'rounded-lg',
        className
      )}
      style={{ width, height }}
    />
  )
}

export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={14}
          className={i === lines - 1 ? 'w-2/3' : 'w-full'}
        />
      ))}
    </div>
  )
}

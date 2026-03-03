import { create } from 'zustand'

export type OperationStatus = 'idle' | 'running' | 'complete' | 'error'

export interface OperationState {
  status: OperationStatus
  progress: number
  message: string
  error: string | null
  startTime: number | null
  elapsed: number
  eta: number | null
  result: any | null
  stalledCount: number
  lastProgress: number
}

const DEFAULT_OPERATION: OperationState = {
  status: 'idle',
  progress: 0,
  message: '',
  error: null,
  startTime: null,
  elapsed: 0,
  eta: null,
  result: null,
  stalledCount: 0,
  lastProgress: 0,
}

interface OperationStoreState {
  operations: Record<string, OperationState>
  startOperation: (name: string) => void
  updateProgress: (name: string, progress: number, message: string) => void
  completeOperation: (name: string, result?: any) => void
  failOperation: (name: string, error: string) => void
  resetOperation: (name: string) => void
  clearResult: (name: string) => void
  clearAll: () => void
}

function getOp(state: OperationStoreState, name: string): OperationState {
  return state.operations[name] || DEFAULT_OPERATION
}

export const useOperationStore = create<OperationStoreState>()((set, get) => ({
  operations: {},

  startOperation: (name) =>
    set((state) => ({
      operations: {
        ...state.operations,
        [name]: {
          ...DEFAULT_OPERATION,
          status: 'running',
          startTime: Date.now(),
        },
      },
    })),

  updateProgress: (name, progress, message) =>
    set((state) => {
      const prev = getOp(state, name)
      const now = Date.now()

      // Stall detection
      let stalledCount = prev.stalledCount
      if (Math.abs(progress - prev.lastProgress) < 0.001) {
        stalledCount++
      } else {
        stalledCount = 0
      }
      const isStalled = stalledCount >= 3

      // ETA calculation
      let elapsed = prev.elapsed
      let eta = prev.eta
      let startTime = prev.startTime

      if (startTime === null || progress < 0.01) {
        startTime = now
        elapsed = 0
        eta = null
        stalledCount = 0
      } else {
        elapsed = now - startTime
        if (isStalled) {
          eta = null
        } else if (progress > 0.05) {
          eta = (elapsed / progress) * (1 - progress)
        }
      }

      return {
        operations: {
          ...state.operations,
          [name]: {
            ...prev,
            status: 'running',
            progress,
            message,
            error: null,
            startTime,
            elapsed,
            eta,
            stalledCount,
            lastProgress: progress,
          },
        },
      }
    }),

  completeOperation: (name, result) =>
    set((state) => {
      const prev = getOp(state, name)
      return {
        operations: {
          ...state.operations,
          [name]: {
            ...prev,
            status: 'complete',
            progress: 1,
            eta: null,
            result: result ?? prev.result,
          },
        },
      }
    }),

  failOperation: (name, error) =>
    set((state) => {
      const prev = getOp(state, name)
      return {
        operations: {
          ...state.operations,
          [name]: {
            ...prev,
            status: 'error',
            error,
            eta: null,
          },
        },
      }
    }),

  resetOperation: (name) =>
    set((state) => ({
      operations: {
        ...state.operations,
        [name]: { ...DEFAULT_OPERATION },
      },
    })),

  clearResult: (name) =>
    set((state) => {
      const prev = getOp(state, name)
      return {
        operations: {
          ...state.operations,
          [name]: { ...prev, result: null },
        },
      }
    }),

  clearAll: () => set({ operations: {} }),
}))

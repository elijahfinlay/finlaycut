import React, { useState, useEffect } from 'react'
import { Button } from '../common/Button'
import { Card } from '../common/Card'
import { apiFetch } from '../../hooks/useApi'

interface HistoryEntry {
  id: string
  operation: string
  timestamp: number
  timeline_name: string
  description: string
}

export function UndoHistory() {
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadHistory()
  }, [])

  const loadHistory = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/api/undo/history')
      setHistory(data.history || [])
    } catch {
      // Ignore
    } finally {
      setLoading(false)
    }
  }

  const handleRollback = async (entryId: string) => {
    try {
      const data = await apiFetch(`/api/undo/rollback/${entryId}`, { method: 'POST' })
      if (data.success) {
        loadHistory()
      }
    } catch {
      // Ignore
    }
  }

  const handleClearHistory = async () => {
    await apiFetch('/api/undo/history', { method: 'DELETE' })
    setHistory([])
  }

  const formatTimestamp = (ts: number) => {
    const date = new Date(ts * 1000)
    const now = new Date()
    const diff = (now.getTime() - date.getTime()) / 1000

    if (diff < 60) return 'Just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return date.toLocaleDateString()
  }

  return (
    <div className="feature-panel">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="feature-title">Undo History</h2>
          <p className="feature-description">
            Every operation creates a backup. Roll back to any previous state.
          </p>
        </div>
        {history.length > 0 && (
          <Button variant="ghost" size="sm" onClick={handleClearHistory}>
            Clear
          </Button>
        )}
      </div>

      {loading && (
        <div className="text-sm text-resolve-textMuted">Loading history...</div>
      )}

      {!loading && history.length === 0 && (
        <Card>
          <p className="text-sm text-resolve-textDim text-center py-4">
            No operations recorded yet. Each time you apply an edit, it will appear here
            with a one-click rollback option.
          </p>
        </Card>
      )}

      {history.length > 0 && (
        <div className="space-y-2">
          {history.map((entry) => (
            <Card key={entry.id} className="!p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-resolve-text capitalize">
                      {entry.operation.replace(/_/g, ' ')}
                    </span>
                    <span className="text-xs text-resolve-textMuted">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                  </div>
                  <p className="text-xs text-resolve-textDim mt-0.5">
                    {entry.timeline_name}
                    {entry.description && ` — ${entry.description}`}
                  </p>
                </div>
                <Button variant="ghost" size="sm" onClick={() => handleRollback(entry.id)}>
                  Rollback
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

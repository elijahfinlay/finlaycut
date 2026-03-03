import { useEffect } from 'react'
import { useProjectStore } from '../stores/projectStore'

const FEATURE_KEYS: Record<string, string> = {
  '1': 'transcript',
  '2': 'silence',
  '3': 'filler',
  '4': 'captions',
  '5': 'zoom',
  '6': 'chapters',
  '7': 'podcast',
  '8': 'takes',
  '9': 'viral',
}

export function useKeyboardShortcuts() {
  const setActiveFeature = useProjectStore((s) => s.setActiveFeature)
  const toggleSidebar = useProjectStore((s) => s.toggleSidebar)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey
      const target = e.target as HTMLElement
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable

      // Cmd+1-9: switch features (skip if typing in input)
      if (meta && FEATURE_KEYS[e.key] && !isInput) {
        e.preventDefault()
        setActiveFeature(FEATURE_KEYS[e.key])
        return
      }

      // Cmd+B: toggle sidebar
      if (meta && e.key === 'b' && !isInput) {
        e.preventDefault()
        toggleSidebar()
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setActiveFeature, toggleSidebar])
}

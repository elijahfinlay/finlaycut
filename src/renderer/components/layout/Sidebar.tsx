import React from 'react'
import { clsx } from 'clsx'
import { useProjectStore } from '../../stores/projectStore'

interface NavItem {
  id: string
  label: string
  icon: string
  section: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'transcript', label: 'Transcript Editor', icon: 'text-cursor', section: 'Features' },
  { id: 'silence', label: 'Silence Removal', icon: 'volume-x', section: 'Features' },
  { id: 'filler', label: 'Filler Words', icon: 'message-circle-x', section: 'Features' },
  { id: 'captions', label: 'Captions', icon: 'subtitles', section: 'Features' },
  { id: 'zoom', label: 'Zoom Cuts', icon: 'zoom-in', section: 'Features' },
  { id: 'chapters', label: 'Chapters', icon: 'bookmark', section: 'Features' },
  { id: 'podcast', label: 'Podcast Editor', icon: 'mic', section: 'Features' },
  { id: 'takes', label: 'Takes & Duplicates', icon: 'repeat', section: 'Features' },
  { id: 'viral', label: 'Viral Clips', icon: 'trending-up', section: 'Features' },
  { id: 'broll', label: 'B-Roll', icon: 'film', section: 'Features' },
  { id: 'scenes', label: 'Scene Detect', icon: 'scissors', section: 'Features' },
  { id: 'clip-search', label: 'Clip Search', icon: 'search', section: 'Features' },
  { id: 'audio', label: 'Audio Enhance', icon: 'headphones', section: 'Extras' },
  { id: 'thumbnail', label: 'Thumbnails', icon: 'image', section: 'Extras' },
  { id: 'export', label: 'Export', icon: 'download', section: 'Extras' },
  { id: 'settings', label: 'Settings', icon: 'settings', section: '_bottom' },
  { id: 'history', label: 'History', icon: 'history', section: '_bottom' },
]

function NavIcon({ name }: { name: string }) {
  // Simple SVG icon mapping
  const icons: Record<string, React.ReactNode> = {
    'text-cursor': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 7V4h16v3M9 20h6M12 4v16" />
      </svg>
    ),
    'volume-x': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11 5L6 9H2v6h4l5 4V5zM23 9l-6 6M17 9l6 6" />
      </svg>
    ),
    'message-circle-x': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M10 10l4 4M14 10l-4 4" />
      </svg>
    ),
    'subtitles': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <rect x="2" y="4" width="20" height="16" rx="2" />
        <path strokeLinecap="round" d="M6 12h4M14 12h4M8 16h8" />
      </svg>
    ),
    'zoom-in': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="11" cy="11" r="8" />
        <path strokeLinecap="round" d="M21 21l-4.35-4.35M11 8v6M8 11h6" />
      </svg>
    ),
    'bookmark': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2v16z" />
      </svg>
    ),
    'mic': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" />
      </svg>
    ),
    'repeat': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <polyline points="17 1 21 5 17 9" />
        <path d="M3 11V9a4 4 0 014-4h14" />
        <polyline points="7 23 3 19 7 15" />
        <path d="M21 13v2a4 4 0 01-4 4H3" />
      </svg>
    ),
    'trending-up': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
        <polyline points="17 6 23 6 23 12" />
      </svg>
    ),
    'film': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
        <path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 17h5M17 7h5" />
      </svg>
    ),
    'headphones': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path d="M3 18v-6a9 9 0 0118 0v6" />
        <path d="M21 19a2 2 0 01-2 2h-1a2 2 0 01-2-2v-3a2 2 0 012-2h3zM3 19a2 2 0 002 2h1a2 2 0 002-2v-3a2 2 0 00-2-2H3z" />
      </svg>
    ),
    'image': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
      </svg>
    ),
    'scissors': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="6" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path strokeLinecap="round" d="M20 4L8.12 15.88M14.47 14.48L20 20M8.12 8.12L12 12" />
      </svg>
    ),
    'download': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
      </svg>
    ),
    'search': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="11" cy="11" r="7" />
        <path strokeLinecap="round" d="M20 20l-3.5-3.5" />
      </svg>
    ),
    'settings': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
      </svg>
    ),
    'history': (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path d="M3 3v5h5" />
        <path d="M3.05 13A9 9 0 106 5.3L3 8" />
        <path d="M12 7v5l4 2" />
      </svg>
    ),
  }

  return <>{icons[name] || <span className="w-4 h-4" />}</>
}

export function Sidebar() {
  const { activeFeature, setActiveFeature, sidebarCollapsed, toggleSidebar } = useProjectStore()

  const sections = {
    Features: NAV_ITEMS.filter((i) => i.section === 'Features'),
    Extras: NAV_ITEMS.filter((i) => i.section === 'Extras'),
    _bottom: NAV_ITEMS.filter((i) => i.section === '_bottom')
  }

  return (
    <div
      className={clsx(
        'bg-resolve-surface border-r border-resolve-border flex flex-col h-full transition-all duration-200',
        sidebarCollapsed ? 'w-14' : 'w-56'
      )}
    >
      {/* Logo + collapse toggle */}
      <div className={clsx('flex items-center border-b border-resolve-border', sidebarCollapsed ? 'px-2 py-4 justify-center' : 'px-5 py-4')}>
        {!sidebarCollapsed && (
          <h1 className="text-lg font-bold bg-gradient-to-r from-resolve-accent to-sky-400 bg-clip-text text-transparent flex-1">
            FinlayCut
          </h1>
        )}
        <button
          onClick={toggleSidebar}
          className="p-1 rounded text-resolve-textMuted hover:text-resolve-text hover:bg-resolve-surfaceLight transition-colors"
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            {sidebarCollapsed ? (
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7M19 19l-7-7 7-7" />
            )}
          </svg>
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-2">
        {(['Features', 'Extras'] as const).map((section) => (
          <div key={section} className={clsx(sidebarCollapsed ? 'px-1.5' : 'px-3', 'mb-2')}>
            {!sidebarCollapsed && (
              <div className="px-2 py-2 text-xs font-semibold text-resolve-textMuted uppercase tracking-wider">
                {section}
              </div>
            )}
            {sections[section].map((item) => (
              <button
                key={item.id}
                onClick={() => setActiveFeature(item.id)}
                title={sidebarCollapsed ? item.label : undefined}
                className={clsx(
                  'w-full flex items-center rounded-lg text-sm transition-all duration-150',
                  sidebarCollapsed ? 'justify-center p-2.5' : 'gap-3 px-3 py-2',
                  activeFeature === item.id
                    ? 'bg-resolve-accent/15 text-resolve-accent font-medium'
                    : 'text-resolve-textDim hover:text-resolve-text hover:bg-resolve-surfaceLight'
                )}
              >
                <NavIcon name={item.icon} />
                {!sidebarCollapsed && item.label}
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* Bottom items */}
      <div className={clsx('py-3 border-t border-resolve-border', sidebarCollapsed ? 'px-1.5' : 'px-3')}>
        {sections._bottom.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveFeature(item.id)}
            title={sidebarCollapsed ? item.label : undefined}
            className={clsx(
              'w-full flex items-center rounded-lg text-sm transition-all duration-150',
              sidebarCollapsed ? 'justify-center p-2.5' : 'gap-3 px-3 py-2',
              activeFeature === item.id
                ? 'bg-resolve-accent/15 text-resolve-accent font-medium'
                : 'text-resolve-textDim hover:text-resolve-text hover:bg-resolve-surfaceLight'
            )}
          >
            <NavIcon name={item.icon} />
            {!sidebarCollapsed && item.label}
          </button>
        ))}
      </div>
    </div>
  )
}

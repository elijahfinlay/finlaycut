import { app, shell, BrowserWindow, Menu } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { PythonManager } from './python-manager'
import { registerIpcHandlers } from './ipc-handlers'

// Catch EPIPE errors that occur when stdout/stderr pipes break
// (e.g., dev server dies, child process pipe closes). Without this,
// the Electron app crashes with "Error: write EPIPE".
process.on('uncaughtException', (err) => {
  if ((err as NodeJS.ErrnoException).code === 'EPIPE') {
    // Swallow EPIPE — it's harmless, just means a pipe reader closed
    return
  }
  // Log other uncaught exceptions but don't crash
  try {
    console.error('[FinlayCut] Uncaught exception:', err)
  } catch {
    // Even logging might fail if it's a pipe error
  }
})

process.stdout?.on('error', (err) => {
  if ((err as NodeJS.ErrnoException).code === 'EPIPE') return
})

process.stderr?.on('error', (err) => {
  if ((err as NodeJS.ErrnoException).code === 'EPIPE') return
})

let mainWindow: BrowserWindow | null = null
let pythonManager: PythonManager | null = null
let appQuitting = false

function blockReloadShortcuts(window: BrowserWindow): void {
  window.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return

    const isReloadShortcut =
      (input.code === 'KeyR' && (input.meta || input.control)) ||
      input.code === 'F5'

    if (isReloadShortcut) {
      event.preventDefault()
    }
  })
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    show: false,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 15, y: 15 },
    backgroundColor: '#141418',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.on('render-process-gone', (_, details) => {
    try {
      console.error('[FinlayCut] Renderer process exited:', details.reason)
    } catch {
      // Ignore logging failures.
    }

    if (appQuitting) {
      return
    }

    const crashedWindow = mainWindow
    mainWindow = null

    if (crashedWindow && !crashedWindow.isDestroyed()) {
      crashedWindow.destroy()
    }

    setTimeout(() => {
      if (!appQuitting && BrowserWindow.getAllWindows().length === 0) {
        createWindow()
      }
    }, 250)
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  blockReloadShortcuts(mainWindow)

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.finlaycut.app')

  // FIX BUG #1: Register app menu so Cmd+C/V/X/Z/A work (paste was broken without this)
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      { role: 'appMenu' },
      { role: 'editMenu' },
      {
        label: 'View',
        submenu: [
          { role: 'resetZoom' },
          { role: 'zoomIn' },
          { role: 'zoomOut' },
          ...(is.dev ? [{ type: 'separator' as const }, { role: 'toggleDevTools' as const }] : []),
          { type: 'separator' },
          { role: 'togglefullscreen' },
        ],
      },
      { role: 'windowMenu' },
    ])
  )

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  pythonManager = new PythonManager()
  registerIpcHandlers(pythonManager)
  createWindow()

  void pythonManager.start().catch((error) => {
    try {
      console.error('[FinlayCut] Backend startup failed:', error)
    } catch {
      // Ignore logging failures.
    }
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// FIX BUG #11: Only stop Python once
let pythonStopped = false

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', async () => {
  appQuitting = true
  if (pythonManager && !pythonStopped) {
    pythonStopped = true
    await pythonManager.stop()
  }
})

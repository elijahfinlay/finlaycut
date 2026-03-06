/// <reference types="vite/client" />

interface BackendDiagnostics {
  mode: 'dev' | 'packaged'
  phase:
    | 'idle'
    | 'preparing-runtime'
    | 'creating-venv'
    | 'installing-dependencies'
    | 'starting-backend'
    | 'ready'
    | 'error'
  isRunning: boolean
  sourceBackendPath: string
  backendPath: string
  runtimePath: string | null
  pythonPath: string | null
  ffmpegPath: string | null
  ffprobePath: string | null
  resolveScriptApi: string | null
  resolveScriptLib: string | null
  summary: string
  lastError: string | null
  logs: string[]
}

interface ElectronAPI {
  getBackendUrl: () => Promise<string>
  getBackendStatus: () => Promise<boolean>
  getBackendDiagnostics: () => Promise<BackendDiagnostics>
  retryBackendStart: () => Promise<BackendDiagnostics>
  openFileDialog: (options?: object) => Promise<Electron.OpenDialogReturnValue>
  openFolderDialog: () => Promise<Electron.OpenDialogReturnValue>
  openExternal: (url: string) => Promise<void>
  getAppVersion: () => Promise<string>
}

interface Window {
  api: ElectronAPI
}

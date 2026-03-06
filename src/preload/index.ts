import { contextBridge, ipcRenderer } from 'electron'

export interface BackendDiagnostics {
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

const api = {
  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('get-backend-url'),
  getBackendStatus: (): Promise<boolean> => ipcRenderer.invoke('get-backend-status'),
  getBackendDiagnostics: (): Promise<BackendDiagnostics> =>
    ipcRenderer.invoke('get-backend-diagnostics'),
  retryBackendStart: (): Promise<BackendDiagnostics> => ipcRenderer.invoke('retry-backend-start'),
  openFileDialog: (options?: object): Promise<Electron.OpenDialogReturnValue> =>
    ipcRenderer.invoke('open-file-dialog', options),
  openFolderDialog: (): Promise<Electron.OpenDialogReturnValue> =>
    ipcRenderer.invoke('open-folder-dialog'),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('open-external', url),
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('get-app-version')
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore
  window.api = api
}

export type ElectronAPI = typeof api

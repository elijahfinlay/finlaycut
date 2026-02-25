import { contextBridge, ipcRenderer } from 'electron'

const api = {
  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('get-backend-url'),
  getBackendStatus: (): Promise<boolean> => ipcRenderer.invoke('get-backend-status'),
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

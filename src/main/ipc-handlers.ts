import { ipcMain, dialog, shell } from 'electron'
import { PythonManager } from './python-manager'

export function registerIpcHandlers(pythonManager: PythonManager): void {
  ipcMain.handle('get-backend-url', () => {
    return `http://127.0.0.1:${pythonManager.port}`
  })

  ipcMain.handle('get-backend-status', () => {
    return pythonManager.isRunning
  })

  ipcMain.handle('open-file-dialog', async (_, options) => {
    const result = await dialog.showOpenDialog({
      properties: ['openFile'],
      ...options
    })
    return result
  })

  ipcMain.handle('open-folder-dialog', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory']
    })
    return result
  })

  ipcMain.handle('open-external', async (_, url: string) => {
    await shell.openExternal(url)
  })

  ipcMain.handle('get-app-version', () => {
    const { app } = require('electron')
    return app.getVersion()
  })
}

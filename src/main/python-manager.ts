import { ChildProcess, spawn } from 'child_process'
import { join } from 'path'
import { app } from 'electron'
import { is } from '@electron-toolkit/utils'
import * as net from 'net'

const BACKEND_PORT = 42069
const HEALTH_CHECK_INTERVAL = 3000
const MAX_START_RETRIES = 30

export class PythonManager {
  private process: ChildProcess | null = null
  private healthCheckTimer: NodeJS.Timeout | null = null
  private _isRunning = false

  get isRunning(): boolean {
    return this._isRunning
  }

  get port(): number {
    return BACKEND_PORT
  }

  private getBackendPath(): string {
    if (is.dev) {
      return join(app.getAppPath(), 'backend')
    }
    return join(process.resourcesPath, 'backend')
  }

  private getPythonPath(): string {
    // Try common Python paths on macOS
    const candidates = [
      'python3',
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      '/usr/bin/python3'
    ]
    return candidates[0] // spawn will search PATH for 'python3'
  }

  async start(): Promise<void> {
    if (this._isRunning) return

    const backendPath = this.getBackendPath()
    const pythonPath = this.getPythonPath()

    console.log(`[PythonManager] Starting backend from: ${backendPath}`)
    console.log(`[PythonManager] Using Python: ${pythonPath}`)

    this.process = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
      cwd: backendPath,
      env: {
        ...process.env,
        PYTHONPATH: backendPath,
        RESOLVE_SCRIPT_API: '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting',
        RESOLVE_SCRIPT_LIB: '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so'
      },
      stdio: ['pipe', 'pipe', 'pipe']
    })

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[Backend] ${data.toString().trim()}`)
    })

    this.process.stderr?.on('data', (data: Buffer) => {
      console.log(`[Backend:err] ${data.toString().trim()}`)
    })

    this.process.on('exit', (code) => {
      console.log(`[PythonManager] Backend exited with code ${code}`)
      this._isRunning = false
    })

    this.process.on('error', (err) => {
      console.error(`[PythonManager] Failed to start backend:`, err)
      this._isRunning = false
    })

    // Wait for the server to be ready
    await this.waitForReady()
    this._isRunning = true
    console.log(`[PythonManager] Backend is ready on port ${BACKEND_PORT}`)
  }

  private async waitForReady(): Promise<void> {
    for (let i = 0; i < MAX_START_RETRIES; i++) {
      try {
        const isReady = await this.checkPort()
        if (isReady) return
      } catch {
        // Port not ready yet
      }
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
    console.warn('[PythonManager] Backend did not start within expected time, continuing anyway')
  }

  private checkPort(): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = new net.Socket()
      socket.setTimeout(1000)
      socket.on('connect', () => {
        socket.destroy()
        resolve(true)
      })
      socket.on('timeout', () => {
        socket.destroy()
        resolve(false)
      })
      socket.on('error', () => {
        socket.destroy()
        resolve(false)
      })
      socket.connect(BACKEND_PORT, '127.0.0.1')
    })
  }

  async stop(): Promise<void> {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer)
      this.healthCheckTimer = null
    }

    if (this.process) {
      console.log('[PythonManager] Stopping backend...')
      this.process.kill('SIGTERM')

      // Give it 5 seconds to shut down gracefully
      await new Promise<void>((resolve) => {
        const timeout = setTimeout(() => {
          if (this.process) {
            this.process.kill('SIGKILL')
          }
          resolve()
        }, 5000)

        this.process?.on('exit', () => {
          clearTimeout(timeout)
          resolve()
        })
      })

      this.process = null
      this._isRunning = false
      console.log('[PythonManager] Backend stopped')
    }
  }
}

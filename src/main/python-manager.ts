import { ChildProcess, spawn } from 'child_process'
import { join } from 'path'
import { app } from 'electron'
import { is } from '@electron-toolkit/utils'
import * as net from 'net'

const BACKEND_PORT = 42069
const MAX_RESTART_ATTEMPTS = 3
const RESTART_DELAY_MS = 2000

export class PythonManager {
  private process: ChildProcess | null = null
  private _isRunning = false
  private _isStopping = false
  private _restartCount = 0
  private _restartTimer: NodeJS.Timeout | null = null

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
    const backendPath = this.getBackendPath()
    const fs = require('fs')

    // Prefer the project's virtual environment python
    const venvCandidates = [
      join(backendPath, '.venv', 'bin', 'python3'),
      join(backendPath, '.venv', 'bin', 'python'),
      join(backendPath, 'venv', 'bin', 'python3'),
      join(backendPath, 'venv', 'bin', 'python'),
    ]

    for (const p of venvCandidates) {
      try {
        if (fs.existsSync(p)) {
          this.safeLog(`[PythonManager] Found venv Python: ${p}`)
          return p
        }
      } catch {
        // keep searching
      }
    }

    // Fall back to system Python
    this.safeLog('[PythonManager] No venv found, falling back to system python3')
    return 'python3'
  }

  /**
   * Safe console.log that swallows EPIPE errors.
   * When Electron's stdout pipe breaks (e.g. dev server dies), console.log
   * throws EPIPE which would crash the whole app if unhandled.
   */
  private safeLog(...args: any[]): void {
    try {
      console.log(...args)
    } catch {
      // EPIPE or other write error — swallow it
    }
  }

  private safeError(...args: any[]): void {
    try {
      console.error(...args)
    } catch {
      // EPIPE — swallow
    }
  }

  async start(): Promise<void> {
    if (this._isRunning) return

    const backendPath = this.getBackendPath()
    const pythonPath = this.getPythonPath()
    const debugCuts = process.env.FINLAYCUT_DEBUG_CUT_PLANNING ?? (is.dev ? '1' : '0')
    const debugLogLimit = process.env.FINLAYCUT_DEBUG_LOG_LIMIT ?? '20'

    this.safeLog(`[PythonManager] Starting backend from: ${backendPath}`)
    this.safeLog(`[PythonManager] Using Python: ${pythonPath}`)
    this.safeLog(`[PythonManager] Cut debug logging: ${debugCuts === '1' ? 'enabled' : 'disabled'}`)

    // Ensure /opt/homebrew/bin is on PATH — Electron strips shell profile paths
    const currentPath = process.env.PATH || ''
    const extraPaths = ['/opt/homebrew/bin', '/usr/local/bin']
    const patchedPath = [...extraPaths.filter((p) => !currentPath.includes(p)), currentPath]
      .filter(Boolean)
      .join(':')

    this.process = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
      cwd: backendPath,
      env: {
        ...process.env,
        PATH: patchedPath,
        PYTHONPATH: backendPath,
        RESOLVE_SCRIPT_API: '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting',
        RESOLVE_SCRIPT_LIB: '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so',
        FINLAYCUT_DEBUG_CUT_PLANNING: debugCuts,
        FINLAYCUT_DEBUG_LOG_LIMIT: debugLogLimit
      },
      stdio: ['pipe', 'pipe', 'pipe']
    })

    // Pipe child stdout/stderr with EPIPE-safe logging
    this.process.stdout?.on('data', (data: Buffer) => {
      this.safeLog(`[Backend] ${data.toString().trim()}`)
    })
    this.process.stdout?.on('error', () => {
      // Pipe error (process died) — ignore
    })

    this.process.stderr?.on('data', (data: Buffer) => {
      this.safeLog(`[Backend:err] ${data.toString().trim()}`)
    })
    this.process.stderr?.on('error', () => {
      // Pipe error (process died) — ignore
    })

    // Also handle stdin pipe errors (if anything ever writes to it)
    this.process.stdin?.on('error', () => {
      // Pipe error — ignore
    })

    this.process.on('exit', (code) => {
      this.safeLog(`[PythonManager] Backend exited with code ${code}`)
      this._isRunning = false

      // Auto-restart if we didn't intentionally stop it
      if (!this._isStopping && this._restartCount < MAX_RESTART_ATTEMPTS) {
        this._restartCount++
        this.safeLog(
          `[PythonManager] Unexpected exit — auto-restarting (attempt ${this._restartCount}/${MAX_RESTART_ATTEMPTS})...`
        )
        this._restartTimer = setTimeout(() => {
          this.start().catch((err) => {
            this.safeError('[PythonManager] Auto-restart failed:', err)
          })
        }, RESTART_DELAY_MS)
      } else if (!this._isStopping) {
        this.safeError('[PythonManager] Backend crashed too many times, giving up')
      }
    })

    this.process.on('error', (err) => {
      this.safeError(`[PythonManager] Failed to start backend:`, err)
      this._isRunning = false
    })

    // Wait for the server to be ready
    await this.waitForReady()
    this._isRunning = true
    this._restartCount = 0 // Reset on successful start
    this.safeLog(`[PythonManager] Backend is ready on port ${BACKEND_PORT}`)
  }

  private async waitForReady(): Promise<void> {
    const MAX_RETRIES = 15
    let delay = 200 // start fast, then back off
    for (let i = 0; i < MAX_RETRIES; i++) {
      try {
        const isReady = await this.checkHealth()
        if (isReady) return
      } catch {
        // Not ready yet
      }
      await new Promise((resolve) => setTimeout(resolve, delay))
      delay = Math.min(delay * 1.5, 1000) // exponential backoff, cap at 1s
    }
    this.safeLog('[PythonManager] Backend did not start within expected time, continuing anyway')
  }

  private async checkHealth(): Promise<boolean> {
    try {
      const http = require('http')
      return new Promise((resolve) => {
        const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, { timeout: 1000 }, (res: any) => {
          resolve(res.statusCode === 200)
          res.resume()
        })
        req.on('error', () => resolve(false))
        req.on('timeout', () => {
          req.destroy()
          resolve(false)
        })
      })
    } catch {
      return false
    }
  }

  async stop(): Promise<void> {
    this._isStopping = true

    if (this._restartTimer) {
      clearTimeout(this._restartTimer)
      this._restartTimer = null
    }

    if (this.process) {
      this.safeLog('[PythonManager] Stopping backend...')
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
      this.safeLog('[PythonManager] Backend stopped')
    }
  }
}

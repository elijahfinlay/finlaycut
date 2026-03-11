import { ChildProcess, spawn } from 'child_process'
import { createHash } from 'crypto'
import { existsSync, readdirSync } from 'fs'
import { cp, mkdir, readFile, writeFile } from 'fs/promises'
import { join, basename, dirname } from 'path'
import { app } from 'electron'
import { is } from '@electron-toolkit/utils'

const BACKEND_PORT = 42069
const MAX_RESTART_ATTEMPTS = 3
const RESTART_DELAY_MS = 2000
const MAX_LOG_LINES = 120

type BackendPhase =
  | 'idle'
  | 'preparing-runtime'
  | 'creating-venv'
  | 'installing-dependencies'
  | 'starting-backend'
  | 'ready'
  | 'error'

interface PythonCommand {
  command: string
  args: string[]
  label: string
}

interface RuntimeMetadata {
  appVersion?: string
  requirementsHash: string
}

interface BundledPythonRuntime {
  version: string
  homePath: string
  pythonPath: string
  sitePackagesPath: string | null
}

interface PreparedRuntime {
  backendPath: string
  pythonPath: string
  pythonHome?: string
  sitePackagesPath?: string | null
}

export interface BackendDiagnostics {
  mode: 'dev' | 'packaged'
  phase: BackendPhase
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

export class PythonManager {
  private process: ChildProcess | null = null
  private _isRunning = false
  private _isStarting = false
  private _isStopping = false
  private _restartCount = 0
  private _restartTimer: NodeJS.Timeout | null = null
  private _logs: string[] = []
  private _diagnostics: BackendDiagnostics

  constructor() {
    const sourceBackendPath = this.getSourceBackendPath()
    const backendPath = this.getWorkingBackendPath()
    this._diagnostics = {
      mode: is.dev ? 'dev' : 'packaged',
      phase: 'idle',
      isRunning: false,
      sourceBackendPath,
      backendPath,
      runtimePath: is.dev ? null : this.getRuntimeRootPath(),
      pythonPath: null,
      ffmpegPath: null,
      ffprobePath: null,
      resolveScriptApi: null,
      resolveScriptLib: null,
      summary: is.dev ? 'Development backend idle.' : 'Packaged backend idle.',
      lastError: null,
      logs: [],
    }
  }

  get isRunning(): boolean {
    return this._isRunning
  }

  get port(): number {
    return BACKEND_PORT
  }

  get diagnostics(): BackendDiagnostics {
    return {
      ...this._diagnostics,
      logs: [...this._logs],
      isRunning: this._isRunning,
    }
  }

  private getSourceBackendPath(): string {
    if (is.dev) {
      return join(app.getAppPath(), 'backend')
    }
    return join(process.resourcesPath, 'backend')
  }

  private getRuntimeRootPath(): string {
    return join(app.getPath('userData'), 'backend-runtime')
  }

  private getWorkingBackendPath(): string {
    if (is.dev) {
      return this.getSourceBackendPath()
    }
    return join(this.getRuntimeRootPath(), 'backend')
  }

  private getBundledPythonRuntimeRootPath(): string {
    return join(process.resourcesPath, 'python-runtime')
  }

  private getBundledPythonRuntime(): BundledPythonRuntime | null {
    const runtimeRoot = this.getBundledPythonRuntimeRootPath()
    if (!existsSync(runtimeRoot)) {
      return null
    }

    const versions = readdirSync(runtimeRoot)
      .filter((entry) => /^\d+\.\d+$/.test(entry))
      .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))

    for (const version of versions) {
      const homePath = join(runtimeRoot, version)
      const pythonPath = process.platform === 'win32'
        ? join(homePath, 'python.exe')
        : join(homePath, 'bin', `python${version}`)
      if (!existsSync(pythonPath)) {
        continue
      }

      const sitePackagesPath = this.getBackendSitePackagesPath(this.getSourceBackendPath(), version)
      return {
        version,
        homePath,
        pythonPath,
        sitePackagesPath,
      }
    }

    return null
  }

  private getBackendSitePackagesPath(backendPath: string, versionHint?: string): string | null {
    const libRoot = join(backendPath, '.venv', 'lib')
    if (!existsSync(libRoot)) {
      return null
    }

    const candidateVersions = versionHint
      ? [`python${versionHint}`]
      : readdirSync(libRoot)
          .filter((entry) => entry.startsWith('python'))
          .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))

    for (const version of candidateVersions) {
      const sitePackagesPath = join(libRoot, version, 'site-packages')
      if (existsSync(sitePackagesPath)) {
        return sitePackagesPath
      }
    }

    return null
  }

  private getRuntimeMetadataPath(): string {
    return join(this.getRuntimeRootPath(), 'runtime.json')
  }

  private getDevRuntimeMetadataPath(backendPath: string): string {
    return join(backendPath, '.venv', '.finlaycut-runtime.json')
  }

  private appendLog(message: string): void {
    const line = `[${new Date().toISOString()}] ${message}`
    this._logs = [...this._logs.slice(-(MAX_LOG_LINES - 1)), line]
    this._diagnostics.logs = [...this._logs]
  }

  private setDiagnostics(
    update: Partial<Omit<BackendDiagnostics, 'logs' | 'isRunning'>>,
  ): void {
    this._diagnostics = {
      ...this._diagnostics,
      ...update,
      isRunning: this._isRunning,
      logs: [...this._logs],
    }
  }

  private setPhase(
    phase: BackendPhase,
    summary: string,
    extra?: Partial<Omit<BackendDiagnostics, 'logs' | 'isRunning' | 'phase' | 'summary'>>,
  ): void {
    this.setDiagnostics({
      phase,
      summary,
      ...(extra || {}),
    })
  }

  /**
   * Safe console.log that also feeds diagnostics without crashing on EPIPE.
   */
  private safeLog(...args: any[]): void {
    const line = args.map((arg) => String(arg)).join(' ')
    this.appendLog(line)
    try {
      console.log(...args)
    } catch {
      // Swallow EPIPE or other write errors.
    }
  }

  private safeError(...args: any[]): void {
    const line = args.map((arg) => String(arg)).join(' ')
    this.appendLog(line)
    try {
      console.error(...args)
    } catch {
      // Swallow EPIPE or other write errors.
    }
  }

  private getVenvPythonCandidates(basePath: string): string[] {
    const unixCandidates = [
      join(basePath, '.venv', 'bin', 'python3'),
      join(basePath, '.venv', 'bin', 'python'),
      join(basePath, 'venv', 'bin', 'python3'),
      join(basePath, 'venv', 'bin', 'python'),
    ]
    const windowsCandidates = [
      join(basePath, '.venv', 'Scripts', 'python.exe'),
      join(basePath, 'venv', 'Scripts', 'python.exe'),
    ]
    return process.platform === 'win32'
      ? [...windowsCandidates, ...unixCandidates]
      : [...unixCandidates, ...windowsCandidates]
  }

  private findExistingVenvPython(basePath: string): string | null {
    for (const candidate of this.getVenvPythonCandidates(basePath)) {
      if (existsSync(candidate)) {
        return candidate
      }
    }
    return null
  }

  private getSystemPythonCandidates(): PythonCommand[] {
    if (process.platform === 'win32') {
      return [
        { command: 'py', args: ['-3'], label: 'py -3' },
        { command: 'python', args: [], label: 'python' },
      ]
    }
    return [
      { command: 'python3', args: [], label: 'python3' },
      { command: 'python', args: [], label: 'python' },
      { command: '/usr/bin/python3', args: [], label: '/usr/bin/python3' },
    ]
  }

  private async canRunCommand(command: string, args: string[]): Promise<boolean> {
    return new Promise((resolve) => {
      const child = spawn(command, [...args, '--version'], {
        stdio: 'ignore',
      })
      child.once('error', () => resolve(false))
      child.once('exit', (code) => resolve(code === 0))
    })
  }

  private async findSystemPython(): Promise<PythonCommand | null> {
    for (const candidate of this.getSystemPythonCandidates()) {
      if (await this.canRunCommand(candidate.command, candidate.args)) {
        return candidate
      }
    }
    return null
  }

  private async computeRequirementsHash(requirementsPath: string): Promise<string> {
    const content = await readFile(requirementsPath, 'utf8')
    return createHash('sha256')
      .update(content)
      .digest('hex')
  }

  private async readRuntimeMetadataAt(metadataPath: string): Promise<RuntimeMetadata | null> {
    try {
      const raw = await readFile(metadataPath, 'utf8')
      const parsed = JSON.parse(raw) as RuntimeMetadata
      if (!parsed.requirementsHash) {
        return null
      }
      return parsed
    } catch {
      return null
    }
  }

  private async readRuntimeMetadata(): Promise<RuntimeMetadata | null> {
    return this.readRuntimeMetadataAt(this.getRuntimeMetadataPath())
  }

  private async writeRuntimeMetadataAt(metadataPath: string, metadata: RuntimeMetadata): Promise<void> {
    await mkdir(dirname(metadataPath), { recursive: true })
    await writeFile(metadataPath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8')
  }

  private async writeRuntimeMetadata(metadata: RuntimeMetadata): Promise<void> {
    await this.writeRuntimeMetadataAt(this.getRuntimeMetadataPath(), metadata)
  }

  private async syncPackagedBackend(): Promise<string> {
    const sourceBackendPath = this.getSourceBackendPath()
    const backendPath = this.getWorkingBackendPath()
    await mkdir(this.getRuntimeRootPath(), { recursive: true })
    await cp(sourceBackendPath, backendPath, {
      recursive: true,
      force: true,
      filter: (source) => {
        const name = basename(source)
        return ![
          '.venv',
          'venv',
          '__pycache__',
          '.pytest_cache',
          '.mypy_cache',
          '.ruff_cache',
        ].includes(name)
      },
    })
    return backendPath
  }

  private async runCommand(
    label: string,
    command: string,
    args: string[],
    cwd: string,
    env?: NodeJS.ProcessEnv,
  ): Promise<void> {
    this.safeLog(`[PythonManager] ${label}: ${command} ${args.join(' ')}`)
    await new Promise<void>((resolve, reject) => {
      const child = spawn(command, args, {
        cwd,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
      })

      child.stdout?.on('data', (data: Buffer) => {
        this.safeLog(`[Bootstrap] ${data.toString().trim()}`)
      })
      child.stderr?.on('data', (data: Buffer) => {
        this.safeLog(`[Bootstrap:err] ${data.toString().trim()}`)
      })

      child.once('error', (error) => reject(error))
      child.once('exit', (code) => {
        if (code === 0) {
          resolve()
          return
        }
        reject(new Error(`${label} failed with exit code ${code ?? 'unknown'}`))
      })
    })
  }

  private async terminateProcess(): Promise<void> {
    if (!this.process) {
      return
    }

    const child = this.process
    this.process = null

    if (child.exitCode !== null || child.killed) {
      return
    }

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        try {
          child.kill('SIGKILL')
        } catch {
          // Ignore cleanup failures during forced termination.
        }
        resolve()
      }, 5000)

      child.once('exit', () => {
        clearTimeout(timeout)
        resolve()
      })

      try {
        child.kill('SIGTERM')
      } catch {
        clearTimeout(timeout)
        resolve()
      }
    })
  }

  private getBundledBinaryPath(name: 'ffmpeg' | 'ffprobe'): string | null {
    const executableName = process.platform === 'win32' ? `${name}.exe` : name
    const candidates = [
      process.env[name === 'ffmpeg' ? 'FINLAYCUT_FFMPEG_PATH' : 'FINLAYCUT_FFPROBE_PATH'],
      join(process.resourcesPath, 'bin', executableName),
      join(this.getSourceBackendPath(), 'bin', executableName),
      join(app.getAppPath(), 'resources', 'bin', executableName),
    ].filter(Boolean) as string[]

    for (const candidate of candidates) {
      if (existsSync(candidate)) {
        return candidate
      }
    }
    return null
  }

  private getResolveScriptApiPath(): string | null {
    const candidates =
      process.platform === 'win32'
        ? [
            process.env.RESOLVE_SCRIPT_API,
            'C:\\ProgramData\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting',
          ]
        : process.platform === 'darwin'
          ? [
              process.env.RESOLVE_SCRIPT_API,
              '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting',
            ]
          : [
              process.env.RESOLVE_SCRIPT_API,
              '/opt/resolve/Developer/Scripting',
              '/home/resolve/Developer/Scripting',
            ]

    for (const candidate of candidates) {
      if (candidate && existsSync(candidate)) {
        return candidate
      }
    }
    return (candidates.find(Boolean) as string | undefined) ?? null
  }

  private getResolveScriptLibPath(): string | null {
    const candidates =
      process.platform === 'win32'
        ? [
            process.env.RESOLVE_SCRIPT_LIB,
            'C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\fusionscript.dll',
          ]
        : process.platform === 'darwin'
          ? [
              process.env.RESOLVE_SCRIPT_LIB,
              '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so',
            ]
          : [
              process.env.RESOLVE_SCRIPT_LIB,
              '/opt/resolve/libs/Fusion/fusionscript.so',
              '/opt/resolve/libs/Fusion/libfusionscript.so',
            ]

    for (const candidate of candidates) {
      if (candidate && existsSync(candidate)) {
        return candidate
      }
    }
    return (candidates.find(Boolean) as string | undefined) ?? null
  }

  private async prepareRuntime(): Promise<PreparedRuntime> {
    if (is.dev) {
      const backendPath = this.getWorkingBackendPath()
      const pythonPath = this.findExistingVenvPython(backendPath)
      if (!pythonPath) {
        return { backendPath, pythonPath: 'python3' }
      }

      const requirementsPath = join(backendPath, 'requirements.txt')
      const requirementsHash = await this.computeRequirementsHash(requirementsPath)
      const metadataPath = this.getDevRuntimeMetadataPath(backendPath)
      const runtimeMetadata = await this.readRuntimeMetadataAt(metadataPath)
      const needsInstall = runtimeMetadata?.requirementsHash !== requirementsHash

      if (needsInstall) {
        this.setPhase('installing-dependencies', 'Syncing backend dependencies for development…')
        await this.runCommand(
          'Upgrade pip',
          pythonPath,
          ['-m', 'pip', 'install', '--upgrade', 'pip'],
          backendPath,
          process.env,
        )
        await this.runCommand(
          'Install backend dependencies',
          pythonPath,
          ['-m', 'pip', 'install', '-r', 'requirements.txt'],
          backendPath,
          process.env,
        )
        await this.writeRuntimeMetadataAt(metadataPath, {
          requirementsHash,
        })
      }

      return { backendPath, pythonPath }
    }

    const bundledBackendPath = this.getSourceBackendPath()
    const bundledRuntime = this.getBundledPythonRuntime()
    if (bundledRuntime?.sitePackagesPath && await this.canRunCommand(bundledRuntime.pythonPath, [])) {
      this.setPhase('preparing-runtime', 'Using bundled backend runtime…')
      return {
        backendPath: bundledBackendPath,
        pythonPath: bundledRuntime.pythonPath,
        pythonHome: bundledRuntime.homePath,
        sitePackagesPath: bundledRuntime.sitePackagesPath,
      }
    }

    this.setPhase('preparing-runtime', 'Preparing backend runtime for this Mac…')
    const backendPath = await this.syncPackagedBackend()
    const requirementsPath = join(backendPath, 'requirements.txt')
    const requirementsHash = await this.computeRequirementsHash(requirementsPath)
    const runtimeMetadata = await this.readRuntimeMetadata()

    let pythonPath = this.findExistingVenvPython(backendPath)
    const needsInstall =
      !pythonPath ||
      runtimeMetadata?.appVersion !== app.getVersion() ||
      runtimeMetadata?.requirementsHash !== requirementsHash

    if (!needsInstall && pythonPath) {
      return { backendPath, pythonPath }
    }

    const systemPython = await this.findSystemPython()
    if (!systemPython) {
      throw new Error(
        'FinlayCut needs Python 3 available on this Mac to prepare its backend runtime.',
      )
    }

    const venvPath = join(backendPath, '.venv')
    const pipEnv = {
      ...process.env,
      PIP_DISABLE_PIP_VERSION_CHECK: '1',
      PIP_CACHE_DIR: join(this.getRuntimeRootPath(), 'pip-cache'),
    }

    if (!pythonPath) {
      this.setPhase('creating-venv', 'Creating an isolated Python environment…')
      await this.runCommand(
        'Create virtual environment',
        systemPython.command,
        [...systemPython.args, '-m', 'venv', venvPath],
        backendPath,
        pipEnv,
      )
      pythonPath = this.findExistingVenvPython(backendPath)
    }

    if (!pythonPath) {
      throw new Error('FinlayCut could not locate the virtual environment Python after creating it.')
    }

    this.setPhase('installing-dependencies', 'Installing backend dependencies. First launch may take a few minutes…')
    await this.runCommand(
      'Upgrade pip',
      pythonPath,
      ['-m', 'pip', 'install', '--upgrade', 'pip'],
      backendPath,
      pipEnv,
    )
    await this.runCommand(
      'Install backend dependencies',
      pythonPath,
      ['-m', 'pip', 'install', '-r', 'requirements.txt'],
      backendPath,
      pipEnv,
    )
    await this.writeRuntimeMetadata({
      appVersion: app.getVersion(),
      requirementsHash,
    })

    return { backendPath, pythonPath }
  }

  async start(): Promise<void> {
    if (this._isRunning || this._isStarting) return

    this._isStarting = true
    this._isStopping = false
    this.setDiagnostics({
      lastError: null,
      sourceBackendPath: this.getSourceBackendPath(),
      backendPath: this.getWorkingBackendPath(),
      runtimePath: is.dev ? null : this.getRuntimeRootPath(),
    })

    try {
      const { backendPath, pythonPath, pythonHome, sitePackagesPath } = await this.prepareRuntime()
      const ffmpegPath = this.getBundledBinaryPath('ffmpeg')
      const ffprobePath = this.getBundledBinaryPath('ffprobe')
      const resolveScriptApi = this.getResolveScriptApiPath()
      const resolveScriptLib = this.getResolveScriptLibPath()
      const debugCuts = process.env.FINLAYCUT_DEBUG_CUT_PLANNING ?? (is.dev ? '1' : '0')
      const debugLogLimit = process.env.FINLAYCUT_DEBUG_LOG_LIMIT ?? '20'

      this.setPhase('starting-backend', 'Starting backend services…', {
        backendPath,
        runtimePath: pythonHome ?? (is.dev ? null : this.getRuntimeRootPath()),
        pythonPath,
        ffmpegPath,
        ffprobePath,
        resolveScriptApi,
        resolveScriptLib,
      })

      this.safeLog(`[PythonManager] Starting backend from: ${backendPath}`)
      this.safeLog(`[PythonManager] Using Python: ${pythonPath}`)
      if (pythonHome) this.safeLog(`[PythonManager] Using PYTHONHOME: ${pythonHome}`)
      if (ffmpegPath) this.safeLog(`[PythonManager] Using bundled ffmpeg: ${ffmpegPath}`)
      if (ffprobePath) this.safeLog(`[PythonManager] Using bundled ffprobe: ${ffprobePath}`)

      const currentPath = process.env.PATH || ''
      const extraPaths = ['/opt/homebrew/bin', '/usr/local/bin']
      const patchedPath = [...extraPaths.filter((p) => !currentPath.includes(p)), currentPath]
        .filter(Boolean)
        .join(':')

      const pythonPathEntries = [backendPath]
      if (sitePackagesPath) {
        pythonPathEntries.push(sitePackagesPath)
      }

      const env: NodeJS.ProcessEnv = {
        ...process.env,
        PATH: patchedPath,
        PYTHONPATH: pythonPathEntries.join(process.platform === 'win32' ? ';' : ':'),
        FINLAYCUT_DEBUG_CUT_PLANNING: debugCuts,
        FINLAYCUT_DEBUG_LOG_LIMIT: debugLogLimit,
        PYTHONNOUSERSITE: '1',
      }
      if (pythonHome) env.PYTHONHOME = pythonHome
      if (resolveScriptApi) env.RESOLVE_SCRIPT_API = resolveScriptApi
      if (resolveScriptLib) env.RESOLVE_SCRIPT_LIB = resolveScriptLib
      if (ffmpegPath) env.FINLAYCUT_FFMPEG_PATH = ffmpegPath
      if (ffprobePath) env.FINLAYCUT_FFPROBE_PATH = ffprobePath

      this.process = spawn(
        pythonPath,
        ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
        {
          cwd: backendPath,
          env,
          stdio: ['pipe', 'pipe', 'pipe'],
        },
      )

      this.process.stdout?.on('data', (data: Buffer) => {
        this.safeLog(`[Backend] ${data.toString().trim()}`)
      })
      this.process.stdout?.on('error', () => {
        // Ignore broken pipe errors from child stdout.
      })

      this.process.stderr?.on('data', (data: Buffer) => {
        this.safeLog(`[Backend:err] ${data.toString().trim()}`)
      })
      this.process.stderr?.on('error', () => {
        // Ignore broken pipe errors from child stderr.
      })

      this.process.stdin?.on('error', () => {
        // Ignore broken pipe errors from child stdin.
      })

      this.process.on('exit', (code) => {
        this.safeLog(`[PythonManager] Backend exited with code ${code}`)
        this.process = null
        this._isRunning = false
        this.setDiagnostics({ phase: this._isStopping ? 'idle' : 'error' })

        if (!this._isStopping && this._restartCount < MAX_RESTART_ATTEMPTS) {
          this._restartCount += 1
          this.setPhase(
            'starting-backend',
            `Backend exited unexpectedly. Retrying (${this._restartCount}/${MAX_RESTART_ATTEMPTS})…`,
          )
          this._restartTimer = setTimeout(() => {
            this.start().catch((error) => {
              this.safeError('[PythonManager] Auto-restart failed:', error)
            })
          }, RESTART_DELAY_MS)
        } else if (!this._isStopping) {
          const message = 'Backend crashed too many times. Check setup diagnostics and retry.'
          this.setPhase('error', message, { lastError: message })
          this.safeError('[PythonManager] Backend crashed too many times, giving up')
        }
      })

      this.process.on('error', (error) => {
        this.process = null
        this._isRunning = false
        this.setPhase('error', 'Failed to launch backend process.', {
          lastError: String(error),
        })
        this.safeError('[PythonManager] Failed to start backend:', error)
      })

      const ready = await this.waitForReady()
      if (!ready) {
        throw new Error('Backend did not respond to health checks.')
      }

      this._isRunning = true
      this._restartCount = 0
      this.setPhase('ready', 'Backend ready.', {
        backendPath,
        runtimePath: pythonHome ?? (is.dev ? null : this.getRuntimeRootPath()),
        pythonPath,
        ffmpegPath,
        ffprobePath,
        resolveScriptApi,
        resolveScriptLib,
      })
      this.safeLog(`[PythonManager] Backend is ready on port ${BACKEND_PORT}`)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const wasStopping = this._isStopping
      this._isStopping = true
      await this.terminateProcess()
      this._isStopping = wasStopping
      this._isRunning = false
      this.setPhase('error', message, {
        lastError: message,
      })
      this.safeError('[PythonManager] Startup failed:', error)
      throw error
    } finally {
      this._isStarting = false
    }
  }

  private async waitForReady(): Promise<boolean> {
    const maxRetries = 60
    let delay = 250
    for (let attempt = 0; attempt < maxRetries; attempt += 1) {
      if (await this.checkHealth()) {
        return true
      }
      await new Promise((resolve) => setTimeout(resolve, delay))
      delay = Math.min(delay * 1.4, 1000)
    }
    return false
  }

  private async checkHealth(): Promise<boolean> {
    try {
      const http = require('http')
      return await new Promise((resolve) => {
        const request = http.get(
          `http://127.0.0.1:${BACKEND_PORT}/health`,
          { timeout: 1000 },
          (response: any) => {
            resolve(response.statusCode === 200)
            response.resume()
          },
        )
        request.on('error', () => resolve(false))
        request.on('timeout', () => {
          request.destroy()
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
      this.safeLog('[PythonManager] Stopping backend…')
      await this.terminateProcess()
    }

    this._isRunning = false
    this._isStarting = false
    this.setPhase('idle', 'Backend stopped.')
    this.safeLog('[PythonManager] Backend stopped')
  }
}

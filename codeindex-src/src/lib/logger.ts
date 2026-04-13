/**
 * Unified Logger with Language Indicators and ANSI Colors
 *
 * Format: HH:MM:SS.mmm [LANG] [LEVEL] [Component] Message
 *
 * Language indicators:
 *   [JS] - JavaScript/TypeScript (white)
 *   [SW] - Swift (light blue)
 *   [RU] - Rust (yellow/green)
 *   [BN] - Bun (light green)
 *
 * Colors:
 *   - JS: White (default)
 *   - SW: Light blue
 *   - RU: Yellow/Green
 *   - BN: Light green
 *   - ERROR: Always red (overrides language color)
 *
 * Usage:
 *   const logger = createLogger('ServerManager', LogCategory.API, 'BN')
 *   logger.info('server ready')
 *   logger.error('connection failed')
 */

export type LogLevel = 'VERBOSE' | 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'
export type LogLanguage = 'JS' | 'SW' | 'RU' | 'BN'

// Preserve original console methods before any patching
const _origConsole = {
  log: console.log.bind(console),
  warn: console.warn.bind(console),
  error: console.error.bind(console),
  debug: console.debug.bind(console),
}

// File logging — only active in Bun/Node (not browser)
let logFileHandle: { write(s: string): void; close?(): void } | null = null
let logFilePath: string | null = null
let consolePatchActive = false
const ANSI_REGEX = /\x1b\[[0-9;]*m/g

function stripAnsi(s: string): string {
  return s.replace(ANSI_REGEX, '')
}

function writeToFile(message: string): void {
  if (!logFileHandle) return
  try {
    logFileHandle.write(stripAnsi(message) + '\n')
  } catch {
    // File write failed, don't crash the app
  }
}

export function initFileLogging(filePath: string): void {
  try {
    const fs = require('fs')
    const path = require('path')
    // Ensure directory exists
    const dir = path.dirname(filePath)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })

    // Open in append mode
    const fd = fs.openSync(filePath, 'a')
    logFileHandle = {
      write: (s: string) => fs.writeSync(fd, s),
      close: () => fs.closeSync(fd),
    }
    logFilePath = filePath

    // Write session header
    const now = new Date()
    const header = `\n${'='.repeat(60)}\n` +
      `Log session started: ${now.toISOString()}\n` +
      `${'='.repeat(60)}\n`
    logFileHandle.write(header)
  } catch {
    // File logging not available (e.g. browser context)
    logFileHandle = null
  }
}

export function getLogFilePath(): string | null {
  return logFilePath
}

export enum LogCategory {
  BOOT = 'BOOT',
  VITE = 'VITE',
  API = 'API',
  INDEX = 'INDEX',
  GRAPH = 'GRAPH',
  SEARCH = 'SEARCH',
  UI = 'UI',
  IPC = 'IPC',
  PERF = 'PERF',
}

const LOG_LEVEL_ORDER: Record<LogLevel, number> = {
  VERBOSE: 0,
  DEBUG: 1,
  INFO: 2,
  WARN: 3,
  ERROR: 4,
}

// ANSI Color codes
const ANSI = {
  reset: '\x1b[0m',
  // Language colors
  white: '\x1b[37m', // TS, JS
  lightBlue: '\x1b[94m', // SW (Swift)
  yellow: '\x1b[93m', // RU (Rust)
  lightGreen: '\x1b[92m', // BN (Bun)
  // Level colors
  red: '\x1b[31m', // ERROR
  orange: '\x1b[33m', // WARN
  cyan: '\x1b[36m', // DEBUG
  gray: '\x1b[90m', // VERBOSE
} as const

/**
 * Get color for language
 */
function getLanguageColor(lang: LogLanguage): string {
  switch (lang) {
    case 'JS':
      return ANSI.white
    case 'SW':
      return ANSI.lightBlue
    case 'RU':
      return ANSI.yellow
    case 'BN':
      return ANSI.lightGreen
    default:
      return ANSI.white
  }
}

/**
 * Get color for log level (ERROR overrides language color)
 */
function getLevelColor(level: LogLevel, langColor: string): string {
  switch (level) {
    case 'ERROR':
      return ANSI.red
    case 'WARN':
      return ANSI.orange
    case 'DEBUG':
      return ANSI.cyan
    case 'VERBOSE':
      return ANSI.gray
    default:
      return langColor
  }
}

/**
 * Format timestamp as HH:MM:SS.mmm
 */
function formatTimestamp(): string {
  const now = new Date()
  const hours = now.getHours().toString().padStart(2, '0')
  const minutes = now.getMinutes().toString().padStart(2, '0')
  const seconds = now.getSeconds().toString().padStart(2, '0')
  const millis = now.getMilliseconds().toString().padStart(3, '0')
  return `${hours}:${minutes}:${seconds}.${millis}`
}

export class Logger {
  private name: string
  private logLevel: LogLevel
  private category?: LogCategory
  private language: LogLanguage
  private enabledCategories: LogCategory[] | null = null

  constructor(
    name: string,
    category?: LogCategory,
    language: LogLanguage = 'JS',
  ) {
    this.name = name
    this.category = category
    this.language = language
    this.logLevel = this.parseLogLevel()
  }

  private parseLogLevel(): LogLevel {
    let envLevel: string | undefined

    // Try process.env first (Bun / Node / test)
    try {
      if (typeof process !== 'undefined' && process.env?.LOG_LEVEL) {
        envLevel = process.env.LOG_LEVEL.toUpperCase()
      }
    } catch {
      // process not available
    }

    // Try import.meta.env (Vite / browser)
    if (!envLevel) {
      try {
        const meta = (import.meta as any)
        if (meta?.env?.VITE_LOG_LEVEL) {
          envLevel = meta.env.VITE_LOG_LEVEL.toUpperCase()
        }
      } catch {
        // import.meta.env not available
      }
    }

    if (envLevel && Object.keys(LOG_LEVEL_ORDER).includes(envLevel)) {
      return envLevel as LogLevel
    }

    // Default to VERBOSE for dev mode (CodeIndex is a dev tool)
    return 'VERBOSE'
  }

  public getLogLevel(): LogLevel {
    return this.logLevel
  }

  public setLogLevel(level: LogLevel): void {
    this.logLevel = level
  }

  public setEnabledCategories(categories: LogCategory[]): void {
    this.enabledCategories = categories
  }

  private shouldLog(level: LogLevel): boolean {
    const currentLevel = LOG_LEVEL_ORDER[this.logLevel]
    const requestedLevel = LOG_LEVEL_ORDER[level]
    if (requestedLevel < currentLevel) {
      return false
    }

    if (!this.category || this.enabledCategories === null) {
      return true
    }

    return this.enabledCategories.includes(this.category)
  }

  /**
   * Format log message with unified format:
   * HH:MM:SS.mmm [LANG] [LEVEL] [Component] Message
   */
  private formatMessage(level: LogLevel, message: string): string {
    const timestamp = formatTimestamp()
    const langColor = getLanguageColor(this.language)
    const levelColor = getLevelColor(level, langColor)

    // Format: HH:MM:SS.mmm [LANG] [LEVEL] [Component] Message
    return `${levelColor}${timestamp} [${this.language}] [${level}] [${this.name}] ${message}${ANSI.reset}`
  }

  /**
   * Format args for output (handle objects, arrays, etc.)
   */
  private formatArgs(args: unknown[]): string {
    if (args.length === 0) return ''
    return (
      ' ' +
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' ')
    )
  }

  private output(level: LogLevel, message: string, args: unknown[]): void {
    const formatted = this.formatMessage(level, message) + this.formatArgs(args)
    writeToFile(formatted)
    // Always use original console to avoid recursion when console is patched
    switch (level) {
      case 'ERROR': _origConsole.error(formatted); break
      case 'WARN': _origConsole.warn(formatted); break
      case 'DEBUG': _origConsole.debug(formatted); break
      default: _origConsole.log(formatted); break
    }
  }

  public verbose(message: string, ...args: unknown[]): void {
    if (!this.shouldLog('VERBOSE')) return
    this.output('VERBOSE', message, args)
  }

  public debug(message: string, ...args: unknown[]): void {
    if (!this.shouldLog('DEBUG')) return
    this.output('DEBUG', message, args)
  }

  public info(message: string, ...args: unknown[]): void {
    if (!this.shouldLog('INFO')) return
    this.output('INFO', message, args)
  }

  public warn(message: string, ...args: unknown[]): void {
    if (!this.shouldLog('WARN')) return
    this.output('WARN', message, args)
  }

  public error(message: string, ...args: unknown[]): void {
    if (!this.shouldLog('ERROR')) return
    this.output('ERROR', message, args)
  }
}

// Global registry
const globalLoggerRegistry: Set<Logger> = new Set()

function registerLogger(logger: Logger): void {
  globalLoggerRegistry.add(logger)
}

export function updateAllLoggers(
  newLevel: LogLevel,
  newCategories?: LogCategory[],
): void {
  _origConsole.log(
    `${formatTimestamp()} [JS] [INFO] [Logger] Updating ${globalLoggerRegistry.size} logger instances to level: ${newLevel}`,
  )

  for (const logger of globalLoggerRegistry) {
    logger.setLogLevel(newLevel)
    if (newCategories) {
      logger.setEnabledCategories(newCategories)
    }
  }

  _origConsole.log(`${formatTimestamp()} [JS] [INFO] [Logger] All loggers updated to: ${newLevel}`)
}

// Pre-configured loggers
export const bootLogger = new Logger('Boot', LogCategory.BOOT, 'BN')
registerLogger(bootLogger)

export const ipcLogger = new Logger('IPC', LogCategory.IPC, 'BN')
registerLogger(ipcLogger)

export const indexLogger = new Logger('Indexer', LogCategory.INDEX, 'JS')
registerLogger(indexLogger)

export const uiLogger = new Logger('UI', LogCategory.UI, 'JS')
registerLogger(uiLogger)

export const perfLogger = new Logger('Performance', LogCategory.PERF, 'JS')
registerLogger(perfLogger)

/**
 * Create a logger instance for a specific module
 * @param moduleName Name of the module/component
 * @param category Optional log category for filtering
 * @param language Language indicator (default: 'JS')
 */
export function createLogger(
  moduleName: string,
  category?: LogCategory,
  language: LogLanguage = 'JS',
): Logger {
  const logger = new Logger(moduleName, category, language)
  registerLogger(logger)
  return logger
}

/**
 * Monkeypatch console.log/warn/error/debug to route through the unified logger.
 * Call once at boot — all existing console.* calls get timestamps, colors, and file logging.
 */
export function patchConsole(language: LogLanguage = 'JS'): void {
  if (consolePatchActive) return
  consolePatchActive = true

  const patchLogger = new Logger('Console', undefined, language)
  registerLogger(patchLogger)

  function formatArgs(args: unknown[]): string {
    return args
      .map(a => (typeof a === 'object' && a !== null ? JSON.stringify(a) : String(a)))
      .join(' ')
  }

  console.log = (...args: unknown[]) => patchLogger.info(formatArgs(args))
  console.warn = (...args: unknown[]) => patchLogger.warn(formatArgs(args))
  console.error = (...args: unknown[]) => patchLogger.error(formatArgs(args))
  console.debug = (...args: unknown[]) => patchLogger.debug(formatArgs(args))
}

/**
 * Restore original console methods (useful for testing)
 */
export function unpatchConsole(): void {
  if (!consolePatchActive) return
  consolePatchActive = false
  console.log = _origConsole.log
  console.warn = _origConsole.warn
  console.error = _origConsole.error
  console.debug = _origConsole.debug
}

/**
 * Default logger instance
 */
const defaultLogger = createLogger('App')

const log = {
  info: (...args: unknown[]) =>
    defaultLogger.info(
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' '),
    ),
  warn: (...args: unknown[]) =>
    defaultLogger.warn(
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' '),
    ),
  error: (...args: unknown[]) =>
    defaultLogger.error(
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' '),
    ),
  debug: (...args: unknown[]) =>
    defaultLogger.debug(
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' '),
    ),
  verbose: (...args: unknown[]) =>
    defaultLogger.verbose(
      args
        .map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
        .join(' '),
    ),
}

export default log

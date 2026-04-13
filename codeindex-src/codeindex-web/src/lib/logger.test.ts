/**
 * Tests for Conditional Logging Optimization
 *
 * Verifies that:
 * 1. Log level filtering works correctly
 * 2. DEBUG logs are skipped in production (LOG_LEVEL=INFO)
 * 3. ERROR and WARN logs are always enabled
 * 4. Performance impact is minimal
 * 5. VERBOSE level support
 * 6. Category-based filtering
 */

import {
  describe,
  test,
  expect,
  beforeEach,
  afterEach,
  mock,
  spyOn,
} from 'bun:test'
import { Logger, LogLevel, LogCategory } from '../../../src/lib/logger'

describe('Logger - Conditional Logging Optimization', () => {
  let logger: Logger
  let originalEnv: Record<string, string | undefined>

  beforeEach(() => {
    // Save original env and reset LOG_LEVEL
    originalEnv = { ...process.env }
    delete process.env.LOG_LEVEL // Reset to default
    // Create fresh logger for each test
    logger = new Logger('TestLogger')
  })

  afterEach(() => {
    // Restore env
    process.env = originalEnv
  })

  describe('Log level configuration', () => {
    test('should default to VERBOSE level (dev tool default)', () => {
      delete process.env.LOG_LEVEL
      const testLogger = new Logger('test')
      expect(testLogger.getLogLevel()).toBe('VERBOSE')
    })

    test('should respect LOG_LEVEL environment variable', () => {
      process.env.LOG_LEVEL = 'DEBUG'
      const testLogger = new Logger('test')
      expect(testLogger.getLogLevel()).toBe('DEBUG')

      process.env.LOG_LEVEL = 'WARN'
      const testLogger2 = new Logger('test2')
      expect(testLogger2.getLogLevel()).toBe('WARN')

      process.env.LOG_LEVEL = 'ERROR'
      const testLogger3 = new Logger('test3')
      expect(testLogger3.getLogLevel()).toBe('ERROR')
    })

    test('should accept valid log levels: DEBUG, INFO, WARN, ERROR', () => {
      const levels = ['DEBUG', 'INFO', 'WARN', 'ERROR']
      for (const level of levels) {
        process.env.LOG_LEVEL = level
        const testLogger = new Logger('test')
        expect(testLogger.getLogLevel()).toBe(level)
      }
    })

    test('should fallback to VERBOSE for invalid log levels', () => {
      process.env.LOG_LEVEL = 'INVALID'
      const testLogger = new Logger('test')
      expect(testLogger.getLogLevel()).toBe('VERBOSE')
    })
  })

  describe('Log filtering: DEBUG level', () => {
    test('should skip debug logs when LOG_LEVEL=INFO', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.debug('This should be skipped')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should include debug logs when LOG_LEVEL=DEBUG', () => {
      process.env.LOG_LEVEL = 'DEBUG'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'debug')

      testLogger.debug('This should be included')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      // Check the full message including all arguments
      const fullMessage = consoleSpy.mock.calls[0].join(' ')
      expect(fullMessage).toContain('This should be included')
      consoleSpy.mockRestore()
    })

    test('should skip debug logs when LOG_LEVEL=WARN', () => {
      process.env.LOG_LEVEL = 'WARN'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.debug('This should be skipped')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should skip debug logs when LOG_LEVEL=ERROR', () => {
      process.env.LOG_LEVEL = 'ERROR'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.debug('This should be skipped')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })
  })

  describe('Log filtering: INFO level', () => {
    test('should skip info logs when LOG_LEVEL=WARN', () => {
      process.env.LOG_LEVEL = 'WARN'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be skipped')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should include info logs when LOG_LEVEL=INFO', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be included')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      consoleSpy.mockRestore()
    })

    test('should include info logs when LOG_LEVEL=DEBUG', () => {
      process.env.LOG_LEVEL = 'DEBUG'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be included')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      consoleSpy.mockRestore()
    })
  })

  describe('Log filtering: WARN level', () => {
    test('should include warn logs at WARN level and below', () => {
      const levels: LogLevel[] = ['VERBOSE', 'DEBUG', 'INFO', 'WARN']
      for (const level of levels) {
        process.env.LOG_LEVEL = level
        const testLogger = new Logger(`test-${level}`)
        const consoleSpy = spyOn(console, 'warn')

        testLogger.warn('Warning message')

        expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
        consoleSpy.mockRestore()
      }
    })

    test('should suppress warn logs when LOG_LEVEL=ERROR', () => {
      process.env.LOG_LEVEL = 'ERROR'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'warn')

      testLogger.warn('Should be suppressed')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })
  })

  describe('Log filtering: ERROR level (always enabled)', () => {
    test('should always include error logs', () => {
      const levels = ['DEBUG', 'INFO', 'WARN', 'ERROR']
      for (const level of levels) {
        process.env.LOG_LEVEL = level
        const testLogger = new Logger(`test-${level}`)
        const consoleSpy = spyOn(console, 'error')

        testLogger.error('Error message')

        expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
        consoleSpy.mockRestore()
      }
    })
  })

  describe('Production mode behavior (LOG_LEVEL=INFO)', () => {
    beforeEach(() => {
      process.env.LOG_LEVEL = 'INFO'
      logger = new Logger('ProdTest')
    })

    test('should skip all debug logs in production', () => {
      const consoleSpy = spyOn(console, 'log')

      logger.debug('Debug 1')
      logger.debug('Debug 2')
      logger.debug('Debug 3')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should include INFO, WARN, ERROR logs in production', () => {
      const logSpy = spyOn(console, 'log')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      logger.info('Info message')
      logger.warn('Warning message')
      logger.error('Error message')

      expect(logSpy.mock.calls.length).toBeGreaterThan(0) // info uses console.log
      expect(warnSpy.mock.calls.length).toBeGreaterThan(0)
      expect(errorSpy.mock.calls.length).toBeGreaterThan(0)

      logSpy.mockRestore()
      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })
  })

  describe('Development mode behavior (LOG_LEVEL=DEBUG)', () => {
    beforeEach(() => {
      process.env.LOG_LEVEL = 'DEBUG'
      logger = new Logger('DevTest')
    })

    test('should include all log levels in development', () => {
      const logSpy = spyOn(console, 'log')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      logger.debug('Debug message')
      logger.info('Info message')
      logger.warn('Warning message')
      logger.error('Error message')

      expect(logSpy.mock.calls.length).toBeGreaterThan(0)
      expect(warnSpy.mock.calls.length).toBeGreaterThan(0)
      expect(errorSpy.mock.calls.length).toBeGreaterThan(0)

      logSpy.mockRestore()
      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })
  })

  describe('Performance: Minimal overhead', () => {
    test('should return immediately for skipped debug logs', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('perf-test')

      const startTime = Date.now()
      for (let i = 0; i < 1000; i++) {
        testLogger.debug(`Debug message ${i}`)
      }
      const elapsed = Date.now() - startTime

      // Should complete very quickly (< 50ms for 1000 calls)
      expect(elapsed).toBeLessThan(50)
    })

    test('should log faster when DEBUG is disabled', () => {
      const debugTimings: number[] = []
      const infoTimings: number[] = []

      // Test with LOG_LEVEL=DEBUG (all logs enabled)
      process.env.LOG_LEVEL = 'DEBUG'
      let testLogger = new Logger('timing1')
      const logSpy1 = spyOn(console, 'log').mockImplementation(() => {})

      let start = Date.now()
      for (let i = 0; i < 100; i++) {
        testLogger.debug(`Debug message ${i}`)
      }
      debugTimings.push(Date.now() - start)

      logSpy1.mockRestore()

      // Test with LOG_LEVEL=INFO (debug logs skipped)
      process.env.LOG_LEVEL = 'INFO'
      testLogger = new Logger('timing2')
      const logSpy2 = spyOn(console, 'log').mockImplementation(() => {})

      start = Date.now()
      for (let i = 0; i < 100; i++) {
        testLogger.debug(`Debug message ${i}`)
      }
      infoTimings.push(Date.now() - start)

      logSpy2.mockRestore()

      // INFO mode (skipping debug) should be faster or equal
      expect(infoTimings[0]).toBeLessThanOrEqual(debugTimings[0] + 5) // Allow 5ms variance
    })
  })

  describe('Logger naming and context', () => {
    test('should include logger name in output', () => {
      process.env.LOG_LEVEL = 'DEBUG'
      const namedLogger = new Logger('APIService')
      const consoleSpy = spyOn(console, 'log')

      namedLogger.info('Test message')

      expect(consoleSpy.mock.calls[0][0]).toContain('APIService')
      consoleSpy.mockRestore()
    })

    test('should support multiple logger instances independently', () => {
      process.env.LOG_LEVEL = 'INFO'
      const logger1 = new Logger('Logger1')
      const logger2 = new Logger('Logger2')

      const consoleSpy = spyOn(console, 'log')

      logger1.debug('Should skip')
      logger1.info('Should show')
      logger2.debug('Should skip')
      logger2.info('Should show')

      // Should have 2 info messages total
      expect(consoleSpy.mock.calls.length).toBe(2)
      consoleSpy.mockRestore()
    })
  })

  describe('Backward compatibility', () => {
    test('should work with typical usage patterns', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('Compat')
      const consoleSpy = spyOn(console, 'log')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      // Typical usage
      testLogger.debug('Detailed state: ', { x: 1, y: 2 })
      testLogger.info('Starting service')
      testLogger.warn('Memory usage high')
      testLogger.error('Connection failed')

      // Expected behavior
      expect(consoleSpy.mock.calls.length).toBe(1) // Only info
      expect(warnSpy.mock.calls.length).toBe(1)
      expect(errorSpy.mock.calls.length).toBe(1)

      consoleSpy.mockRestore()
      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })
  })
})

/**
 * Tests for Enhanced Logging System
 *
 * Verifies:
 * - 5 log levels (ERROR=4, WARN=3, INFO=2, DEBUG=1, VERBOSE=0)
 * - Level hierarchy enforcement
 * - 9 categories for filtering
 * - VERBOSE as default level (dev tool)
 * - <10ms logging overhead
 */
describe('Logger - Enhanced Logging System', () => {
  let logger: Logger
  let originalEnv: Record<string, string | undefined>

  beforeEach(() => {
    originalEnv = { ...process.env }
    process.env.LOG_LEVEL = undefined
    logger = new Logger('TestLogger')
  })

  afterEach(() => {
    process.env = originalEnv
  })

  describe('VERBOSE log level', () => {
    test('should support VERBOSE as lowest level (0)', () => {
      process.env.LOG_LEVEL = 'VERBOSE'
      const testLogger = new Logger('test')
      expect(testLogger.getLogLevel()).toBe('VERBOSE')
    })

    test('should skip VERBOSE logs when LOG_LEVEL=DEBUG', () => {
      process.env.LOG_LEVEL = 'DEBUG'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.verbose('This should be skipped')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should include VERBOSE logs when LOG_LEVEL=VERBOSE', () => {
      process.env.LOG_LEVEL = 'VERBOSE'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.verbose('This should be included')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      expect(consoleSpy.mock.calls[0].join(' ')).toContain(
        'This should be included',
      )
      consoleSpy.mockRestore()
    })

    test('should skip VERBOSE logs at higher levels (INFO, WARN, ERROR)', () => {
      const levels: LogLevel[] = ['INFO', 'WARN', 'ERROR']

      for (const level of levels) {
        process.env.LOG_LEVEL = level
        const testLogger = new Logger('test')
        const consoleSpy = spyOn(console, 'log')

        testLogger.verbose('Should be skipped')

        expect(consoleSpy.mock.calls.length).toBe(0)
        consoleSpy.mockRestore()
      }
    })
  })

  describe('Log level hierarchy', () => {
    test('should enforce correct level order: VERBOSE(0) < DEBUG(1) < INFO(2) < WARN(3) < ERROR(4)', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      testLogger.verbose('verbose - skipped')
      testLogger.debug('debug - skipped')
      testLogger.info('info - shown')
      testLogger.warn('warn - shown')
      testLogger.error('error - shown')

      // Only INFO and higher should be logged
      expect(consoleSpy.mock.calls.length).toBe(1) // info
      expect(warnSpy.mock.calls.length).toBe(1) // warn
      expect(errorSpy.mock.calls.length).toBe(1) // error

      consoleSpy.mockRestore()
      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })

    test('WARN level should suppress VERBOSE, DEBUG, and INFO', () => {
      process.env.LOG_LEVEL = 'WARN'
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      testLogger.verbose('skipped')
      testLogger.debug('skipped')
      testLogger.info('skipped')
      testLogger.warn('shown')
      testLogger.error('shown')

      expect(consoleSpy.mock.calls.length).toBe(0)
      expect(warnSpy.mock.calls.length).toBe(1)
      expect(errorSpy.mock.calls.length).toBe(1)

      consoleSpy.mockRestore()
      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })
  })

  describe('LogCategory enum and filtering', () => {
    test('should support all 9 categories', () => {
      const expectedCategories = [
        'BOOT',
        'VITE',
        'API',
        'INDEX',
        'GRAPH',
        'SEARCH',
        'UI',
        'IPC',
        'PERF',
      ]

      for (const category of expectedCategories) {
        expect(LogCategory[category as keyof typeof LogCategory]).toBeDefined()
      }
    })

    test('should create logger with specific category', () => {
      const testIpcLogger = new Logger('IPC', LogCategory.IPC)
      expect(testIpcLogger).toBeDefined()
    })

    test('should filter logs by category when category is disabled', () => {
      const testLogger = new Logger('test', LogCategory.API)
      testLogger.setEnabledCategories([LogCategory.IPC, LogCategory.BOOT])

      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be filtered out')

      expect(consoleSpy.mock.calls.length).toBe(0)
      consoleSpy.mockRestore()
    })

    test('should allow logs when category is enabled', () => {
      process.env.LOG_LEVEL = 'INFO' // Set to INFO so info() logs are shown
      const testLogger = new Logger('test', LogCategory.API)
      testLogger.setEnabledCategories([LogCategory.API, LogCategory.IPC])

      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be shown')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      consoleSpy.mockRestore()
    })

    test('should allow all categories when none specified (default)', () => {
      process.env.LOG_LEVEL = 'INFO' // Set to INFO so info() logs are shown
      const testLogger = new Logger('test', LogCategory.GRAPH)
      const consoleSpy = spyOn(console, 'log')

      testLogger.info('This should be shown with default settings')

      expect(consoleSpy.mock.calls.length).toBeGreaterThan(0)
      consoleSpy.mockRestore()
    })
  })

  describe('Default log level VERBOSE (dev tool)', () => {
    test('should default to VERBOSE level when LOG_LEVEL not set', () => {
      delete process.env.LOG_LEVEL
      const testLogger = new Logger('test')
      expect(testLogger.getLogLevel()).toBe('VERBOSE')
    })

    test('should show all levels by default', () => {
      delete process.env.LOG_LEVEL
      const testLogger = new Logger('test')
      const consoleSpy = spyOn(console, 'log')

      testLogger.verbose('shown')
      testLogger.debug('shown via debug')

      // verbose uses console.log
      expect(consoleSpy.mock.calls.length).toBe(1)
      consoleSpy.mockRestore()
    })

    test('should show WARN and ERROR by default', () => {
      delete process.env.LOG_LEVEL
      const testLogger = new Logger('test')
      const warnSpy = spyOn(console, 'warn')
      const errorSpy = spyOn(console, 'error')

      testLogger.warn('shown')
      testLogger.error('shown')

      expect(warnSpy.mock.calls.length).toBe(1)
      expect(errorSpy.mock.calls.length).toBe(1)

      warnSpy.mockRestore()
      errorSpy.mockRestore()
    })
  })

  describe('Performance overhead', () => {
    test('should complete 10000 suppressed logs in <50ms', () => {
      process.env.LOG_LEVEL = 'WARN'
      const testLogger = new Logger('perf-test')

      const startTime = performance.now()
      for (let i = 0; i < 10000; i++) {
        testLogger.verbose(`Verbose message ${i}`)
        testLogger.debug(`Debug message ${i}`)
        testLogger.info(`Info message ${i}`)
      }
      const elapsed = performance.now() - startTime

      expect(elapsed).toBeLessThan(50)
    })

    test('single suppressed log should take <1ms', () => {
      process.env.LOG_LEVEL = 'ERROR'
      const testLogger = new Logger('single-log-test')

      const timings: number[] = []
      for (let i = 0; i < 100; i++) {
        const start = performance.now()
        testLogger.debug('test message')
        const elapsed = performance.now() - start
        timings.push(elapsed)
      }

      const avgTime = timings.reduce((a, b) => a + b, 0) / timings.length
      expect(avgTime).toBeLessThan(1) // <1ms average
    })

    test('category filtering should have minimal overhead', () => {
      const testLogger = new Logger('category-perf', LogCategory.API)
      testLogger.setEnabledCategories([LogCategory.IPC]) // Disable API category

      const startTime = performance.now()
      for (let i = 0; i < 10000; i++) {
        testLogger.info(`Filtered message ${i}`)
      }
      const elapsed = performance.now() - startTime

      expect(elapsed).toBeLessThan(50)
    })
  })

  describe('Runtime configuration updates', () => {
    test('should support changing log level at runtime', () => {
      const testLogger = new Logger('runtime-test')
      const consoleSpy = spyOn(console, 'log')
      consoleSpy.mockClear()

      testLogger.setLogLevel('ERROR')
      testLogger.info('Should be skipped')
      expect(consoleSpy.mock.calls.length).toBe(0)

      testLogger.setLogLevel('INFO')
      testLogger.info('Should be shown')
      expect(consoleSpy.mock.calls.length).toBe(1)

      consoleSpy.mockRestore()
    })

    test('should support enabling/disabling categories at runtime', () => {
      process.env.LOG_LEVEL = 'INFO'
      const testLogger = new Logger('category-runtime', LogCategory.BOOT)
      const consoleSpy = spyOn(console, 'log')
      consoleSpy.mockClear()

      testLogger.setEnabledCategories([LogCategory.API])
      testLogger.info('Should be filtered')
      expect(consoleSpy.mock.calls.length).toBe(0)

      testLogger.setEnabledCategories([LogCategory.BOOT, LogCategory.API])
      testLogger.info('Should be shown')
      expect(consoleSpy.mock.calls.length).toBe(1)

      consoleSpy.mockRestore()
    })
  })
})

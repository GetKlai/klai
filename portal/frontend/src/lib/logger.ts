import { createConsola } from 'consola/browser'
import * as Sentry from '@sentry/react'

const logger = createConsola({
  level: import.meta.env.DEV ? 4 : 1,
})

if (!import.meta.env.DEV) {
  logger.addReporter(Sentry.createConsolaReporter())
}

export const authLogger = logger.withTag('auth')
export const editorLogger = logger.withTag('editor')
export const queryLogger = logger.withTag('query')
export const treeLogger = logger.withTag('tree')
export const adminLogger = logger.withTag('admin')
export const focusLogger = logger.withTag('focus')
export const helpLogger = logger.withTag('help')
export const taxonomyLogger = logger.withTag('taxonomy')
export const chatKbLogger = logger.withTag('chat-kb')
export const perfLogger = logger.withTag('perf')

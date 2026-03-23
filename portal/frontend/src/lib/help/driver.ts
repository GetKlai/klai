import type { Config } from 'driver.js'
import { driver } from 'driver.js'
import 'driver.js/dist/driver.css'

export type DriverInstance = ReturnType<typeof driver>

export function createDriver(config?: Partial<Config>): DriverInstance {
  return driver({
    animate: true,
    smoothScroll: false,
    allowClose: true,
    ...config,
  })
}

export function destroyDriver(d: DriverInstance | null): void {
  if (d) {
    try {
      d.destroy()
    } catch {
      // ignore if already destroyed
    }
  }
}

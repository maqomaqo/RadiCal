// The verdict that decides whether a map draws on OpenStreetMap or on the
// app's own coastlines. What is pinned down: no verdict before the probe
// answers, one probe shared across every map that asks, a verdict that ages
// out, and a probe that never rejects into the map.
import { describe, expect, it, vi } from 'vitest'

import { TileServerMonitor, probeTileServer } from './tileServer'

/** Let every settled promise's continuation run. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('TileServerMonitor', () => {
  it('has no verdict until the probe answers, then keeps it', async () => {
    const probe = deferred<boolean>()
    const monitor = new TileServerMonitor(() => probe.promise, 60_000, () => 0)
    const seen: Array<boolean | null> = []
    monitor.subscribe((v) => seen.push(v))

    expect(monitor.check()).toBeNull()
    probe.resolve(true)
    await flush()

    expect(monitor.current()).toBe(true)
    expect(seen).toEqual([true])
  })

  it('shares one probe between every map that asks at once', () => {
    const probe = vi.fn(() => new Promise<boolean>(() => {}))
    const monitor = new TileServerMonitor(probe, 60_000, () => 0)
    monitor.check()
    monitor.check()
    monitor.check()
    expect(probe).toHaveBeenCalledTimes(1)
  })

  it('asks again once the verdict has aged past the TTL', async () => {
    let now = 0
    const probe = vi.fn(() => Promise.resolve(true))
    const monitor = new TileServerMonitor(probe, 1000, () => now)
    monitor.check()
    await flush()
    expect(monitor.current()).toBe(true)

    now = 500
    monitor.check()
    expect(probe).toHaveBeenCalledTimes(1)

    now = 1500
    monitor.check()
    expect(probe).toHaveBeenCalledTimes(2)
  })

  it('treats a probe that throws as unreachable rather than as no answer', async () => {
    const monitor = new TileServerMonitor(() => Promise.reject(new Error('boom')), 60_000, () => 0)
    monitor.check()
    await flush()
    expect(monitor.current()).toBe(false)
  })

  it('forgets on invalidate so the next check probes afresh', async () => {
    const probe = vi.fn(() => Promise.resolve(false))
    const monitor = new TileServerMonitor(probe, 60_000, () => 0)
    monitor.check()
    await flush()
    expect(monitor.current()).toBe(false)

    monitor.invalidate()
    expect(monitor.current()).toBeNull()
    monitor.check()
    expect(probe).toHaveBeenCalledTimes(2)
  })

  it('asks again after a negative while something is listening, then gives up', async () => {
    vi.useFakeTimers()
    try {
      const probe = vi.fn(() => Promise.resolve(false))
      const monitor = new TileServerMonitor(probe, 60_000, () => Date.now(), [100, 200])
      const off = monitor.subscribe(() => {})
      monitor.check()
      await vi.advanceTimersByTimeAsync(0)
      expect(probe).toHaveBeenCalledTimes(1)
      expect(monitor.current()).toBe(false)

      await vi.advanceTimersByTimeAsync(100)
      expect(probe).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(200)
      expect(probe).toHaveBeenCalledTimes(3)
      // The schedule is spent: no fourth ask, however long it waits.
      await vi.advanceTimersByTimeAsync(10_000)
      expect(probe).toHaveBeenCalledTimes(3)
      off()
    } finally {
      vi.useRealTimers()
    }
  })

  it('corrects a wrong negative under the maps that fell back', async () => {
    vi.useFakeTimers()
    try {
      let answer = false
      const monitor = new TileServerMonitor(() => Promise.resolve(answer), 60_000, () => Date.now(), [50])
      const seen: Array<boolean | null> = []
      monitor.subscribe((v) => seen.push(v))
      monitor.check()
      await vi.advanceTimersByTimeAsync(0)
      answer = true
      await vi.advanceTimersByTimeAsync(50)
      expect(seen).toEqual([false, true])
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not retry into an empty room', async () => {
    vi.useFakeTimers()
    try {
      const probe = vi.fn(() => Promise.resolve(false))
      const monitor = new TileServerMonitor(probe, 60_000, () => Date.now(), [50])
      monitor.check()
      await vi.advanceTimersByTimeAsync(1_000)
      expect(probe).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops telling a listener that unsubscribed', async () => {
    const monitor = new TileServerMonitor(() => Promise.resolve(true), 60_000, () => 0)
    const listener = vi.fn()
    const off = monitor.subscribe(listener)
    off()
    monitor.check()
    await flush()
    expect(listener).not.toHaveBeenCalled()
  })
})

describe('probeTileServer', () => {
  // A stand-in for Image that lets the test decide how the load ends.
  function fakeImage(outcome: 'load' | 'error' | 'hang' | 'landed-silently') {
    const requested: string[] = []
    class FakeImage {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      // What a real Image reports once its bytes are in, whether or not
      // its load event has been delivered yet.
      complete = false
      naturalWidth = 0
      private _src = ''
      get src() {
        return this._src
      }
      set src(value: string) {
        this._src = value
        if (!value) return
        requested.push(value)
        if (outcome === 'load') queueMicrotask(() => this.onload?.())
        if (outcome === 'error') queueMicrotask(() => this.onerror?.())
        // The bytes arrive but the event never gets to run before the
        // timer does -- a page whose main thread was busy drawing.
        if (outcome === 'landed-silently') {
          this.complete = true
          this.naturalWidth = 256
        }
      }
    }
    return { FakeImage: FakeImage as unknown as typeof Image, requested }
  }

  it('is true when the tile loads', async () => {
    const { FakeImage, requested } = fakeImage('load')
    await expect(probeTileServer('https://x/0/0/0.png', 1000, FakeImage)).resolves.toBe(true)
    // Cache-busted, so a stale cached tile cannot vouch for the connection.
    expect(requested[0]).toMatch(/^https:\/\/x\/0\/0\/0\.png\?probe=\d+$/)
  })

  it('is false when the tile errors', async () => {
    const { FakeImage } = fakeImage('error')
    await expect(probeTileServer('https://x/0/0/0.png', 1000, FakeImage)).resolves.toBe(false)
  })

  it('is false when the tile never arrives', async () => {
    vi.useFakeTimers()
    try {
      const { FakeImage } = fakeImage('hang')
      const result = probeTileServer('https://x/0/0/0.png', 50, FakeImage)
      await vi.advanceTimersByTimeAsync(60)
      await expect(result).resolves.toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('is true when the tile landed while the page was too busy to hear it', async () => {
    vi.useFakeTimers()
    try {
      const { FakeImage } = fakeImage('landed-silently')
      const result = probeTileServer('https://x/0/0/0.png', 50, FakeImage)
      await vi.advanceTimersByTimeAsync(60)
      await expect(result).resolves.toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('is false where there is no Image at all', async () => {
    await expect(probeTileServer('https://x/0/0/0.png', 1000, undefined)).resolves.toBe(false)
  })
})

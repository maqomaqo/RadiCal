// Whether the basemap host can be reached, so a project that has opted into
// the tiled basemap still gets a map when there is no internet.
//
// MapLibre does not degrade when its tiles fail: it draws the marks on a
// blank grey square and says nothing. So before the tiled map is drawn at
// all, one small tile is asked for and the style is fetched, and if either
// does not arrive the map falls back to the offline basemap. The tile asked
// for is a zoom-0 whole-world tile, which names no place -- so the probe
// gives away nothing the map itself would not.

import { BASEMAP_PROBE_URL, loadBasemapStyle } from '@/theme/basemapStyle'

/** The image the probe asks for. See BASEMAP_PROBE_URL. */
export const PROBE_TILE_URL = BASEMAP_PROBE_URL

/**
 * How long to wait for the probe tile before concluding the server is out
 * of reach. Long enough for a slow connection to deliver a 10 kB image,
 * short enough that an offline user is not looking at a blank box for it.
 *
 * Measured by the browser's timer, which is NOT wall-clock time when the
 * page is busy: on a results page with eight hundred dates the main thread
 * is drawing the stacked plot for ten seconds or more, and the timer's
 * callback and the tile's load event both wait for it. Which of the two
 * runs first afterwards is not promised, so when the timer wins it checks
 * whether the tile has in fact arrived before calling the server
 * unreachable -- see probeTileServer. Without that check the first view of
 * a large analysis fell back to the offline map about half the time and a
 * refresh, with the plot's data cached, did not.
 */
export const PROBE_TIMEOUT_MS = 4000

/**
 * How soon to ask again after a NEGATIVE verdict, then how much longer each
 * time. A wrong "unreachable" is the expensive mistake -- every map on the
 * page draws the wrong basemap for the verdict's whole life -- and a real
 * one costs nothing to confirm, so a negative is re-checked a few times
 * while anything is still listening, and then left alone until the browser
 * reports the connection back.
 */
export const RETRY_AFTER_MS = [5_000, 15_000, 45_000]

/**
 * How long a verdict is trusted before the next map to mount asks again.
 * The maps on one page share a verdict rather than each probing; a page
 * left open for a while re-checks, since a connection can come and go.
 */
export const VERDICT_TTL_MS = 60_000

export type Reachability = boolean | null

/**
 * Ask for one tile, resolving to whether it arrived.
 *
 * An Image rather than fetch, deliberately, and for two reasons. An image
 * load succeeds only when the server answered with an image, so a 4xx --
 * reachable, but not serving -- counts as unreachable, which is the answer
 * the map needs. And an Image can be asked afterwards whether its bytes
 * came, which is how the timeout survives a busy page (see PROBE_TIMEOUT_MS).
 */
export function probeTileServer(
  url: string = PROBE_TILE_URL,
  timeoutMs: number = PROBE_TIMEOUT_MS,
  ImageCtor: typeof Image | undefined = typeof Image === 'undefined' ? undefined : Image,
): Promise<boolean> {
  if (!ImageCtor) return Promise.resolve(false)
  return new Promise((resolve) => {
    const image = new ImageCtor()
    let settled = false
    const settle = (ok: boolean) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      image.onload = null
      image.onerror = null
      // Abandon the request if it is still in flight.
      image.src = ''
      resolve(ok)
    }
    const timer = setTimeout(() => {
      // The timer ran late if the page was busy, and the tile may have
      // landed in the meantime with its load event still queued behind
      // this. A complete image with a size is a tile that arrived.
      settle(Boolean(image.complete && image.naturalWidth > 0))
    }, timeoutMs)
    image.onload = () => settle(true)
    image.onerror = () => settle(false)
    // A cache-buster, so a tile the browser cached last week does not vouch
    // for a connection that has since gone.
    image.src = `${url}${url.includes('?') ? '&' : '?'}probe=${Date.now()}`
  })
}

/**
 * The whole probe: the tile, then the style. The style is what the map is
 * drawn on, so a host that serves tiles but not the style is not reachable
 * in any sense the map can use. Fetched once and kept -- see
 * loadBasemapStyle -- so only the first probe pays for it.
 */
export function probeBasemap(): Promise<boolean> {
  return probeTileServer().then((ok) =>
    ok ? loadBasemapStyle().then(() => true, () => false) : false,
  )
}

type Listener = (verdict: Reachability) => void

/**
 * One shared verdict for every map on the page.
 *
 * Module state rather than a context, because the answer is a property of
 * the browser's connection, not of any part of the tree -- and because the
 * maps that need it are in dialogs and panels with no common ancestor
 * short of the app.
 */
class TileServerMonitor {
  private verdict: Reachability = null
  private decidedAt = 0
  private inflight: Promise<boolean> | null = null
  private listeners = new Set<Listener>()
  private listeningToWindow = false

  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private retriesMade = 0

  constructor(
    private readonly probe: () => Promise<boolean> = probeBasemap,
    private readonly ttlMs: number = VERDICT_TTL_MS,
    private readonly now: () => number = () => Date.now(),
    private readonly retryAfterMs: readonly number[] = RETRY_AFTER_MS,
  ) {}

  /** The current verdict; null until the first probe has answered. */
  current(): Reachability {
    return this.verdict
  }

  /**
   * Make sure a fresh verdict is on its way, and return the one in hand.
   *
   * Cheap to call from every mount: it probes only when there is no verdict,
   * the one there is has aged past the TTL, and no probe is already running.
   */
  check(): Reachability {
    this.attachToWindow()
    const stale = this.verdict === null || this.now() - this.decidedAt > this.ttlMs
    if (stale && !this.inflight) this.ask()
    return this.verdict
  }

  /** Probe now, whatever the verdict in hand. */
  private ask(): void {
    if (this.inflight) return
    // The browser knows it is offline before any request could: skip the
    // wait and say so. Only the negative is trusted -- `onLine` true means
    // a network interface is up, not that anything answers on it.
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      this.decide(false)
      return
    }
    this.inflight = this.probe()
      .then((ok) => ok, () => false)
      .then((ok) => {
        this.inflight = null
        this.decide(ok)
        return ok
      })
  }

  /** Subscribe to verdict changes. Returns the unsubscribe. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** Forget the verdict, so the next check probes again. */
  invalidate(): void {
    this.verdict = null
    this.decidedAt = 0
    this.retriesMade = 0
    this.cancelRetry()
    this.notify()
  }

  private decide(ok: boolean): void {
    this.verdict = ok
    this.decidedAt = this.now()
    this.notify()
    if (ok) {
      this.retriesMade = 0
      this.cancelRetry()
    } else {
      this.scheduleRetry()
    }
  }

  /**
   * After a negative, ask again on the RETRY_AFTER_MS schedule while
   * anything is listening. A map that fell back is subscribed, so a wrong
   * verdict is corrected under it; an empty page asks nothing.
   */
  private scheduleRetry(): void {
    this.cancelRetry()
    const wait = this.retryAfterMs[this.retriesMade]
    if (wait === undefined || this.listeners.size === 0) return
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      this.retriesMade += 1
      if (this.listeners.size > 0) this.ask()
    }, wait)
  }

  private cancelRetry(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
  }

  private notify(): void {
    for (const listener of this.listeners) listener(this.verdict)
  }

  /**
   * Let the browser's own online/offline events move the verdict. Going
   * offline is believed at once; coming back online only earns a re-probe,
   * since the event says an interface came up, not that the tile server is
   * on the other end of it.
   */
  private attachToWindow(): void {
    if (this.listeningToWindow || typeof window === 'undefined') return
    this.listeningToWindow = true
    window.addEventListener('offline', () => this.decide(false))
    window.addEventListener('online', () => {
      this.invalidate()
      this.check()
    })
  }
}

export { TileServerMonitor }

/** The app's one monitor. Tests build their own with a fake probe. */
export const tileServerMonitor = new TileServerMonitor()

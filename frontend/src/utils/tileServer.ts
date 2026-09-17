// Whether the tile server can be reached, so a project that has opted into
// OpenStreetMap still gets a map when there is no internet.
//
// MapLibre does not degrade when its tiles fail: it draws the marks on a
// blank grey square and says nothing. So before the tiled map is drawn at
// all, one tile is asked for, and if it does not arrive the map falls back to
// the offline basemap. The tile asked for is the zoom-0 whole-world tile,
// which every tiled map requests anyway and which names no place -- so the
// probe gives away nothing the map itself would not.

/**
 * The tile Plotly's `open-street-map` style fetches from; one of the three
 * hosts it round-robins between. Kept in step with plotly.js-dist-min.
 */
export const PROBE_TILE_URL = 'https://tile.openstreetmap.org/0/0/0.png'

/**
 * How long to wait for the probe tile before concluding the server is out
 * of reach. Long enough for a slow connection to deliver a 10 kB image,
 * short enough that an offline user is not looking at a blank box for it.
 */
export const PROBE_TIMEOUT_MS = 4000

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
 * An Image rather than fetch, deliberately: an image load succeeds only
 * when the server answered with an image it would also serve as a tile, so a
 * 4xx from the tile server -- reachable, but not serving -- counts as
 * unreachable, which is the answer the map needs. A no-cors fetch would call
 * that a success.
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
    const timer = setTimeout(() => settle(false), timeoutMs)
    image.onload = () => settle(true)
    image.onerror = () => settle(false)
    // A cache-buster, so a tile the browser cached last week does not vouch
    // for a connection that has since gone.
    image.src = `${url}${url.includes('?') ? '&' : '?'}probe=${Date.now()}`
  })
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

  constructor(
    private readonly probe: () => Promise<boolean> = () => probeTileServer(),
    private readonly ttlMs: number = VERDICT_TTL_MS,
    private readonly now: () => number = () => Date.now(),
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
    if (stale && !this.inflight) {
      // The browser knows it is offline before any request could: skip the
      // wait and say so. Only the negative is trusted -- `onLine` true
      // means a network interface is up, not that anything answers on it.
      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        this.decide(false)
      } else {
        this.inflight = this.probe()
          .then((ok) => ok, () => false)
          .then((ok) => {
            this.inflight = null
            this.decide(ok)
            return ok
          })
      }
    }
    return this.verdict
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
    this.notify()
  }

  private decide(ok: boolean): void {
    this.verdict = ok
    this.decidedAt = this.now()
    this.notify()
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

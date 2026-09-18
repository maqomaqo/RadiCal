import { useSyncExternalStore } from 'react'

/**
 * The reader's own switch between the tiled basemap and the app's offline
 * one, on every map at once.
 *
 * Distinct from the project's setting, which is a permission: a project
 * that has not opted into tiles never shows the switch, because turning
 * tiles on is the decision that sends a request naming the square of the
 * earth on screen to a third party, and that decision is made once, in the
 * project's own dialog, behind a notice. This is the smaller choice a
 * project that HAS opted in leaves to the reader: which of the two allowed
 * maps to look at right now. Kept in the browser, so it holds across pages
 * and reloads on this machine and travels nowhere.
 */
const STORAGE_KEY = 'radical.basemap'

type Preference = 'tiles' | 'local'

const listeners = new Set<() => void>()

function read(): Preference {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'local' ? 'local' : 'tiles'
  } catch {
    return 'tiles'
  }
}

function write(preference: Preference): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    // A private window, or storage refused: the choice lasts the page.
  }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Whether the reader wants tiles where they are allowed, and the setter. */
export function useBasemapPreference(): [boolean, (preferTiles: boolean) => void] {
  const preference = useSyncExternalStore(subscribe, read, () => 'tiles' as Preference)
  return [preference === 'tiles', (preferTiles) => write(preferTiles ? 'tiles' : 'local')]
}

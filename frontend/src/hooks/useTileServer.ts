import { useEffect, useState } from 'react'

import { tileServerMonitor, type Reachability } from '@/utils/tileServer'

/**
 * Whether the OpenStreetMap tile server can be reached, for a map that
 * wants to draw on it.
 *
 * Null while the first probe is out, then true or false; moves with the
 * browser's own online/offline events after that. With `wanted` false no
 * probe is made at all -- a project that has not opted into tiles must not
 * send even the zoom-0 request, which is the whole point of the opt-in.
 */
export function useTileServerReachable(wanted: boolean): Reachability {
  const [verdict, setVerdict] = useState<Reachability>(() =>
    wanted ? tileServerMonitor.current() : null,
  )

  useEffect(() => {
    if (!wanted) return
    // Subscribed BEFORE asking, so a verdict that comes back at once -- the
    // browser already knows it is offline -- still has a listener, and the
    // monitor knows something is waiting on the retry it then schedules.
    const unsubscribe = tileServerMonitor.subscribe(setVerdict)
    setVerdict(tileServerMonitor.check())
    return unsubscribe
  }, [wanted])

  return wanted ? verdict : null
}

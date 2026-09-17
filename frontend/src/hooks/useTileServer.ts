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
    setVerdict(tileServerMonitor.check())
    return tileServerMonitor.subscribe(setVerdict)
  }, [wanted])

  return wanted ? verdict : null
}

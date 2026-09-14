// What the map draws, and how a click becomes a position.
//
// The load-bearing pair here is geoProjectionOf + latLonFromPoint. Between
// them they read Plotly internals, because the public event API cannot answer
// "where on the map did they click?" -- plotly_click on a geo subplot fires
// only when a mark is hit. So what is pinned down is the FAILING: an upgrade
// that moves those internals has to make placement unavailable, never throw
// and never place a date at NaN.
import { describe, expect, it } from 'vitest'

import {
  frameFor,
  geoProjectionOf,
  groupIntoSites,
  latLonFromMapPoint,
  latLonFromPoint,
  mapLibreOf,
  roundPlacement,
  siteLabel,
  unplaced,
  zoomForFrame,
} from './siteMapGeometry'
import type { GeoProjection, MapLibreMap, MapPoint } from './siteMapGeometry'

const point = (id: string, latitude: number | null, longitude: number | null): MapPoint => ({
  id,
  name: id,
  latitude,
  longitude,
})

describe('groupIntoSites', () => {
  it('makes one site of the dates sharing a position', () => {
    // The app has no sites table: dates at one coordinate ARE a site, so a
    // midden with three dates on it is one pin that says three.
    const sites = groupIntoSites([
      point('A', 54.5, -5.6),
      point('B', 54.5, -5.6),
      point('C', 54.5, -5.6),
    ])
    expect(sites).toHaveLength(1)
    expect(sites[0].ids).toEqual(['A', 'B', 'C'])
  })

  it('keeps two nearby positions apart', () => {
    // Grouping by proximity would silently merge two pins a user placed
    // separately, and there would be no way to un-merge them from the map.
    const sites = groupIntoSites([point('A', 54.5, -5.6), point('B', 54.5001, -5.6)])
    expect(sites).toHaveLength(2)
  })

  it('skips a date with no position', () => {
    expect(groupIntoSites([point('A', null, null)])).toEqual([])
  })

  it('skips half a coordinate rather than guessing the other half', () => {
    // The API refuses half a position, so one arriving here is a sign
    // something went wrong -- and guessing would put the date in the sea.
    expect(groupIntoSites([point('A', 54.5, null), point('B', null, -5.6)])).toEqual([])
  })

  it('treats zero as a position, because it is one', () => {
    const sites = groupIntoSites([point('A', 0, 0)])
    expect(sites).toHaveLength(1)
    expect([sites[0].latitude, sites[0].longitude]).toEqual([0, 0])
  })
})

describe('unplaced', () => {
  it('is the list beside the map', () => {
    const missing = unplaced([point('A', 54.5, -5.6), point('B', null, null), point('C', 54.5, null)])
    expect(missing.map((p) => p.id)).toEqual(['B', 'C'])
  })
})

describe('siteLabel', () => {
  it('names a lone date', () => {
    expect(siteLabel(groupIntoSites([point('OxA-1', 54.5, -5.6)])[0])).toBe('OxA-1')
  })

  it('counts them once there is more than one', () => {
    // Fourteen names in a tooltip is not a tooltip.
    const site = groupIntoSites([point('A', 54.5, -5.6), point('B', 54.5, -5.6)])[0]
    expect(siteLabel(site)).toBe('2 dates')
  })
})

describe('frameFor', () => {
  it('is null when nothing is placed, so the caller shows the world', () => {
    expect(frameFor([])).toBeNull()
  })

  it('opens on a readable window around a single site', () => {
    // One point has no extent, and a zero-width range is not a map. The span
    // is set by what 110m geometry can draw without looking like a polygon,
    // not by how close the reader would like to get.
    const frame = frameFor(groupIntoSites([point('A', 54.5, -5.6)]))!
    expect(frame.lat[1] - frame.lat[0]).toBeCloseTo(6)
    expect(frame.lon[1] - frame.lon[0]).toBeCloseTo(6)
    expect((frame.lat[0] + frame.lat[1]) / 2).toBeCloseTo(54.5)
  })

  it('does not shrink a spread wider than that minimum', () => {
    const frame = frameFor(groupIntoSites([point('A', 20, -40), point('B', 60, 30)]))!
    expect(frame.lat[0]).toBeLessThan(20)
    expect(frame.lat[1]).toBeGreaterThan(60)
  })

  it('pads a spread of sites rather than cropping them to the edge', () => {
    const frame = frameFor(groupIntoSites([point('A', 50, -10), point('B', 58, 2)]))!
    expect(frame.lat[0]).toBeLessThan(50)
    expect(frame.lat[1]).toBeGreaterThan(58)
    expect(frame.lon[0]).toBeLessThan(-10)
    expect(frame.lon[1]).toBeGreaterThan(2)
  })

  it('never runs off the earth', () => {
    // A site near the pole must not open a map reaching to 91, which draws as
    // an empty band above the top of the world.
    const frame = frameFor(groupIntoSites([point('A', 89.5, 179.5)]))!
    expect(frame.lat[1]).toBeLessThanOrEqual(90)
    expect(frame.lon[1]).toBeLessThanOrEqual(180)
  })
})

describe('geoProjectionOf', () => {
  // The shape Plotly 2.35.3 actually has, confirmed in a browser.
  const realShape = {
    _fullLayout: { geo: { _subplot: { projection: { invert: () => [0, 0] } } } },
  }

  it('finds the projection on a real graph div', () => {
    expect(geoProjectionOf(realShape)).not.toBeNull()
  })

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['no _fullLayout', {}],
    ['no geo', { _fullLayout: {} }],
    ['no _subplot', { _fullLayout: { geo: {} } }],
    ['no projection', { _fullLayout: { geo: { _subplot: {} } } }],
    ['projection without invert', { _fullLayout: { geo: { _subplot: { projection: {} } } } }],
  ])('returns null rather than throwing when there is %s', (_label, div) => {
    expect(geoProjectionOf(div)).toBeNull()
  })
})

describe('latLonFromPoint', () => {
  const projection = (result: unknown): GeoProjection =>
    ({ invert: () => result }) as GeoProjection

  it('reads a position out of the projection', () => {
    // Plotly's order is [lon, lat]; ours is latitude first, and getting that
    // backwards puts every British site in Somalia.
    const place = latLonFromPoint(projection([-5.6, 54.5]), 100, 100)
    expect(place).toEqual({ latitude: 54.5, longitude: -5.6 })
  })

  it('passes the point through in the projection frame', () => {
    const seen: Array<[number, number]> = []
    const spy = { invert: (p: [number, number]) => { seen.push(p); return [1, 2] } }
    latLonFromPoint(spy as GeoProjection, 42, 17)
    expect(seen).toEqual([[42, 17]])
  })

  it('is null off the globe, where an interrupted projection has no answer', () => {
    expect(latLonFromPoint(projection(null), 0, 0)).toBeNull()
    expect(latLonFromPoint(projection(undefined), 0, 0)).toBeNull()
  })

  it('is null for a point that inverts to nothing numeric', () => {
    expect(latLonFromPoint(projection([NaN, NaN]), 0, 0)).toBeNull()
    expect(latLonFromPoint(projection([Infinity, 10]), 0, 0)).toBeNull()
  })

  it('is null past the pole or the date line', () => {
    expect(latLonFromPoint(projection([0, 91]), 0, 0)).toBeNull()
    expect(latLonFromPoint(projection([181, 0]), 0, 0)).toBeNull()
  })

  it('does not throw when the projection does', () => {
    const throwing = { invert: () => { throw new Error('not a place') } } as GeoProjection
    expect(latLonFromPoint(throwing, 0, 0)).toBeNull()
  })
})

describe('roundPlacement', () => {
  it('keeps four decimals, about ten metres', () => {
    expect(roundPlacement(54.56789123)).toBe(54.5679)
    expect(roundPlacement(-5.63123456)).toBe(-5.6312)
  })

  it('does not let a click claim a surveyed position', () => {
    // A pin dropped on a world map is not accurate to ten centimetres, and
    // the value it writes is exported as the site's recorded location.
    expect(String(roundPlacement(54.567891234567))).not.toMatch(/\d{5,}$/)
  })

  it('leaves a value that is already coarse alone', () => {
    expect(roundPlacement(54.5)).toBe(54.5)
    expect(roundPlacement(0)).toBe(0)
  })
})

describe('mapLibreOf', () => {
  // The shape MapLibre actually has under Plotly 2.35.3, confirmed in a
  // browser against a tile-free style.
  const realShape = {
    _fullLayout: {
      map: { _subplot: { map: { unproject: () => ({ lat: 0, lng: 0 }), getContainer: () => null } } },
    },
  }

  it('finds the map on a real graph div', () => {
    expect(mapLibreOf(realShape)).not.toBeNull()
  })

  it.each([
    ['null', null],
    ['no _fullLayout', {}],
    ['no map', { _fullLayout: {} }],
    ['no _subplot', { _fullLayout: { map: {} } }],
    ['no maplibre', { _fullLayout: { map: { _subplot: {} } } }],
    ['no unproject', { _fullLayout: { map: { _subplot: { map: { getContainer: () => null } } } } }],
    ['no getContainer', { _fullLayout: { map: { _subplot: { map: { unproject: () => null } } } } }],
  ])('returns null rather than throwing when there is %s', (_label, div) => {
    expect(mapLibreOf(div)).toBeNull()
  })

  it('does not mistake the offline map for the tiled one', () => {
    // The two live at different keys. Reading one as the other would give a
    // projection with the wrong frame and place every pin in the wrong place.
    const geoOnly = { _fullLayout: { geo: { _subplot: { projection: { invert: () => [0, 0] } } } } }
    expect(mapLibreOf(geoOnly)).toBeNull()
    expect(geoProjectionOf(realShape)).toBeNull()
  })
})

describe('latLonFromMapPoint', () => {
  const map = (result: unknown): MapLibreMap =>
    ({ unproject: () => result, getContainer: () => null }) as MapLibreMap

  it('translates MapLibre lng into longitude', () => {
    expect(latLonFromMapPoint(map({ lat: 54.5, lng: -5.6 }), 10, 10)).toEqual({
      latitude: 54.5,
      longitude: -5.6,
    })
  })

  it('refuses the same nonsense the offline map refuses', () => {
    expect(latLonFromMapPoint(map(null), 0, 0)).toBeNull()
    expect(latLonFromMapPoint(map({ lat: NaN, lng: 0 }), 0, 0)).toBeNull()
    expect(latLonFromMapPoint(map({ lat: 91, lng: 0 }), 0, 0)).toBeNull()
  })

  it('does not throw when unproject does', () => {
    const throwing = {
      unproject: () => { throw new Error('no') },
      getContainer: () => null,
    } as MapLibreMap
    expect(latLonFromMapPoint(throwing, 0, 0)).toBeNull()
  })
})

describe('zoomForFrame', () => {
  it('is further out for a wider window', () => {
    const wide = zoomForFrame({ lon: [-40, 40], lat: [0, 40] })
    const narrow = zoomForFrame({ lon: [-3, 3], lat: [50, 56] })
    expect(wide).toBeLessThan(narrow)
  })

  it('shows the world at the bottom of its range', () => {
    expect(zoomForFrame({ lon: [-180, 180], lat: [-90, 90] })).toBe(1)
  })

  it('is clamped, so a degenerate window cannot ask for infinite zoom', () => {
    const z = zoomForFrame({ lon: [5, 5], lat: [5, 5] })
    expect(Number.isFinite(z)).toBe(true)
    expect(z).toBeLessThanOrEqual(12)
  })
})

import { describe, it, expect } from 'vitest'
import { groupingPreview, groupsFor, haversineKm, siteModelTree } from './siteGrouping'
import type { ModelElement } from '@/types'

function find(root: ModelElement, id: string): ModelElement | null {
  if (root.id === id) return root
  for (const child of root.children || []) {
    const hit = find(child, id)
    if (hit) return hit
  }
  return null
}

function parentOf(root: ModelElement, id: string): ModelElement | null {
  for (const child of root.children || []) {
    if (child.id === id) return root
    const hit = parentOf(child, id)
    if (hit) return hit
  }
  return null
}

const cook = [
  { id: 'a', location: 'Rarotonga', latitude: -21.23, longitude: -159.78 },
  { id: 'b', location: 'Rarotonga', latitude: -21.23, longitude: -159.78 },
  { id: 'c', location: 'Mangaia', latitude: -21.92, longitude: -157.92 },
  { id: 'd', location: 'Aitutaki', latitude: -18.85, longitude: -159.79 },
]

describe('groupsFor', () => {
  it('groups by location in the order the dates introduce it', () => {
    const { groups, unassigned } = groupsFor(cook, 'location')
    expect(groups.map((g) => g.name)).toEqual(['Rarotonga', 'Mangaia', 'Aitutaki'])
    expect(groups[0].ids).toEqual(['a', 'b'])
    expect(unassigned).toEqual([])
  })

  it('leaves dates with no value for the key aside rather than inventing one', () => {
    const { groups, unassigned } = groupsFor(
      [{ id: 'a', location: 'X' }, { id: 'b', location: '  ' }, { id: 'c' }],
      'location',
    )
    expect(groups.map((g) => g.name)).toEqual(['X'])
    expect(unassigned).toEqual(['b', 'c'])
  })

  it('groups by context name when asked', () => {
    const { groups } = groupsFor(
      [{ id: 'a', default_context_name: 'Layer 2', location: 'ignored' }, { id: 'b', default_context_name: 'Layer 2' }],
      'context',
    )
    expect(groups).toEqual([{ name: 'Layer 2', ids: ['a', 'b'] }])
  })

  it('groups by position within the same radius the server uses', () => {
    // Two coordinates 3 km apart are one place; 40 km apart are two.
    const near = { id: 'n', latitude: -21.23, longitude: -159.75 }
    expect(haversineKm(-21.23, -159.78, near.latitude, near.longitude)).toBeLessThan(5)
    const { groups, unassigned } = groupsFor([...cook, near, { id: 'u' }], 'position')
    expect(groups.map((g) => g.ids)).toEqual([['a', 'b', 'n'], ['c'], ['d']])
    expect(unassigned).toEqual(['u'])
  })

  it('names a position group after its location, or its coordinate when it has none', () => {
    const { groups } = groupsFor(
      [
        { id: 'a', latitude: -21.23, longitude: -159.78 },
        { id: 'b', latitude: -21.23, longitude: -159.78, location: 'Rarotonga' },
        { id: 'c', latitude: -18.85, longitude: -159.79 },
      ],
      'position',
    )
    expect(groups.map((g) => g.name)).toEqual(['Rarotonga', 'Site 2 (-18.85, -159.79)'])
  })
})

describe('siteModelTree', () => {
  it('builds one bracketed sequence per site inside an unordered phase', () => {
    const { tree } = siteModelTree(cook, 'location')

    expect(tree.element_type).toBe('sequence')
    const [start, sites, end] = tree.children || []
    expect([start.element_type, sites.element_type, end.element_type]).toEqual([
      'boundary', 'phase', 'boundary',
    ])
    // A Phase, not a Sequence: the order of the sites is what the dispersal
    // models are for finding out, so it must not be asserted here.
    expect(sites.name).toBe('Sites')
    expect((sites.children || []).map((c) => c.element_type)).toEqual([
      'sequence', 'sequence', 'sequence',
    ])
    for (const site of sites.children || []) {
      expect((site.children || []).map((c) => c.element_type)).toEqual([
        'boundary', 'phase', 'boundary',
      ])
      expect(site.children![0].name).toBe(`Start of ${site.name}`)
    }
  })

  it('assigns every date to the phase inside its site', () => {
    const { tree, assignments } = siteModelTree(cook, 'location')
    expect(Object.keys(assignments).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(assignments.a).toBe(assignments.b)
    expect(find(tree, assignments.a)?.element_type).toBe('phase')
    expect(parentOf(tree, assignments.a)?.name).toBe('Rarotonga')
    expect(parentOf(tree, assignments.c)?.name).toBe('Mangaia')
  })

  it('puts dates with no value in a plain phase beside the sites, with no boundaries of its own', () => {
    const { tree, assignments } = siteModelTree([...cook, { id: 'x' }], 'location')
    const sites = (tree.children || [])[1]
    const last = (sites.children || [])[3]
    expect(last.element_type).toBe('phase')
    expect(last.name).toBe('Unassigned dates')
    expect(assignments.x).toBe(last.id)
  })
})

describe('groupingPreview', () => {
  it('counts what a key would make before anything is replaced', () => {
    expect(groupingPreview([...cook, { id: 'x' }], 'location')).toEqual({ sites: 3, unassigned: 1 })
    expect(groupingPreview(cook, 'context')).toEqual({ sites: 0, unassigned: 4 })
  })
})

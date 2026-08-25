// Period clustering.
//
// The rule being pinned here is that clustering and ordering are two
// different questions. A cluster is a set of analyses that can share one
// calendar axis -- decided chronologically. The order they appear in is
// decided by recency, because the surface is "recent analyses" and turning
// it into a chronological index would quietly answer a different question.
//
// The other rule worth a test: overlap wins over the width ratio. A wide
// survey and a narrow date sitting inside it are one period no matter how
// different their widths are, and splitting them would put an analysis on
// its own axis while its own samples sat on another.
import { describe, expect, it } from 'vitest'

import { clusterByPeriod } from './periodClusters'

const at = (name: string, span: [number, number] | null, created: string) => ({
  name,
  calendar_span: span,
  created_at: created,
})

const names = (items: { name: string }[]) => items.map((i) => i.name)

describe('clusterByPeriod', () => {
  it('keeps one axis while the corpus is one period', () => {
    // The real Wairau Bar / Tikoraki corpus: 616 years end to end, narrowest
    // member 80 -- a ratio of 7.7, inside the readable limit.
    const { clusters } = clusterByPeriod([
      at('tikoraki', [145, 491], '2026-08-22T12:33:47'),
      at('nenthorn', [542, 761], '2026-08-22T12:07:11'),
      at('wairau', [555, 655], '2026-08-20T01:04:37'),
      at('jacomb', [555, 635], '2026-08-20T00:46:06'),
    ])
    expect(clusters).toHaveLength(1)
    expect(clusters[0].span).toEqual([145, 761])
  })

  it('splits off a period far enough away to flatten the rest', () => {
    const { clusters } = clusterByPeriod([
      at('medieval', [555, 655], '2026-08-20T00:00:00'),
      at('palaeo', [30000, 32000], '2026-08-19T00:00:00'),
    ])
    expect(clusters).toHaveLength(2)
    expect(names(clusters[0].items)).toEqual(['medieval'])
    expect(names(clusters[1].items)).toEqual(['palaeo'])
  })

  it('keeps an overlapping pair together however lopsided their widths', () => {
    // 5,000-year survey with a 20-year date inside it: ratio 250, but they
    // occupy the same stretch of calendar, so they share an axis.
    const { clusters } = clusterByPeriod([
      at('survey', [1000, 6000], '2026-08-20T00:00:00'),
      at('tight', [3000, 3020], '2026-08-19T00:00:00'),
    ])
    expect(clusters).toHaveLength(1)
    expect(clusters[0].span).toEqual([1000, 6000])
  })

  it('orders clusters and rows by recency, not by period', () => {
    const { clusters } = clusterByPeriod([
      at('old-but-newest', [30000, 32000], '2026-08-25T00:00:00'),
      at('recent-older-run', [555, 655], '2026-08-01T00:00:00'),
      at('recent-newer-run', [560, 650], '2026-08-10T00:00:00'),
    ])
    // The Palaeolithic cluster leads because its run is the most recent.
    expect(names(clusters[0].items)).toEqual(['old-but-newest'])
    // And within a cluster, newest first.
    expect(names(clusters[1].items)).toEqual(['recent-newer-run', 'recent-older-run'])
  })

  it('reports a wholly-AD cluster as current era, and anything older not', () => {
    const { clusters } = clusterByPeriod([at('ad', [145, 761], '2026-08-20T00:00:00')])
    expect(clusters[0].isCurrentEra).toBe(true)

    const { clusters: bc } = clusterByPeriod([at('bc', [2780, 3240], '2026-08-20T00:00:00')])
    expect(bc[0].isCurrentEra).toBe(false)
  })

  it('treats a cluster touching 1950 cal BP as reaching into BC', () => {
    // 1950 cal BP is 1 BC, not AD 1 -- there is no year zero, so a cluster
    // that reaches it can no longer be labelled AD throughout.
    const { clusters } = clusterByPeriod([at('edge', [1900, 1950], '2026-08-20T00:00:00')])
    expect(clusters[0].isCurrentEra).toBe(false)
  })

  it('sets unsolved analyses aside rather than dropping them', () => {
    const { clusters, unplaced } = clusterByPeriod([
      at('solved', [555, 655], '2026-08-20T00:00:00'),
      at('pending', null, '2026-08-21T00:00:00'),
    ])
    expect(names(clusters[0].items)).toEqual(['solved'])
    expect(names(unplaced)).toEqual(['pending'])
  })

  it('returns nothing to draw for an empty list', () => {
    expect(clusterByPeriod([])).toEqual({ clusters: [], unplaced: [] })
  })
})

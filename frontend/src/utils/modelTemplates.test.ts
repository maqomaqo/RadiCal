import { describe, expect, it } from 'vitest'
import type { MatrixMeasurement, ModelElement } from '@/types'
import { applyTemplate, describeSettings, templateSettings } from './modelTemplates'

const el = (
  id: string,
  element_type: string,
  name: string,
  children: ModelElement[] = []
): ModelElement => ({ id, element_type, name, tpq_age_bp: null, taq_age_bp: null, children }) as ModelElement

const date = (id: string, sample_name: string): MatrixMeasurement =>
  ({ id, sample_name, age_bp: 600, error_1sigma: 25 }) as MatrixMeasurement

const current = el('root', 'sequence', 'Draft', [
  el('p-a', 'phase', 'Layer 2'),
  el('p-b', 'phase', 'Pit fill'),
])

const template = el('t-root', 'sequence', 'Site', [
  el('t-b0', 'boundary', 'Start'),
  el('t-p3', 'phase', ' layer 3 '),
  el('t-b1', 'boundary', 'Transition'),
  el('t-p2', 'phase', 'LAYER 2'),
  el('t-b2', 'boundary', 'End'),
])

describe('applyTemplate', () => {
  it('places dates in the phase of the same name and leaves the rest unassigned', () => {
    const applied = applyTemplate(
      template,
      current,
      [date('d1', 'LA1'), date('d2', 'LA2'), date('d3', 'LA3')],
      { d1: 'p-a', d2: 'p-a', d3: 'p-b' }
    )
    expect(applied.placed).toBe(2)
    expect(applied.unplaced).toEqual(['LA3'])
    const layer2 = applied.tree.children![3]
    expect(applied.assignments).toEqual({ d1: layer2.id, d2: layer2.id })
  })

  it('gives the model ids of its own, leaving the template untouched', () => {
    const applied = applyTemplate(template, current, [], {})
    const ids: string[] = []
    const collect = (e: ModelElement) => {
      ids.push(e.id)
      e.children?.forEach(collect)
    }
    collect(applied.tree)
    expect(ids.some((id) => id.startsWith('t-'))).toBe(false)
    expect(new Set(ids).size).toBe(ids.length)
    expect(template.id).toBe('t-root')
  })

  it('falls back on the date’s own phase when the draft has no assignment for it', () => {
    const withElement = { ...date('d1', 'LA1'), element_id: 'p-a' }
    expect(applyTemplate(template, current, [withElement], {}).placed).toBe(1)
  })
})

describe('templateSettings', () => {
  it('keeps the settings a draft has and drops the rest', () => {
    expect(
      templateSettings({ solver: 'exact', resolution: 10, cross_check: false, dispersal_origin: null })
    ).toEqual({ solver: 'exact', resolution: 10 })
    expect(templateSettings(null)).toBeNull()
  })

  it('describes them in a line', () => {
    expect(describeSettings({ solver: 'exact', resolution: 10, estimate_durations: true })).toBe(
      'exact solver, 10-year resolution, durations'
    )
  })
})

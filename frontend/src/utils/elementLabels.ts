// What each model element is CALLED, in one place.
//
// A tree editor row, a Harris matrix node and a comparison table all fall back
// to the type when an element has no name -- which is one cleared text field
// away -- and without a shared map that fallback is the raw `element_type`.
// Three of the seven do not title-case into English: "Wiggle_Sequence" is not
// a word.
//
// A util rather than an export from the editor, because `utils/harrisLayout`
// and `utils/stratigraphy` need it and neither should depend on a component.
import type { ElementType, ModelElement } from '@/types'

export const ELEMENT_TYPE_LABEL: Record<ElementType, string> = {
  sequence: 'Sequence',
  phase: 'Phase',
  boundary: 'Boundary',
  hiatus: 'Hiatus',
  wiggle_sequence: 'Wiggle match',
  deposition_sequence: 'Deposition sequence',
  interpolated_depth: 'Undated level',
}

/** An element's name, or a readable default for its type. */
export function elementLabel(element: Pick<ModelElement, 'name' | 'element_type'>): string {
  return element.name || ELEMENT_TYPE_LABEL[element.element_type] || element.element_type
}

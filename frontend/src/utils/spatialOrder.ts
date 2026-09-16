/**
 * Applying a proposed spatial order to a model tree.
 *
 * Separate from the editor because it is a tree transformation with right and
 * wrong answers, and the wrong ones are quiet: an order applied to the wrong
 * positions, or one that drops an element, changes the model somebody is about
 * to run without changing anything they would notice on screen.
 *
 * WHAT IT DOES NOT DO. It does not rebuild the model. The proposed elements
 * are permuted AMONG THE POSITIONS THEY ALREADY OCCUPY, so every Boundary,
 * Hiatus and unproposed element stays exactly where the analyst put it. A
 * spanning tree is a claim about which sites are adjacent, not about how the
 * model should be structured, and rewriting the structure on the strength of
 * it would be applying far more than was proposed.
 */
import type { ModelElement } from '@/types'

/**
 * `tree` with the proposed elements permuted into `orderedIds`.
 *
 * Elements not named in the proposal are untouched, and so are the positions
 * themselves: if a sequence held [Boundary, A, Boundary, B, Boundary] and the
 * proposal is [B, A], it comes back [Boundary, B, Boundary, A, Boundary].
 *
 * Returns the original tree unchanged when there is nothing to do, so a caller
 * can compare by reference to tell whether accepting the proposal was a no-op.
 */
export function reorderToProposal(
  tree: ModelElement,
  orderedIds: string[]
): ModelElement {
  if (orderedIds.length < 2) return tree
  const rank = new Map(orderedIds.map((id, index) => [id, index]))
  let changed = false

  const walk = (element: ModelElement): ModelElement => {
    const children = element.children || []
    if (children.length === 0) return element

    const walked = children.map(walk)
    // Where in THIS element's child list the proposed elements sit. Only
    // these positions are written to.
    const slots: number[] = []
    for (let i = 0; i < walked.length; i += 1) {
      if (rank.has(walked[i].id)) slots.push(i)
    }

    if (slots.length > 1) {
      const wanted = slots
        .map((i) => walked[i])
        .sort((a, b) => (rank.get(a.id) as number) - (rank.get(b.id) as number))
      slots.forEach((slot, index) => {
        if (walked[slot].id !== wanted[index].id) changed = true
        walked[slot] = wanted[index]
      })
    }

    const same =
      walked.length === children.length &&
      walked.every((child, index) => child === children[index])
    return same ? element : { ...element, children: walked }
  }

  const next = walk(tree)
  return changed ? next : tree
}

/**
 * Whether a proposal would change anything.
 *
 * Worth asking before showing an accept button: a proposal that matches the
 * model already is a useful answer — the spatial order and the one the analyst
 * chose agree — and offering to "apply" it would suggest otherwise.
 */
export function proposalChangesAnything(
  tree: ModelElement,
  orderedIds: string[]
): boolean {
  return reorderToProposal(tree, orderedIds) !== tree
}

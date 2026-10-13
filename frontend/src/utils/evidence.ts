// Model evidence and the Bayes factors between runs.
//
// An exactly solved run with bounded outer Boundaries reports p(data | model)
// in its solver record (exact_chain._model_evidence). Two runs can be set
// against each other only if they explain the same readings; the backend
// leaves out the rest of what makes a comparison fair (the prior is each run's
// own, and is normalised), so what is checked here is that, and that every run
// has a figure.

export interface SolverEvidence {
  available: boolean
  reason?: string
  // Natural log and log10 of p(data | model).
  log?: number
  log10?: number
  // The prior's calendar range, cal BP.
  lower?: number
  upper?: number
  step?: number
  // Taken on a grid coarser than the figure deserves; see the backend.
  approximate?: boolean
  // Identifies the radiocarbon readings the model was fitted to.
  readings?: string
}

export interface EvidenceRun {
  name: string
  isBayesian: boolean
  method?: string | null
  evidence?: SolverEvidence | null
}

export interface EvidenceRow {
  name: string
  log10: number
  /** log10 of the Bayes factor of this run against the reference; null for the reference. */
  log10Factor: number | null
  strength: string | null
}

export type EvidenceComparison =
  | { kind: 'withheld'; reason: string }
  | { kind: 'ok'; rows: EvidenceRow[]; approximate: boolean }

const MINUS = '−'

/** A log10 evidence or factor as printed: two decimals, a true minus sign. */
export function formatLog10(value: number): string {
  const text = Math.abs(value).toFixed(2)
  return value < 0 && Number(text) !== 0 ? `${MINUS}${text}` : text
}

/**
 * Jeffreys' (1961) words for the size of a log10 Bayes factor, the scale
 * Kass and Raftery (1995) reproduce. Direction is carried by the sign; the
 * word is for its size.
 */
export function strengthOf(log10Factor: number): string {
  const size = Math.abs(log10Factor)
  if (size < 0.5) return 'barely worth mentioning'
  if (size < 1) return 'substantial'
  if (size < 1.5) return 'strong'
  if (size < 2) return 'very strong'
  return 'decisive'
}

/** The odds a log10 factor stands for, as "20 : 1" or, past a thousand, "10^3.4 : 1". */
export function oddsOf(log10Factor: number): string {
  const size = Math.abs(log10Factor)
  if (size >= 3) return `10^${size.toFixed(1)} : 1`
  const ratio = 10 ** size
  return `${ratio < 10 ? ratio.toFixed(1) : Math.round(ratio)} : 1`
}

/**
 * Evidence of each run against the first, or why they cannot be set against
 * each other. The first run is the reference, so its row carries no factor.
 */
export function compareEvidence(runs: EvidenceRun[]): EvidenceComparison {
  for (const run of runs) {
    if (!run.isBayesian) {
      return { kind: 'withheld', reason: `${run.name} is not a Bayesian analysis.` }
    }
    if (run.method !== 'exact') {
      return {
        kind: 'withheld',
        reason: `${run.name} was sampled, and only an exactly solved run has an evidence.`,
      }
    }
    if (!run.evidence) {
      return {
        kind: 'withheld',
        reason: `${run.name} was run before the evidence was recorded. Run it again.`,
      }
    }
    if (!run.evidence.available || typeof run.evidence.log10 !== 'number') {
      return {
        kind: 'withheld',
        reason: `${run.name}: ${run.evidence.reason ?? 'no evidence was computed'}.`,
      }
    }
  }
  const readings = new Set(runs.map((run) => run.evidence?.readings))
  if (readings.size > 1) {
    return {
      kind: 'withheld',
      reason:
        'The runs are not fitted to the same radiocarbon readings, so their evidence cannot be compared.',
    }
  }
  const reference = runs[0].evidence!.log10!
  return {
    kind: 'ok',
    approximate: runs.some((run) => run.evidence?.approximate),
    rows: runs.map((run, index) => {
      const log10 = run.evidence!.log10!
      const factor = index === 0 ? null : log10 - reference
      return {
        name: run.name,
        log10,
        log10Factor: factor,
        strength: factor === null ? null : strengthOf(factor),
      }
    }),
  }
}

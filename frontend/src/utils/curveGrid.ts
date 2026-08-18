/**
 * A calibration curve's calendar grid, said briefly.
 *
 * The backend reports it in full — "1 yr (0-5,000), 5 yr (5,000-15,000),
 * 10 yr (15,000-25,000), 20 yr (25,000-55,000)" — which is the right thing to
 * store and the right thing to show on the curve library page, where there is
 * a column for it. In a chip beside a row of controls it is 80 characters
 * that push everything else onto a second line, so this reduces it to the
 * range of steps and leaves the full text to a tooltip.
 */

/** "1 yr (0-5,000), 5 yr (…), 20 yr (…)" → "1–20 yr steps". */
export function compactGrid(resolution: string): string {
  // Two forms to read: a list of single steps ("1 yr (0-5,000), 5 yr (…)"),
  // and the range the backend uses for a curve whose grid is not regular
  // ("0.5-37 yr (irregular)") -- where both ends sit in one token, so a
  // pattern that only looked for a number before "yr" saw the 37 and lost the
  // 0.5.
  const steps = [...(resolution ?? '').matchAll(/([\d.]+)(?:\s*[-–]\s*([\d.]+))?\s*yr/g)]
    .flatMap((match) => [match[1], match[2]])
    .map(Number)
    .filter((value) => Number.isFinite(value))

  if (steps.length === 0) return resolution ?? ''

  const low = Math.min(...steps)
  const high = Math.max(...steps)
  return low === high ? `${low} yr steps` : `${low}–${high} yr steps`
}

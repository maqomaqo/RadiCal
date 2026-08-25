import { Box } from '@mui/material'
import { usePlotColors } from '@/theme/plotColors'

/** One sample's HPD geometry: disjoint [min, max] pairs in cal BP, plus its point estimate. */
export interface SampleIntervals {
  h68: [number, number][]
  h95: [number, number][]
  median: number | null
}

interface Props {
  intervals: SampleIntervals[]
  /** [min, max] cal BP of the axis this plot is drawn against -- handed down, never measured here. */
  axis: [number, number]
  /** Tick positions in cal BP, drawn as gridlines so a row lines up with its ruler. */
  ticks: number[]
  height?: number
  /** Announced instead of the marks, which carry no text of their own. */
  label: string
}

/**
 * Every sample in an analysis as one horizontal bar on a shared calendar
 * axis: the 95% HPD at low strength, the 68% nested inside it at high, and
 * the posterior median as an ink tick.
 *
 * Why bars and not a density curve. An analysis is usually many dates, and
 * the one-curve version of "many dates" is a summed probability
 * distribution -- a contested construct that invents structure the model
 * never asserted. Drawing one here would put a statistical claim on the
 * dashboard that the Results page would refuse to make.
 *
 * Why each bar is segments and not a span. A calibrated HPD is genuinely
 * disjoint wherever the curve plateaus; a single min-max bar would fill in
 * the gaps between peaks and so claim probability the calibration
 * explicitly withheld. This is the drawn form of the rule
 * utils/dateFormat.formatHPDRanges already follows in text.
 *
 * One hue at two strengths, not two hues: the two bands are one quantity at
 * two confidence levels, and they take the same Phase green the posterior
 * plots use for exactly this (see theme/plotColors), so a band means the
 * same thing here as it does there.
 */
export default function IntervalPlot({ intervals, axis, ticks, height = 64, label }: Props) {
  const colors = usePlotColors()
  const [axisMin, axisMax] = axis
  const width = axisMax - axisMin

  // Older cal BP sits left, so the axis runs high-to-low across the box.
  // Callers in BC/AD get the same geometry with different tick labels --
  // the marks never move when the reporting format changes.
  const pct = (calBP: number) => ((axisMax - calBP) / width) * 100

  // The row budget is spent, not requested: 63 samples get a 1px hairline
  // each and read as a block, 5 get a 9px bar each. A row that sized itself
  // would either overflow the card or cap the analysis at a few dates.
  const rowHeight = Math.min(10, height / Math.max(intervals.length, 1))
  const barHeight = Math.max(1, rowHeight - 1)
  const top = (height - rowHeight * intervals.length) / 2

  const segment = (
    [a, b]: [number, number],
    y: number,
    background: string,
    key: string
  ) => {
    const left = pct(Math.max(a, b))
    return (
      <Box
        key={key}
        sx={{
          position: 'absolute',
          borderRadius: '1px',
          left: `${left}%`,
          // A floor, because a plateau can leave a range only a few years
          // wide and a zero-width band is a range the reader cannot see
          // was reported at all.
          width: `${Math.max(pct(Math.min(a, b)) - left, 0.15)}%`,
          top: `${y}px`,
          height: `${barHeight}px`,
          backgroundColor: background,
        }}
      />
    )
  }

  return (
    <Box
      role="img"
      aria-label={label}
      sx={{ position: 'relative', height: `${height}px`, width: '100%' }}
    >
      {ticks.map((tick) => (
        <Box
          key={`grid-${tick}`}
          aria-hidden
          sx={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            width: '1px',
            left: `${pct(tick)}%`,
            backgroundColor: colors.grid,
          }}
        />
      ))}

      {intervals.map((sample, index) => {
        const y = top + index * rowHeight
        return (
          <Box component="span" key={index}>
            {sample.h95.map((range, i) => segment(range, y, colors.hpd95, `95-${index}-${i}`))}
            {sample.h68.map((range, i) => segment(range, y, colors.hpd68, `68-${index}-${i}`))}
            {sample.median !== null && (
              <Box
                sx={{
                  position: 'absolute',
                  left: `${pct(sample.median)}%`,
                  top: `${y}px`,
                  height: `${barHeight}px`,
                  width: '1px',
                  backgroundColor: colors.mean,
                }}
              />
            )}
          </Box>
        )
      })}
    </Box>
  )
}

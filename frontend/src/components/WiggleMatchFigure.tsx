// The figure a wiggle match is published with: the series hung on the curve.
//
// Every member's laboratory measurement is drawn at the calendar age the match
// puts it at -- the reference age plus its own gap -- over the calibration
// curve it was calibrated against. A good match is the whole series lying
// along the curve's wiggles at once; a member at the wrong ring sits off the
// curve by a visible distance, which is the agreement index made spatial.
//
// It replaces nothing on the page. The stacked plot below still draws each
// member's posterior, but those are one answer moved n times, and read without
// this figure they look like n independent confirmations. This is the one
// picture of a wiggle match in which the n points are genuinely n
// observations: n radiocarbon measurements, placed by one parameter.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Button, CircularProgress, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import DownloadIcon from '@mui/icons-material/Download'
import type { Config, Data, Layout } from 'plotly.js-dist-min'
import Plot from '@/components/Plot'
import { fetchCurvePoints, type CurvePoint } from '@/api/curves'
import { curveStyles } from '@/theme/curveColors'
import { useElementTypeColors } from '@/theme/diagramColors'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { calendarAxisTicks, calendarAxisTitle, formatCalendarAge, type DateFormat } from '@/utils/dateFormat'
import { POOR_AGREEMENT } from '@/utils/diagnostics'
import { downloadPlotPng } from '@/utils/downloadPlot'

export interface WiggleFigureMember {
  id: string
  name: string
  ringOffset: number
  ringOffsetError?: number | null
  agreementIndex?: number | null
  /** The laboratory measurement, radiocarbon years BP. */
  ageBp: number
  error1Sigma?: number | null
  /** Subtracted before plotting, so a marine measurement reads against its curve. */
  deltaR?: number | null
  curveId?: string | null
}

interface Props {
  members: WiggleFigureMember[]
  /** The reference age (gap 0), cal BP, and its HPD span at the page's level. */
  referenceMedian: number
  referenceSpan: [number, number] | null
  confidenceLabel: string
  gapUnits: string
  dateFormat: DateFormat
  exportName?: string
  height?: number
}

const CURVE_MAX_POINTS = 800

export default function WiggleMatchFigure({
  members,
  referenceMedian,
  referenceSpan,
  confidenceLabel,
  gapUnits,
  dateFormat,
  exportName = 'wiggle-match',
  height = 340,
}: Props) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const element = useElementTypeColors().wiggle_sequence
  const box = useRef<HTMLDivElement | null>(null)
  // Poor agreement is a genuine warning, so it takes the warning colour --
  // the same one the table beside this uses for the same fact.
  const warning = useTheme().palette.warning.main

  // Where each member lands on the calendar axis: the reference plus its gap.
  const placed = useMemo(
    () =>
      members.map((m) => ({
        ...m,
        calendar: referenceMedian + m.ringOffset,
        radiocarbon: m.ageBp - (m.deltaR ?? 0),
        poor: m.agreementIndex != null && m.agreementIndex < POOR_AGREEMENT,
      })),
    [members, referenceMedian]
  )

  // The window: the series plus a margin as wide as a third of it, so the
  // curve's shape either side of the match is visible -- which is what says
  // whether the match found the only place the series fits.
  const window = useMemo(() => {
    const cal = placed.map((p) => p.calendar)
    if (referenceSpan) cal.push(...referenceSpan)
    const lo = Math.min(...cal)
    const hi = Math.max(...cal)
    const pad = Math.max(30, (hi - lo) / 3)
    return { from: Math.max(0, Math.floor((lo - pad) / 10) * 10), to: Math.ceil((hi + pad) / 10) * 10 }
  }, [placed, referenceSpan])

  const curveId = placed.find((p) => p.curveId)?.curveId ?? null
  const [curve, setCurve] = useState<CurvePoint[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  useEffect(() => {
    if (!curveId) return
    let active = true
    setLoadError(null)
    fetchCurvePoints(curveId, { calFrom: window.from, calTo: window.to, maxPoints: CURVE_MAX_POINTS })
      .then((w) => active && setCurve(w.points))
      .catch((err) => active && setLoadError((err as Error).message || 'Could not load the curve.'))
    return () => {
      active = false
    }
  }, [curveId, window.from, window.to])

  const style = useMemo(() => (curveId ? curveStyles([curveId])[curveId] : null), [curveId])

  const data = useMemo((): Data[] => {
    const traces: Data[] = []

    // The reference's own range, as a band standing behind everything: where
    // gap 0 can be. Drawn in the wiggle match's hue at a wash, so it reads as
    // this model's answer rather than as a curve envelope.
    if (referenceSpan) {
      traces.push({
        type: 'scatter',
        mode: 'lines',
        x: [referenceSpan[0], referenceSpan[0], referenceSpan[1], referenceSpan[1]],
        y: [-1e6, 1e6, 1e6, -1e6],
        fill: 'toself',
        fillcolor: alpha(element.main, 0.12),
        line: { width: 0 },
        hoverinfo: 'skip',
        showlegend: false,
      })
    }

    if (curve && style) {
      const cal = curve.map((p) => p[0])
      const c14 = curve.map((p) => p[1])
      const sigma = curve.map((p) => p[2])
      const band = (k: number, color: string): Data => ({
        type: 'scatter',
        mode: 'lines',
        x: [...cal, ...cal.slice().reverse()],
        y: [...c14.map((v, i) => v + k * sigma[i]), ...c14.map((v, i) => v - k * sigma[i]).reverse()],
        fill: 'toself',
        fillcolor: color,
        line: { width: 0 },
        hoverinfo: 'skip',
        showlegend: false,
      })
      traces.push(band(2, style.band2), band(1, style.band1))
      traces.push({
        type: 'scatter',
        mode: 'lines',
        x: cal,
        y: c14,
        line: { color: style.line, width: 1.5, dash: style.dash },
        hoverinfo: 'skip',
        showlegend: false,
      })
    }

    const hover = placed.map(
      (p) =>
        `${p.name}<br>gap ${p.ringOffset}${p.ringOffsetError ? ` ± ${Math.round(p.ringOffsetError)}` : ''} ${gapUnits}` +
        `<br>${Math.round(p.ageBp)}${p.error1Sigma ? ` ± ${Math.round(p.error1Sigma)}` : ''} ¹⁴C BP` +
        `<br>placed at ${formatCalendarAge(p.calendar, dateFormat)}` +
        (p.agreementIndex != null ? `<br>agreement ${Math.round(p.agreementIndex)}%` : '')
    )

    // The measurements: a dot with its 1-sigma bar, and the gap's own
    // uncertainty as a horizontal bar where it was measured rather than
    // counted. Ink, the colour this app draws observations in.
    traces.push({
      type: 'scatter',
      mode: 'markers',
      x: placed.map((p) => p.calendar),
      y: placed.map((p) => p.radiocarbon),
      error_y: {
        type: 'data',
        array: placed.map((p) => p.error1Sigma ?? 0),
        color: plot.axis,
        thickness: 1.25,
        width: 3,
      },
      error_x: {
        type: 'data',
        array: placed.map((p) => p.ringOffsetError ?? 0),
        color: plot.axis,
        thickness: 1,
        width: 0,
      },
      marker: { size: 7, color: plot.mean, line: { color: plot.surface, width: 1 } },
      text: hover,
      hovertemplate: '%{text}<extra></extra>',
      showlegend: false,
    })

    // A ring round each member that fits poorly -- a mark as well as the
    // table's colour, and the one place the reader can SEE why: the dot sits
    // off the curve.
    const poor = placed.filter((p) => p.poor)
    if (poor.length > 0) {
      traces.push({
        type: 'scatter',
        mode: 'markers',
        x: poor.map((p) => p.calendar),
        y: poor.map((p) => p.radiocarbon),
        marker: { size: 18, symbol: 'circle-open', color: warning, line: { width: 2 } },
        hoverinfo: 'skip',
        showlegend: false,
      })
    }
    return traces
  }, [placed, curve, style, referenceSpan, element.main, plot, gapUnits, dateFormat, warning])

  const yRange = useMemo(() => {
    const ys: number[] = []
    placed.forEach((p) => {
      const e = p.error1Sigma ?? 0
      ys.push(p.radiocarbon - 2 * e, p.radiocarbon + 2 * e)
    })
    ;(curve ?? []).forEach(([, c14, s]) => ys.push(c14 - 2 * s, c14 + 2 * s))
    const lo = Math.min(...ys)
    const hi = Math.max(...ys)
    const pad = Math.max(10, (hi - lo) * 0.06)
    return [lo - pad, hi + pad] as [number, number]
  }, [placed, curve])

  const ticks = useMemo(
    () => calendarAxisTicks(window.from, window.to, dateFormat),
    [window, dateFormat]
  )

  const layout = useMemo(
    (): Partial<Layout> => ({
      dragmode: false as const,
      height,
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 64, r: 12, t: 24, b: 48 },
      hovermode: 'closest' as const,
      showlegend: false,
      xaxis: {
        title: { text: calendarAxisTitle(dateFormat), standoff: 8 },
        // Oldest on the left, as everywhere else in this app.
        range: [window.to, window.from],
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
        ...(ticks ? { tickmode: 'array' as const, tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Radiocarbon age (¹⁴C BP)' },
        range: yRange,
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
      },
      shapes: [
        {
          type: 'line',
          xref: 'x',
          yref: 'paper',
          x0: referenceMedian,
          x1: referenceMedian,
          y0: 0,
          y1: 1,
          line: { color: element.ink, width: 1.5 },
        },
      ],
      annotations: [
        {
          x: referenceMedian,
          y: 1,
          xref: 'x',
          yref: 'paper',
          yanchor: 'bottom',
          text: 'gap 0',
          showarrow: false,
          font: { ...plotFont, color: element.ink },
        },
      ],
    }),
    [height, plotFont, plot, dateFormat, window, ticks, yRange, referenceMedian, element.ink]
  )

  const config = useMemo(
    (): Partial<Config> => ({ responsive: true, displayModeBar: false as const, displaylogo: false }),
    []
  )

  return (
    <Box>
      <Box ref={box} role="img" aria-label={`The ${members.length} measurements of this series drawn on the calibration curve at the ages the match places them`}>
        <Plot
          data={data}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: '100%', height: `${height}px` }}
        />
      </Box>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mt: 0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flex: 1, maxWidth: '72ch' }}>
          Measurements (±1σ) placed at reference age + gap. Shaded: the
          reference's {confidenceLabel} range. Ringed: poor agreement.
          {loadError ? ` Curve unavailable: ${loadError}` : ''}
        </Typography>
        {!curve && !loadError && curveId && <CircularProgress size={16} />}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          aria-label="Download the wiggle match figure as PNG"
          onClick={() => downloadPlotPng(box.current, exportName)}
          sx={{ flexShrink: 0 }}
        >
          Download PNG
        </Button>
      </Box>
    </Box>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Typography } from '@mui/material'
import { useTheme } from '@mui/material/styles'
import {
  calToPixel,
  clampWindow,
  panByPixels,
  pixelToCal,
  windowRect,
  type Extent,
  type Window,
} from '@/utils/curveScrubber'
import { formatCalendarAge, type DateFormat } from '@/utils/dateFormat'

/**
 * The whole curve at a glance, with the part currently being examined
 * highlighted and draggable.
 *
 * This is the "scroll along the time axis" control, and it exists because
 * panning inside the main plot alone never tells you *where* in 55,000 years
 * you are. Drag the highlight to move; drag either edge to widen or narrow;
 * click anywhere to jump there.
 *
 * Drawn as an SVG rather than a second Plotly figure. It needs a drag that
 * moves a window rather than a drag that pans a chart, so a plotting library
 * would be something to work around rather than something to use — and the
 * curve here is sixty line segments, not a figure.
 */

const HEIGHT = 56
const HANDLE_GRAB_PX = 6

type DragMode = 'move' | 'from' | 'to'

interface Props {
  /** The whole curve, coarsely: [cal BP, 14C age] pairs. */
  overview: number[][]
  extent: Extent
  window: Window
  onChange: (next: Window) => void
  dateFormat: DateFormat
}

export default function CurveScrubber({ overview, extent, window: view, onChange, dateFormat }: Props) {
  const theme = useTheme()
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const drag = useRef<{ mode: DragMode; startX: number; startWindow: Window } | null>(null)

  // The strip is full-width and the window's pixel geometry depends on that
  // width, so it has to be measured rather than assumed -- and re-measured
  // when the page is resized.
  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
    })
    observer.observe(element)
    setWidth(element.clientWidth)
    return () => observer.disconnect()
  }, [])

  const rect = windowRect(view, extent, width)

  const positionOf = useCallback(
    (event: { clientX: number }) => {
      const bounds = containerRef.current?.getBoundingClientRect()
      return event.clientX - (bounds?.left ?? 0)
    },
    []
  )

  const onPointerDown = (event: React.PointerEvent) => {
    if (width <= 0) return
    const x = positionOf(event)
    const nearFrom = Math.abs(x - (rect.x + rect.width)) <= HANDLE_GRAB_PX
    const nearTo = Math.abs(x - rect.x) <= HANDLE_GRAB_PX
    const inside = x >= rect.x && x <= rect.x + rect.width

    let mode: DragMode = 'move'
    if (nearTo) mode = 'to'
    else if (nearFrom) mode = 'from'
    else if (!inside) {
      // A click outside the window centres it there, so getting to a distant
      // part of the curve is one gesture rather than a long drag.
      const centre = pixelToCal(x, extent, width)
      const half = (view.to - view.from) / 2
      onChange(clampWindow({ from: centre - half, to: centre + half }, extent))
      return
    }

    drag.current = { mode, startX: x, startWindow: view }
    ;(event.target as Element).setPointerCapture?.(event.pointerId)
  }

  const onPointerMove = (event: React.PointerEvent) => {
    const state = drag.current
    if (!state || width <= 0) return
    const x = positionOf(event)
    const delta = x - state.startX

    if (state.mode === 'move') {
      onChange(panByPixels(state.startWindow, extent, width, delta))
      return
    }
    // Dragging an edge moves that edge only. Which cal BP bound an edge is
    // depends on the axis running oldest-left: the left handle is the OLDER
    // bound.
    const cal = pixelToCal(x, extent, width)
    const next =
      state.mode === 'to'
        ? { from: state.startWindow.from, to: cal }
        : { from: cal, to: state.startWindow.to }
    onChange(clampWindow(next, extent))
  }

  const endDrag = () => {
    drag.current = null
  }

  // The curve itself, scaled to the strip.
  const c14s = overview.map((p) => p[1])
  const c14Min = Math.min(...c14s)
  const c14Max = Math.max(...c14s)
  const y = (c14: number) => HEIGHT - ((c14 - c14Min) / (c14Max - c14Min || 1)) * (HEIGHT - 6) - 3
  const path = overview
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${calToPixel(p[0], extent, width).toFixed(1)},${y(p[1]).toFixed(1)}`)
    .join(' ')

  return (
    <Box>
      <Box
        ref={containerRef}
        sx={{
          position: 'relative',
          height: HEIGHT,
          cursor: 'grab',
          touchAction: 'none',
          userSelect: 'none',
          backgroundColor: theme.palette.action.hover,
          borderRadius: 1,
          overflow: 'hidden',
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={endDrag}
      >
        {width > 0 && (
          <Box
            component="svg"
            width={width}
            height={HEIGHT}
            role="img"
            aria-label="The whole curve, with the part currently shown highlighted"
            sx={{ display: 'block' }}
          >
            <path d={path} fill="none" stroke={theme.palette.text.disabled} strokeWidth={1} />
            <rect
              x={rect.x}
              y={0}
              width={rect.width}
              height={HEIGHT}
              fill={theme.palette.primary.main}
              fillOpacity={0.18}
              stroke={theme.palette.primary.main}
              strokeWidth={1}
            />
            {/* Grab handles, drawn so the edges look draggable rather than
                being an invisible six-pixel secret. */}
            {[rect.x, rect.x + rect.width].map((x, i) => (
              <rect
                key={i}
                x={x - 1.5}
                y={HEIGHT / 2 - 10}
                width={3}
                height={20}
                rx={1.5}
                fill={theme.palette.primary.main}
              />
            ))}
          </Box>
        )}
      </Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.5 }}>
        <Typography variant="caption" color="textSecondary">
          {formatCalendarAge(extent.max, dateFormat)}
        </Typography>
        <Typography variant="caption" color="textSecondary">
          Drag to move, drag an edge to zoom, click to jump
        </Typography>
        <Typography variant="caption" color="textSecondary">
          {formatCalendarAge(extent.min, dateFormat)}
        </Typography>
      </Box>
    </Box>
  )
}

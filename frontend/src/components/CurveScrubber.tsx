import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

function CurveScrubber({ overview, extent, window: view, onChange, dateFormat }: Props) {
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

  /**
   * What clicking here would do, as a cursor.
   *
   * The strip does three different things depending on where the pointer is
   * -- move the window, resize an edge, or jump somewhere else -- and a
   * single grab hand for all three left the six-pixel edge handles as
   * something you found by accident. Written straight to the element's style
   * rather than held in state: this runs on every pointer move, and the chart
   * above re-renders from this component's parent.
   */
  const cursorAt = useCallback(
    (x: number) => {
      if (width <= 0) return 'grab'
      const nearFrom = Math.abs(x - (rect.x + rect.width)) <= HANDLE_GRAB_PX
      const nearTo = Math.abs(x - rect.x) <= HANDLE_GRAB_PX
      if (nearFrom || nearTo) return 'ew-resize'
      return x >= rect.x && x <= rect.x + rect.width ? 'grab' : 'pointer'
    },
    [rect.x, rect.width, width]
  )

  /**
   * The gesture in progress, on the element itself.
   *
   * The cursor becomes the closed hand or the resize arrows for as long as
   * the button is down, so the strip acknowledges the press rather than
   * looking the same held as hovered. `data-drag` carries the same fact to
   * the CSS below, which lifts the handle being dragged.
   */
  const showGesture = useCallback((mode: DragMode | null) => {
    const element = containerRef.current
    if (!element) return
    if (mode === null) {
      delete element.dataset.drag
      element.style.cursor = ''
      return
    }
    element.dataset.drag = mode
    element.style.cursor = mode === 'move' ? 'grabbing' : 'ew-resize'
  }, [])

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
    showGesture(mode)
    ;(event.target as Element).setPointerCapture?.(event.pointerId)
  }

  /**
   * The window a drag is asking for, reported once a frame.
   *
   * A pointer emits moves faster than the page redraws, and each one moves
   * the chart, its panels and the readout above -- and starts a refetch
   * timer. Coalescing to a frame drops only updates that would never have
   * been drawn, and is the difference between a drag that follows the pointer
   * and one that lags behind it.
   */
  const moveFrame = useRef<number | null>(null)
  const pending = useRef<Window | null>(null)
  const report = useCallback(
    (next: Window) => {
      pending.current = next
      if (moveFrame.current !== null) return
      moveFrame.current = requestAnimationFrame(() => {
        moveFrame.current = null
        if (pending.current) onChange(pending.current)
      })
    },
    [onChange]
  )
  useEffect(
    () => () => {
      if (moveFrame.current !== null) cancelAnimationFrame(moveFrame.current)
    },
    []
  )

  const onPointerMove = (event: React.PointerEvent) => {
    const state = drag.current
    if (!state) {
      // Not dragging: the cursor is a preview of what a press would start.
      if (containerRef.current) {
        containerRef.current.style.cursor = cursorAt(positionOf(event))
      }
      return
    }
    if (width <= 0) return
    const x = positionOf(event)
    const delta = x - state.startX

    if (state.mode === 'move') {
      report(panByPixels(state.startWindow, extent, width, delta))
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
    report(clampWindow(next, extent))
  }

  const endDrag = () => {
    drag.current = null
    showGesture(null)
  }

  /**
   * The curve itself, scaled to the strip.
   *
   * Rebuilt only when the curve or the strip's width changes, not when the
   * window moves: it is four hundred points of string-building, and the page
   * around this re-renders on every hover of the chart above.
   */
  const path = useMemo(() => {
    const c14s = overview.map((p) => p[1])
    const c14Min = Math.min(...c14s)
    const c14Max = Math.max(...c14s)
    const y = (c14: number) =>
      HEIGHT - ((c14 - c14Min) / (c14Max - c14Min || 1)) * (HEIGHT - 6) - 3
    return overview
      .map(
        (p, i) =>
          `${i === 0 ? 'M' : 'L'}${calToPixel(p[0], extent, width).toFixed(1)},${y(p[1]).toFixed(1)}`
      )
      .join(' ')
  }, [overview, extent, width])

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
          // The handles answer the pointer: they grow under it, and grow
          // further while actually being dragged. Six pixels of grabbable
          // edge is easy to miss, and a control that does not react to being
          // pressed reads as one that did not hear the press.
          '& .scrub-handle': {
            transformBox: 'fill-box',
            transformOrigin: 'center',
            transition: 'transform 120ms ease, fill-opacity 120ms ease',
          },
          '& .scrub-grip:hover + .scrub-handle': {
            transform: 'scale(1.7, 1.25)',
          },
          '&[data-drag="from"] .scrub-handle-from, &[data-drag="to"] .scrub-handle-to': {
            transform: 'scale(2, 1.35)',
          },
          // The window itself firms up while it is being moved, which is the
          // only feedback there is when the whole strip is one flat colour.
          '& .scrub-window': { transition: 'fill-opacity 120ms ease' },
          '&[data-drag="move"] .scrub-window': { fillOpacity: 0.3 },
          '@media (prefers-reduced-motion: reduce)': {
            '& .scrub-handle, & .scrub-window': { transition: 'none' },
          },
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
              className="scrub-window"
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
                being an invisible six-pixel secret. Each is a transparent
                grip the width of the area onPointerDown treats as that edge,
                with the visible bar after it so CSS can lift the bar while
                the grip is hovered. */}
            {([
              ['to', rect.x],
              ['from', rect.x + rect.width],
            ] as const).map(([edge, x]) => (
              <g key={edge}>
                <rect
                  className="scrub-grip"
                  x={x - HANDLE_GRAB_PX}
                  y={0}
                  width={HANDLE_GRAB_PX * 2}
                  height={HEIGHT}
                  fill="transparent"
                  style={{ cursor: 'ew-resize' }}
                />
                <rect
                  className={`scrub-handle scrub-handle-${edge}`}
                  x={x - 1.5}
                  y={HEIGHT / 2 - 10}
                  width={3}
                  height={20}
                  rx={1.5}
                  fill={theme.palette.primary.main}
                  style={{ pointerEvents: 'none' }}
                />
              </g>
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

/**
 * Memoised: this sits under a chart that reports hover, so its parent
 * re-renders as the pointer moves over the curve while nothing here changes.
 */
export default memo(CurveScrubber)

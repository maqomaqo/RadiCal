import type { ForwardRefExoticComponent, RefAttributes } from 'react'
import Plotly from 'plotly.js-dist-min'
import type {
  Config,
  Data,
  Layout,
  PlotHoverEvent,
  PlotMouseEvent,
  PlotRelayoutEvent,
} from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import type { PlotParams } from 'react-plotly.js/factory'

/**
 * The props every figure in the app passes, typed against plotly.js's own
 * declarations.
 *
 * react-plotly.js types data, layout and config as `unknown` -- it does not
 * depend on plotly's types -- so a figure built with it is checked for
 * nothing: a misspelt attribute, a string where Plotly 3 wanted `{ text }`,
 * all compile. Narrowed here to plotly's Data, Layout and Config, and to the
 * event payloads the app actually handles, so the compiler reads a figure the
 * way Plotly will.
 */
export interface FigureProps
  extends Omit<PlotParams, 'data' | 'layout' | 'config' | 'onClick' | 'onHover' | 'onUnhover' | 'onRelayout'> {
  data: Data[]
  layout?: Partial<Layout>
  config?: Partial<Config>
  onClick?: (event: PlotMouseEvent) => void
  onHover?: (event: PlotHoverEvent) => void
  onUnhover?: (event: PlotMouseEvent) => void
  onRelayout?: (event: PlotRelayoutEvent) => void
}

/**
 * One Plot component for the app, bound to the minified bundle rather than
 * react-plotly's default (which would pull in the full plotly.js as well).
 */
const Plot = createPlotlyComponent(Plotly) as ForwardRefExoticComponent<
  FigureProps & RefAttributes<HTMLDivElement>
>

export default Plot

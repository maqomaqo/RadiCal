// Neither plotly.js-dist-min nor react-plotly.js ships its own type
// declarations. Rather than pull in the separately-maintained (and not
// always in sync) @types/plotly.js / @types/react-plotly.js packages for
// what's a single narrowly-used component, declare just enough of a
// shape here: PosteriorPlot.tsx already treats trace/layout/config data
// as loosely-typed objects (matching Plotly's own untyped JS API), so
// there's nothing more precise to gain from a fuller declaration.
declare module 'plotly.js-dist-min' {
  const Plotly: any
  export default Plotly
}

declare module 'react-plotly.js/factory' {
  import type { ComponentType } from 'react'
  export default function createPlotlyComponent(plotly: any): ComponentType<any>
}

# The offline basemap

`world_110m.json` is the coastline, country and lake geometry the map draws
when a project has not turned basemap tiles on. It is served from here and
read through Plotly's `topojsonURL` config option — see
`src/components/SiteMap.tsx`, which sets `TOPOJSON_URL`.

**It is vendored because Plotly does not ship it.** `plotly.js-dist-min`
contains the `scattergeo` *renderer* but none of the geometry: left to itself
it fetches `https://cdn.plot.ly/world_110m.json` at render time, and when that
request cannot be made — an air-gapped install, a firewalled network — the
plot does not degrade, it rejects, and no map appears at all. Both of those
are unacceptable here for the reason in
`docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md` §4.5: the offline map is the
DEFAULT precisely so that looking at a site discloses nothing, and a default
that silently phones a CDN is not that.

## Provenance

Natural Earth 110m, public domain, as packaged by Plotly (MIT). This copy was
extracted from `plotly.js@3.7.0`'s `dist/plotly-geo-assets.js`, which is in
the dependency tree already, and renders correctly under the 2.35.3 bundle the
app actually loads — the topojson format has not changed between the two. The
version-matched original at `cdn.plot.ly` could not be fetched to diff against
it from the environment this was built in.

## Refreshing it

Only needed if the geometry is wrong or a higher resolution is wanted
(`world_50m.json` is the same objects at 1.6 MB — six times the size for
detail no figure in this app is drawn at). From the `frontend` directory:

```
node -e "
const fs=require('fs'), vm=require('vm');
const src=fs.readFileSync('node_modules/plotly.js/dist/plotly-geo-assets.js','utf8');
const ctx={window:{}}; vm.createContext(ctx); ctx.self=ctx; ctx.root=ctx;
vm.runInContext(src, ctx);
const t=(ctx.window.Plotly||ctx.moduleName).topojson;
fs.writeFileSync('public/geo/world_110m.json', JSON.stringify(t.world_110m));
"
```

The file must keep its name: Plotly builds the request as
`\${topojsonURL}\${scope}_\${resolution}m.json`, so `scope: 'world'` at the
default resolution asks for exactly `world_110m.json`.

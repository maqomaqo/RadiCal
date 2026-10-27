# The offline basemap

`world_50m.json` is the coastline, country and lake geometry the map draws
when a project has not turned basemap tiles on. It is served from here and
read through Plotly's `topojsonURL` config option — see
`src/components/SiteMap.tsx`, which sets `TOPOJSON_URL` and asks for the
50m resolution explicitly (`geo.resolution`), since Plotly's default is 110m.

**It is vendored because Plotly does not ship it.** `plotly.js-dist-min`
contains the `scattergeo` *renderer* but none of the geometry: left to itself
it fetches `https://cdn.plot.ly/world_50m.json` at render time, and when that
request cannot be made — an air-gapped install, a firewalled network — the
plot does not degrade, it rejects, and no map appears at all. Both of those
are unacceptable here: the offline map is the
DEFAULT precisely so that looking at a site discloses nothing, and a default
that silently phones a CDN is not that.

## Provenance

Natural Earth 50m, public domain, as packaged by Plotly (MIT). This copy was
extracted from `plotly.js@3.7.0`'s `dist/plotly-geo-assets.js`, which is in
the dependency tree already, and renders correctly under the 2.35.3 bundle the
app actually loads — the topojson format has not changed between the two. The
version-matched original at `cdn.plot.ly` could not be fetched to diff against
it from the environment this was built in.

## Refreshing it

Only needed if the geometry is wrong. 50m rather than the 110m Plotly asks
for by default because the map's tightest window is six degrees across, and
at that scale 110m draws a coastline as straight segments and an island as a
polygon; 50m is six times the size (1.7 MB against 285 kB) and holds up
there. From the `frontend` directory:

```
node -e "
const fs=require('fs'), vm=require('vm');
const src=fs.readFileSync('node_modules/plotly.js/dist/plotly-geo-assets.js','utf8');
const ctx={window:{}}; vm.createContext(ctx); ctx.self=ctx; ctx.root=ctx;
vm.runInContext(src, ctx);
const t=(ctx.window.Plotly||ctx.moduleName).topojson;
fs.writeFileSync('public/geo/world_50m.json', JSON.stringify(t.world_50m));
"
```
node -e "
const fs=require('fs'), vm=require('vm');
const src=fs.readFileSync('node_modules/plotly.js/dist/plotly-geo-assets.js','utf8');
const ctx={window:{}}; vm.createContext(ctx); ctx.self=ctx; ctx.root=ctx;
vm.runInContext(src, ctx);
const t=(ctx.window.Plotly||ctx.moduleName).topojson;
fs.writeFileSync('public/geo/world_50m.json', JSON.stringify(t.world_50m));
"
```

The file must keep its name: Plotly builds the request as
`\${topojsonURL}\${scope}_\${resolution}m.json`, so `scope: 'world'` at
`resolution: 50` asks for exactly `world_50m.json`.

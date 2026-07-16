# Custom imagery: design options

Follow-up to the questions raised while building the multi-source combo feature: can
we get GOES imagery without NOAA's baked-in state lines / "fake" synthetic city
lights, and can we add our own overlays (real state lines, city labels, storm tracks,
surface analysis charts)? This is scoped as a separate initiative from everything in
`NEXT_STEPS.md` — a bigger architectural change, not an incremental feature.

## Recap: why today's approach can't do either

`goes_wallpaper.pyw` fetches NOAA STAR's *already-rendered* GeoColor/band JPGs from
`cdn.star.nesdis.noaa.gov`. Checked directly against GEOCOLOR, Band 02, and Band 13
for CONUS: state/country border lines are baked into the pixels on every product, not
confined to an edge, so no crop can remove them. The synthetic VIIRS night-lights
compositing ("fake city lights") is GEOCOLOR-specific. Since we only ever receive a
flat JPG with no georeferencing information, we also can't accurately overlay our own
georeferenced content (lines, labels, charts) on top — we don't know which pixel
corresponds to which lat/lon.

## Three options

### A. Georeference + reprojection on NOAA's rendered JPG (`lanceberc/GOES` technique)

Keep fetching NOAA's rendered JPG (no change to the source), but add a post-hoc
georeferencing step: construct a GEOS-projection WKT + GeoTransform from documented
sector offset/resolution constants (no raw satellite files needed), reproject with
GDAL (`gdal.Warp`) into a standard projection, then alpha-composite our own
georeferenced content on top (state lines from Natural Earth shapefiles, city labels,
storm tracks, or NOAA's own surface analysis charts the way `lanceberc/GOES` does).

- **Solves**: custom overlays.
- **Does not solve**: NOAA's existing baked-in state lines/logo/fake city lights are
  still there in the base image — this only adds new content on top, it can't remove
  what's already rendered into the pixels.
- **Cost**: adds GDAL, one of the more painful Windows dependencies — the `GDAL`
  PyPI package needs a compiled wheel matching both your Python version and a matching
  PROJ/GEOS version; most Windows users end up on conda or OSGeo4W rather than a plain
  `pip`/`uv` install. Otherwise cheap: no new network/compute cost per cycle, still
  just one small JPG fetch.

### B. Composite our own GeoColor from raw ABI data (satpy)

Fetch raw Level 1b radiance bands from the public `noaa-goes18`/`noaa-goes19` S3
buckets (anonymous access, no auth needed) and composite them ourselves with
[satpy](https://satpy.readthedocs.io/), which has a built-in `geo_color` composite
(rayleigh correction, day/night blending — not a from-scratch reimplementation of the
Miller et al. 2020 algorithm) plus proper reprojection via `pyresample`.

- **Solves**: both. The output has no NOAA annotations at all (there's nothing to
  remove — we're building the image ourselves), and since satpy gives us the area
  definition/projection directly, overlaying our own content is straightforward
  (`satpy.writers.add_overlay`/`add_decorate`, or plain `cartopy`).
- **City lights**: GEOCOLOR's synthetic night-lights come from a static VIIRS DNB
  climatology NOAA uses internally, which isn't just sitting in the ABI S3 buckets —
  it'd need sourcing as a separate ancillary dataset. Given the "fake" framing that
  motivated this in the first place, the simplest option is to just not implement
  that part: night side renders as plain IR/no-lights, which is arguably more honest.
- **Cost**: the heaviest option. New dependencies: `satpy`, `pyresample`, `xarray`,
  `dask`, `pyproj`, `trollimage`, `s3fs` (or `boto3`) — but unlike GDAL, these mostly
  ship Windows wheels and install cleanly via `uv add`. Per-cycle cost jumps
  meaningfully: raw CONUS band files run low tens of MB each (vs. ~2MB for today's
  single JPG), GeoColor needs at least 3 bands (blue/red/veggie) plus band 13 for
  night, and reprojection/compositing adds real CPU time per cycle — worth checking
  this stays comfortable at a 5-minute loop cadence before committing.

### C. Hybrid

Add raw-data compositing (B) as an alternative source, with automatic fallback to
today's CDN-JPG fetch if raw data isn't available (satellite offline, S3 access
issue, etc. — directly relevant given GOES-19 was actually offline during this
session). Both paths would feed the same downstream pipeline (crop/info-block/EXIF/
wallpaper-apply/combos), so most of `goes_wallpaper.pyw` stays unchanged regardless of
which source produced the base image. Overlays (A's approach or satpy's own) would
layer on top of either source, though only meaningfully useful on the raw-composited
path since the CDN-JPG path has no georeferencing.

## Recommendation

The original ask was specifically about removing NOAA's baked-in state lines and fake
city lights — only **B** actually does that; **A** is additive-only and leaves the
underlying problem in place. If custom overlays turn out to be the primary interest
and removing NOAA's annotations is negotiable, A is far cheaper and could ship
quickly. If removing them is the actual goal, B is required and A isn't a meaningful
stepping stone toward it (different technique entirely, no shared code).

Suggested phasing if going with B:
1. Raw-data fetch + satpy `geo_color` composite as a new source path, producing a
   plain `PIL.Image` + capture-time metadata — designed to plug into the *existing*
   `fetch_and_render`/crop/info-block/EXIF/combo pipeline with minimal changes there,
   rather than a parallel implementation.
2. Verify bandwidth/compute cost is acceptable at the configured loop interval before
   making it the default.
3. Custom overlays (state lines, labels) on top, once the base composite is working —
   satpy exposes the area definition directly, so this doesn't need GDAL/option A's
   technique at all.
4. Optional: B/A hybrid fallback per option C, if raw-source outages (like this
   session's GOES-19 downtime) turn out to be common enough to matter.

## Open questions before implementation starts

- Is removing NOAA's state lines/city lights still the goal, or has the ask shifted
  toward "add overlays" as the primary interest (changes A vs. B)?
- Which satellite(s)/sector(s) need this — just CONUS, or Full Disk/Mesoscale too
  (affects band file sizes and satpy area-definition setup)?
- Acceptable per-cycle bandwidth/compute budget, given this runs on a `--loop`/Task
  Scheduler cadence rather than on demand?
- Should this replace the CDN-JPG path entirely, or stay opt-in alongside it (e.g. a
  new `combo` source type)?

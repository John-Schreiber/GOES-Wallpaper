# Custom imagery: design options

**Status: Option B's first cut has landed and was verified against live data**
(`source_kind = "satpy_raw"` — see `source_satpy.py`, README.md's "Custom raw-data
source (satpy_raw)" section). What's done: raw ABI L1b fetch from the public S3
buckets, satpy compositing, wired into the existing crop/info-block/EXIF/combo
pipeline as an opt-in alternative to the default `cdn_jpg` source (not a
replacement), plus georeferenced overlays generalized to work off the real
per-frame area info (so Full Disk/Mesoscale work too, not just CONUS). Confirmed
end to end with a live GOES-18 CONUS fetch: real S3 bucket listing/download, a real
composite, and the full crop/info-block/EXIF pipeline producing a correct final
image with no NOAA annotations.

Three things found only by actually running it, not by reading satpy's docs:

- satpy's stock `geo_color` composite's night-blend layer depends on an external
  NASA-hosted Black Marble file that currently 404s. Rather than lean on that (an
  external outage, and a static/non-real-time image regardless), `source_satpy.py`
  builds its own day/night blend: `true_color` by day, a custom muted navy-to-pale
  color mapped from Band 13 (clean IR window) brightness temperature by night,
  blended at the terminator by real per-pixel solar zenith angle
  (`satpy.modifiers.angles.get_cos_sza`) — see `_composite_true_color_with_muted_ir_night`
  in `source_satpy.py`. **Verified against real nighttime pixels**: a live Full
  Disk fetch (which always spans both hemispheres) showed a smooth, correctly-toned
  transition from daylit true_color cloud tops into the muted navy/pale-lavender
  night palette at the real terminator, cloud structure still visible/recognizable
  across it — genuinely photographic-feeling rather than a harsh false-color IR
  product, matching the original ask.
- `satpy[abi_l1b]` alone isn't sufficient to actually run any composite — `h5py`,
  `pyspectral`, `rasterio`, and `rioxarray` are all needed too (now in the
  `satpy-raw` optional-dependencies group) despite not being pulled in automatically.
- Compositing at each band's true native resolution (satpy's "native" resampler
  defaults to upsampling every band to the finest loaded band's grid) is expensive
  enough to matter, especially for Full Disk: one live Full Disk (GOES-18) fetch at
  native resolution downloaded fine (~553MB for the four required bands, Band 2
  alone ~405MB at native 10848×10848) but didn't finish *compositing* within a
  5-minute budget. Since the output gets downsized to screen resolution by
  `crop_to_screen` anyway, compositing at full native resolution first is wasted
  work — `source_satpy.py` now resamples down to `_COMPOSITE_TARGET_WIDTH_PX`
  (5000px, matching `Config.resolution`'s own default rationale) *before*
  compositing, snapped to the nearest power-of-2 downscale factor (ABI's own band
  resolutions are power-of-2 multiples of each other, so this keeps every band's
  individual resample ratio an exact integer — satpy's native resampler rejects
  non-integer aggregation factors, found by hitting that error on a live Full Disk
  attempt). Result: CONUS dropped from ~55-70s to ~22s per composite, and Full Disk
  went from not finishing in 5 minutes to ~45s.

**Bandwidth/compute cost is real — budget for it.** Even with the resampling fix
above, `satpy_raw` downloads and processes the full set of raw band files every
cycle (no cross-cycle caching in v1): a live GOES-18 CONUS fetch measured
**~98MB** for the four required bands (now logged every fetch — see
`fetch_composite`'s "Downloaded %d bytes" line), and Full Disk is considerably more
(Band 2 alone was ~405MB natively in one live fetch). The four native band files,
before any downsampling, still have to be downloaded in full — only the
*compositing* step got cheaper, not the download. This is a fundamentally heavier
source than `cdn_jpg`'s single small JPG fetch (~2-9MB observed for CONUS in this
session); think carefully before enabling `satpy_raw` on a tight `--loop` interval,
especially for Full Disk, especially on a metered/limited
connection. See README's "Custom raw-data source (satpy_raw)" section.

**TODO: make `_COMPOSITE_TARGET_WIDTH_PX` dynamic instead of a fixed constant** —
derive it from the actual configured screen size (with some headroom factor)
instead of a fixed 5000px, the same way `cdn_jpg`'s `resolution` tier selection
could in principle be screen-aware. Would need threading `screen_size` (currently
only known in `goes_wallpaper.py`'s `fetch_and_render`) down into
`source_satpy.fetch_composite`, and deciding how much headroom to keep for
`source_crop_*`/combo `crop_*` users who intentionally crop into a sub-region
before the screen-fit crop. (Also noted inline at `_COMPOSITE_TARGET_WIDTH_PX`'s
definition in `source_satpy.py`.)

**Backlog: real VIIRS Day/Night Band city lights.** The muted-IR night composite
above is deliberately not trying to show city lights — Suomi NPP/NOAA-20/21's
VIIRS DNB is a genuine low-light sensor (unlike ABI's visible bands, which need
sunlight and go fully dark at night) that captures real, current moonlit clouds and
city lights, also freely available on public S3. Would be a real upgrade over both
the muted-IR approach and GEOCOLOR's static Black-Marble compositing — but a
materially bigger scope: a different satellite/reader/bucket, polar orbit instead
of geostationary (so it's not continuously available for a given spot the way GOES
is — more like one or two passes a night), and reprojection to align with the ABI
GEOS grid.

What's still open, matching the "Suggested phasing" below: phase 2's bandwidth/
compute cost at a *sustained* `--loop` cadence (single-cycle numbers above are
promising post-resampling-fix — CONUS ~22s, Full Disk ~45s composite time — but
repeated cycles, warm-cache behavior, and Full Disk's meaningfully larger download
haven't been checked); and phase 4 (the B/A hybrid fallback, Option C, still
deferred — no automatic fallback to `cdn_jpg` if a raw fetch fails). The rest of
this document is the original design doc, kept as-is for the
reasoning/alternatives-considered record.

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

(Historical — these were resolved before the first cut above: goal stayed B/removing
NOAA's annotations; scope became CONUS + Full Disk + Mesoscale; opt-in alongside
`cdn_jpg`, not a replacement. Kept for the record, same as the rest of this doc below
the status block.)

- Is removing NOAA's state lines/city lights still the goal, or has the ask shifted
  toward "add overlays" as the primary interest (changes A vs. B)?
- Which satellite(s)/sector(s) need this — just CONUS, or Full Disk/Mesoscale too
  (affects band file sizes and satpy area-definition setup)?
- Acceptable per-cycle bandwidth/compute budget, given this runs on a `--loop`/Task
  Scheduler cadence rather than on demand?
- Should this replace the CDN-JPG path entirely, or stay opt-in alongside it (e.g. a
  new `combo` source type)?

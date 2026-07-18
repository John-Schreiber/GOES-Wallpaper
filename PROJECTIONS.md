# Output projection gallery

Example renders of every `output_projection` value, all from the same live
GOES-18 CONUS GEOCOLOR frame — the differences below are purely the
projection, not the source data (the day/night terminator visible in some is
real). See [README.md](README.md#output-projection) for the full config
reference.

## `"native"` (default)

No reprojection — the satellite's own GEOS view, exactly as `cdn_jpg`/
`satpy_raw` produce it.

![native](docs/projections/native.jpg)

## `"platecarree"` — equirectangular

Framed by `source_crop_min_lon/min_lat/max_lon/max_lat` (chosen here to stay
inside the CONUS frame's actual coverage — a box reaching past its edges comes
back with black "no data" margins). Simple mapping, but visibly stretches
north-south distances at this latitude.

```toml
output_projection = "platecarree"
source_crop_min_lon = -124.0
source_crop_min_lat = 32.0
source_crop_max_lon = -108.0
source_crop_max_lat = 46.0
```

![platecarree](docs/projections/platecarree.jpg)

## `"lambertconformal"` — conformal conic

Same bounds as above. This is what NWS/NOAA's own CONUS maps use — standard
parallels default to 1/6 and 5/6 up the box's latitude range, giving
negligible shape distortion at CONUS scale. Looks close to `platecarree` here;
the difference grows with the box's latitude span and matters more once
you're comparing distances/areas rather than eyeballing coastlines.

```toml
output_projection = "lambertconformal"
source_crop_min_lon = -124.0
source_crop_min_lat = 32.0
source_crop_max_lon = -108.0
source_crop_max_lat = 46.0
# output_projection_lcc_lat1 = 33.0  # optional, defaults to the 1/6 rule above
# output_projection_lcc_lat2 = 45.0
```

![lambertconformal](docs/projections/lambertconformal.jpg)

## `"orthographic"` — globe view

Centered on `output_projection_center_lon/_center_lat` (here, off the
California coast) — a view as seen from space. Black outside the visible
hemisphere (that's space, not a bug); note the barrel-distorted edges of the
CONUS content and the day/night terminator crossing the frame. The output
canvas is square with mostly black margin (only CONUS was fetched, not a full
disk image) — cropped in tight here for legibility; `crop_to_screen` handles
fitting the real output to your screen the same way it does for `"native"`.

```toml
output_projection = "orthographic"
output_projection_center_lon = -115.0
output_projection_center_lat = 37.0
```

![orthographic](docs/projections/orthographic.jpg)

## `"lambertazimuthal"` — equal-area azimuthal

Same center and canvas size as `orthographic`, but spanning the *whole* globe
(out to the antipode) instead of just the visible hemisphere — so the same
CONUS content occupies a visibly smaller fraction of the frame. Useful for
seeing more of the globe than `orthographic` can show; less "photographic"
looking as a result.

```toml
output_projection = "lambertazimuthal"
output_projection_center_lon = -115.0
output_projection_center_lat = 37.0
```

![lambertazimuthal](docs/projections/lambertazimuthal.jpg)

## Picking one

- Framing a region and want it to look geometrically correct: **`lambertconformal`**.
- Simplest possible mapping (e.g. feeding other lon/lat-grid tooling): **`platecarree`**.
- A wallpaper that looks like a real view of Earth from space: **`orthographic`**.
- As much of the globe as possible from one center point: **`lambertazimuthal`**.
- Don't care, or want the imagery exactly as delivered: **`native`**.

All four non-native projections work identically for `cdn_jpg` (CONUS/Full
Disk only) and `satpy_raw` (any sector) — see `reproject_frame` in
`goes_wallpaper.py`.

## Known quality limitations

`reproject_frame` is pure nearest-neighbor resampling (`pyproj`/`numpy` only,
no `pyresample`/`satpy` dependency) — cheap and dependency-free, but visibly
rougher than a real resampling library in two ways:

* **Jagged valid-data edges.** Look closely at the `orthographic`/
  `lambertazimuthal` renders above — the boundary between real content and
  the black margin is stair-stepped, with no anti-aliasing.
* **Overlays get warped, not redrawn.** `overlays.toml`'s graticule/GeoJSON
  sources (see [OVERLAYS.md](OVERLAYS.md)) draw onto the source image's
  native pixel grid *before* reprojection runs, so their pixels get dragged
  through the same nearest-neighbor warp as everything else instead of being
  reprojected as geometry. Thin lines can break into dashed segments; markers
  can distort; text can shear — worst near the projection's edges.
  `lambertconformal`/`platecarree` over a CONUS-sized box barely show this;
  `orthographic`/`lambertazimuthal` show it the most.

Both are tracked as follow-ups in `NEXT_STEPS.md` rather than fixed here —
the cheapest likely fix is supersampling (render larger, downsample with
antialiasing); the thorough fix is reprojecting overlay geometry directly
instead of warping already-drawn pixels.

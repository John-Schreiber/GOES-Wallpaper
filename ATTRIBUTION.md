# Attribution

## Origin

This project exists because Paul H (`pjlhjr@gmail.com`) built
[pjlhjr/GOES-Wallpaper](https://github.com/pjlhjr/GOES-Wallpaper) and generously
shared it under the Apache License, Version 2.0 — the idea of turning NOAA's
public satellite feed into a live desktop wallpaper was his, not ours. The
codebase has since grown a lot (see [CHANGELOG.md](CHANGELOG.md)), but none of
it would exist without his original work to start from. Thank you, Paul.

This project (the code as it exists now) is licensed under the GNU General Public
License v3.0-or-later — see [LICENSE](LICENSE). Gratefully, and in keeping with the
original Apache License 2.0's terms, its notice is preserved below for the portions of
the project's heritage it covers.

<details>
<summary>Original Apache License 2.0 notice (pjlhjr/GOES-Wallpaper)</summary>

```
Copyright [yyyy] [name of copyright owner]

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

The original repository's `LICENSE` file (commit `d256923`, 2020-06-10) left the
`[yyyy] [name of copyright owner]` placeholder from the Apache-2.0 template
unfilled — reproduced here exactly as committed.

</details>

## Techniques

- **Georeferenced overlays** (`OVERLAYS.md`) owe a real debt to
  [lanceberc/GOES](https://github.com/lanceberc/GOES) — its write-up of
  georeferencing NOAA's rendered GEOS-projection imagery from documented
  sector offset/resolution constants, without needing raw satellite files, was
  exactly the idea this feature needed. This project's implementation was
  written independently on top of `pyproj` and calibrated/validated from
  scratch against real ABI L1b data and known city landmarks (see
  `CUSTOM_IMAGERY_PLAN.md`), but the technique came from seeing it done there
  first. Thanks for publishing it.

## Data source

- Satellite imagery comes from [NOAA STAR](https://www.star.nesdis.noaa.gov/)'s
  public CDN (`cdn.star.nesdis.noaa.gov`) and the public `noaa-goes16`/
  `noaa-goes18`/`noaa-goes19` AWS S3 buckets — freely available thanks to
  NOAA's open-data policy. Thanks to NOAA/NESDIS for keeping taxpayer-funded
  satellite data free and open.

## Assets

- **Bundled point icons** (`icon`/`properties.icon`/`properties.marker-symbol`
  on `geojson_sources`/`shell_sources`, see `OVERLAYS.md`) are rasterized from
  [Mapbox's Maki icon set](https://github.com/mapbox/maki) (commit
  `28e2a3602e4bde033a1dd388e86e9c117b425e34`, 2025-06-23), vendored in full
  under `vendor/maki/` (SVG source + its own `LICENSE.txt`) and converted to
  the PNGs actually shipped/read at runtime (`overlays/icons/`) by
  `scripts/rasterize_maki_icons.py`. Licensed **CC0-1.0** (public domain) — no
  attribution is legally required, credited here anyway in keeping with this
  file's practice of documenting where things came from. See
  `vendor/maki/README.md` for re-vendoring/re-rendering instructions.

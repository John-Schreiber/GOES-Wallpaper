# Attribution

## Origin

This project began as a local clone of
[pjlhjr/GOES-Wallpaper](https://github.com/pjlhjr/GOES-Wallpaper) by Paul H
(`pjlhjr@gmail.com`), licensed under the Apache License, Version 2.0. The codebase has
since been substantially rewritten — modern Python, parametric config, retries,
freshness-aware scheduling, screen-exact cropping, multi-source combos, per-monitor
wallpapers, georeferenced overlays, a cross-platform backend abstraction, and more.
See [NEXT_STEPS.md](NEXT_STEPS.md) for the detailed history.

This project (the code as it exists now) is licensed under the GNU General Public
License v3.0-or-later — see [LICENSE](LICENSE). In accordance with the original
Apache License 2.0's terms, its notice is preserved below for the portions of the
project's heritage it covers.

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
unfilled — reproduced here as committed.

</details>

## Techniques

- **Georeferenced overlay approach** (`overlay_graticule`/`overlay_cities` in
  `goes_wallpaper.py`) was informed by
  [lanceberc/GOES](https://github.com/lanceberc/GOES), specifically its technique of
  georeferencing NOAA's rendered GEOS-projection imagery from documented sector
  offset/resolution constants without needing raw satellite files. No code was copied
  — this project's implementation uses `pyproj` independently and was calibrated and
  validated from scratch against real ABI L1b data and known city landmarks (see
  `NEXT_STEPS.md` and `CUSTOM_IMAGERY_PLAN.md` for details). `lanceberc/GOES` has no
  LICENSE file in its repository as of this writing, so this credit is for the idea,
  not a code-reuse license grant.

## Data source

- Satellite imagery: [NOAA STAR](https://www.star.nesdis.noaa.gov/)'s public CDN
  (`cdn.star.nesdis.noaa.gov`) and the public `noaa-goes18`/`noaa-goes19` AWS S3
  buckets. U.S. government satellite data of this kind is generally public domain;
  this credit is offered as courtesy, not because it's a license requirement.

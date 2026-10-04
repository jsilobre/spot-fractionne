# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spot Fractionné (site name; the Python package and CLI keep the name `flat-segments`) finds short stretches for running workouts near a position: flat straight segments
(200 m / 400 m / 1 km targets) and regular climbs. An offline Python pipeline precomputes every
segment for metropolitan France from OpenStreetMap + IGN elevation (LiDAR HD, RGE ALTI fallback);
a static MapLibre page (`web/`, GitHub Pages) filters them in the browser. No server, no database.

Language conventions: code, comments, docstrings and commit messages are in English; `docs/` and
`README.md` are in French.

## Commands

Python (managed with uv, Python ≥ 3.12; CI runs 3.12 and 3.13):

```bash
uv sync                                   # env + dev tools
uv run ruff check .                       # lint (also: ruff format --check .)
uv run mypy                               # strict, covers src/, tests/, scripts/
uv run pytest                             # all tests
uv run pytest tests/test_detect.py::test_name   # one test
uv run flat-segments --help               # pipeline CLI (Typer, src/flat_segments/cli.py)
```

pytest runs with `filterwarnings = error`: any new warning fails the suite. Some tests need
`tippecanoe` (≥ 2.55, not the Ubuntu package) and `osmium-tool` on PATH, as installed in CI.

Front (no build step, Node ≥ 20 only for tests):

```bash
cd web && node --test                     # filters / tile decoding / geocode tests
npx http-server web -p 8000 -c-1          # serve locally; needs HTTP Range support (not python -m http.server)
```

Without `web/data/segments.json` the page falls back to the fictitious sample in `web/data/sample/`
(shown with a banner). Regenerate it with `uv run python scripts/make_sample_data.py` (needs tippecanoe).

## Architecture

Read `docs/architecture.md` for the full component table; key ideas that span files:

- **Pipeline stages are file-to-file** (GeoParquet in `data/`, git-ignored):
  `extract` (osm.py + network.py → `strokes.parquet`) → `elevation` (elevation.py → `profiles.parquet`)
  → `detect` (profile.py + detect.py → `segments.parquet`) → `export` / `export-pmtiles`.
  `pipeline.py` holds the stage functions shared by the CLI commands; splitting them lets you
  re-tune detection without re-reading the PBF or re-sampling the DEM.
- **Pure logic is separated from I/O.** `geometry`, `network`, `profile`, `detect`, `params` work on
  numpy arrays and dataclasses and are tested on synthetic profiles/networks (`tests/helpers.py`),
  never on real files or the network. Keep new computation in pure modules.
- **All computation is in metres, Lambert-93 (EPSG:2154)**; reprojection to WGS84 happens only at
  export (`WORK_CRS` / `WEB_CRS` in `params.py`).
- **Parameters**: frozen dataclasses in `params.py` (defaults + invariant checks) → overridden by a
  TOML file (`--config`, template `configs/default.toml`) → overridden by `--set group.key=value`
  (`config.py`; unknown keys are errors). `detect` writes `segments.params.toml` beside its output.
  `tests/test_docs.py` checks that the parameter table in `docs/algorithm.md` §13 matches
  `params.py`, so changing a default means updating that table (and `configs/default.toml`).
- **National production** (`batch.py`, `departments.py`, `osm_extracts.py`): one département at a
  time on its outline buffered by 2 km, keeping segments whose midpoint is inside, with resumable
  steps recorded in `state.json`. `.github/workflows/produce.yml` runs one département per job,
  then publishes.
- **Publication** (`tiles.py`, `lineage.py`): tippecanoe builds `segments.pmtiles` (layer `segments`
  z12–14 with all attributes, `overview` z8–11), plus `segments.json` and an `ids/` index.
  `--previous` matches against the already-published set so segment ids stay stable and shared
  links keep working (ADR 0012). Published data lives on Cloudflare R2 / the `data-latest` release,
  not in the repo (ADR 0011, 0013); `pages.yml` fetches it at deploy time.
- **Front** (`web/`): `app.js` wires MapLibre + PMTiles (ES modules from a CDN via an import map,
  pinned with SRI). Pure, tested logic lives in `filters.js` (criteria, `matches`, `mapFilter`, URL
  state `?id=`, `?lat=&lon=`, `?kind=climb`), `tiles.js` (z12 tile decoding, tiles covering the
  ≤ 10 km search circle) and `geocode.js` (IGN geocoder). The result list is built from z12 tiles;
  the map's MapLibre filter expression must select exactly what `matches` selects.
- Design decisions are recorded as ADRs in `docs/adr/`; phase-2 reports in `docs/phase-2/`.

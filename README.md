# Stringer

**Offline stair-stringer planner: one plain-text file in, exact rise and run math, code checks and cut marks out.**

Stringer solves the riser count, tread depth, stringer length and board length for a stair, checks the result against residential stair limits, and prints the cut layout you need to mark a board. It uses exact fractions until the last display step, so `7 3/8"` is not `7.38"` rounded somewhere along the way. It runs on Python 3's standard library alone, makes no network calls, and keeps everything in one readable spec file.

## Features

- **Riser solving** from a total rise: the fewest risers that stay under the maximum riser height, or the count closest to a riser target you give.
- **Tread solving** that targets a 2R + T of 24.5 in, clamped to the 10 in minimum tread.
- **Exact run and stringer geometry**: total run, stringer length (Pythagoras) and stair angle in degrees.
- **Standard board selection**: the shortest 8 to 20 ft board that fits the stringer plus your end allowance, with the stringer count.
- **Code checks** for riser min and max, tread minimum, 2R + T range, and nosing, headroom and handrail height when you supply them.
- **Fit check** against your available horizontal space (`run_limit`), reporting how far over you are.
- **Cut marks**: a step-by-step table of riser and tread positions from the bottom front corner, plus the bottom riser adjustment for tread thickness.
- **ASCII profile** of the stringer silhouette, so you can eyeball the shape before cutting.
- **CSV export** of the step table in inches and millimetres for a spreadsheet.
- **JSON output** from `plan --json` for scripts and dashboards.
- **Line-numbered lint** of the spec file: unknown keys, duplicates, bad units and missing `total_rise`, with `file:line:` messages.
- **CI exit codes**: `check` exits 1 on any failed code rule and `lint` exits 2 on any spec error.
- **Mixed-unit input**: lengths can be written in inches, feet, mm, cm or m. `plan` reports inches (to 1/16") and millimetres.
- **Starter template** via `init`, which refuses to overwrite a file unless you pass `--force`.

## Quick start

Create a spec file, or start from the built-in template:

```sh
python3 stringer.py init stair.txt
```

The spec is one `key: value` pair per line. Lengths are inches unless you add a unit (`in`, `"`, `ft`, `mm`, `cm`, `m`), and fractions like `7 5/8 in` are accepted. The example below is in `examples/basement.stair`:

```text
# Basement stair, 96 in floor-to-floor
name: Basement stair
total_rise: 96 in
run_limit: 150 in
stringers: 3
tread_thickness: 1 in
stringer_allowance: 6 in
nosing: 3/4 in
headroom: 82 in
handrail_height: 36 in
```

Solve it:

```sh
python3 stringer.py plan examples/basement.stair
```

Expected output (worked out from the formulas; check it against your own run):

```text
Basement stair

  Risers      13 at 7 3/8" (187.6 mm)
  Treads      12 at 10" (254.0 mm)
  Total rise  96" (2438.4 mm)
  Total run   120" (3048.0 mm)
  2R + T      24 3/4"
  Stringer    153 11/16" long at 38.7 degrees
  Boards      3 x 14 ft (needs 159 11/16" with allowance)
```

Then check it against the code limits. `check` exits 0 when everything passes:

```sh
python3 stringer.py check examples/basement.stair
```

## How to run

Requires Python 3.8 or newer. There are no dependencies to install for the program itself.

| Command | What it does |
| --- | --- |
| `python3 stringer.py plan FILE [--json]` | Solve and summarise the stair |
| `python3 stringer.py check FILE` | Run code rules; exit 1 on any failure |
| `python3 stringer.py cuts FILE` | Print the step cut table and the bottom riser note |
| `python3 stringer.py profile FILE` | Draw an ASCII stringer profile |
| `python3 stringer.py csv FILE [-o OUT]` | Export the step table as CSV |
| `python3 stringer.py lint FILE` | Report spec errors with line numbers; exit 2 on any |
| `python3 stringer.py init FILE [--force]` | Write a starter spec file |

Spec keys: `name`, `total_rise`, `run_limit`, `riser`, `tread`, `tread_thickness` (default 1 in), `stringers` (default 2), `nosing`, `headroom`, `handrail_height`, `stringer_allowance` (default 6 in).

The residential limits are constants at the top of `stringer.py` (`RISER_MAX`, `TREAD_MIN`, `TWO_R_T_MIN`/`MAX`, and so on). Edit them there if your local code differs.

## How to test

```sh
python3 -m pytest -q
```

This build has not yet been run through its test suite in the environment where it was written, so run it before relying on the results.

The tests in `tests/test_stringer.py` cover unit parsing, fraction formatting, spec parsing with line-numbered errors, riser and tread solving, stringer geometry, board selection, every code rule, cut rows, the profile, CSV output, and the CLI exit codes for `plan`, `check`, `lint`, `init`, `csv` and missing files.

## Project layout

```text
stringer.py          the planner and CLI (standard library only)
examples/            sample spec files
tests/               pytest suite and conftest
```

## Limits

Stringer models a straight stringer with equal risers and treads, and a top landing at the full rise. It does not model winders, landings mid-flight, or curved stairs, and the code limits are a simplified residential set rather than a complete code reference. Check the result against your local code and a structural calculation before cutting.

#!/usr/bin/env python3
"""stringer: offline stair-stringer planner.

Reads one plain-text stair spec and solves the riser count, tread depth,
stringer length and board length, checks the result against residential
stair limits, and prints cut marks, a stringer profile and a step table.
All arithmetic is exact (Fraction) until the final display step.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

MM_PER_IN = Fraction(254, 10)

# Residential limits in inches, modelled on the IRC stair provisions.
RISER_MIN = Fraction(4)
RISER_MAX = Fraction(775, 100)
TREAD_MIN = Fraction(10)
TWO_R_T_MIN = Fraction(24)
TWO_R_T_MAX = Fraction(25)
TWO_R_T_TARGET = Fraction(49, 2)
NOSING_MAX = Fraction(125, 100)
HEADROOM_MIN = Fraction(80)
HANDRAIL_MIN = Fraction(34)
HANDRAIL_MAX = Fraction(38)

DEFAULT_ALLOWANCE = Fraction(6)
BOARD_LENGTHS_FT = (8, 10, 12, 14, 16, 18, 20)

# Length units expressed as a multiple of one inch.
UNITS_IN = {
    "": Fraction(1),
    "in": Fraction(1),
    "inch": Fraction(1),
    "inches": Fraction(1),
    '"': Fraction(1),
    "ft": Fraction(12),
    "'": Fraction(12),
    "cm": Fraction(50, 127),
    "mm": Fraction(5, 127),
    "m": Fraction(5000, 127),
}

LENGTH_KEYS = (
    "total_rise", "run_limit", "riser", "tread", "tread_thickness", "nosing",
    "headroom", "handrail_height", "stringer_allowance",
)
INT_KEYS = ("stringers",)
TEXT_KEYS = ("name",)

TEMPLATE = """\
# Stair spec: one key per line. Lengths are inches unless a unit is given.
name: Basement stair
total_rise: 96 in
stringers: 3
tread_thickness: 1 in
stringer_allowance: 6 in
nosing: 3/4 in

# Optional targets and limits
# riser: 7 1/2 in
# tread: 10 1/2 in
# run_limit: 120 in
# headroom: 80 in
# handrail_height: 36 in
"""


def parse_length(text: str) -> Fraction:
    """Parse '96', '96 in', '7 5/8"', '2438mm', '8 ft' into exact inches."""
    text = text.strip()
    match = re.fullmatch(r"(?:(\d+)\s+)?(\d+)/(\d+)\s*([A-Za-z\"']*)", text)
    if match:
        whole, num, den, unit = match.groups()
        if int(den) == 0:
            raise ValueError(f"zero denominator in {text!r}")
        value = Fraction(int(num), int(den)) + (int(whole) if whole else 0)
    else:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z\"']*)", text)
        if not match:
            raise ValueError(f"not a length: {text!r}")
        value = Fraction(match.group(1))
        unit = match.group(2)
    unit = unit.lower()
    if unit not in UNITS_IN:
        raise ValueError(f"unknown unit {unit!r}")
    return value * UNITS_IN[unit]


def fmt_in(value: float) -> str:
    """Inches as whole plus fraction rounded to 1/16, e.g. 7 3/8\"."""
    sixteenths = round(float(value) * 16)
    sign = "-" if sixteenths < 0 else ""
    whole, rem = divmod(abs(sixteenths), 16)
    frac = Fraction(rem, 16)
    if frac == 0:
        return f'{sign}{whole}"'
    if whole == 0:
        return f'{sign}{frac.numerator}/{frac.denominator}"'
    return f'{sign}{whole} {frac.numerator}/{frac.denominator}"'


def fmt_mm(value: float) -> str:
    return f"{float(value) * float(MM_PER_IN):.1f} mm"


@dataclass
class Spec:
    name: str = "Untitled stair"
    total_rise: Fraction | None = None
    run_limit: Fraction | None = None
    riser: Fraction | None = None
    tread: Fraction | None = None
    tread_thickness: Fraction = Fraction(1)
    nosing: Fraction | None = None
    headroom: Fraction | None = None
    handrail_height: Fraction | None = None
    stringer_allowance: Fraction = DEFAULT_ALLOWANCE
    stringers: int = 2


@dataclass
class Plan:
    risers: int
    riser: Fraction
    tread: Fraction
    total_rise: Fraction
    run: Fraction
    stringer_length: float
    angle: float
    two_r_t: Fraction
    board_ft: int | None
    stringers: int
    need_length: float


@dataclass(frozen=True)
class Check:
    rule: str
    ok: bool
    message: str


def parse_spec(text: str) -> tuple[Spec, list[tuple[int | None, str]]]:
    """Parse key: value lines. Returns the spec and (line, message) errors."""
    spec = Spec()
    errors: list[tuple[int | None, str]] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z_]+)\s*[:=]\s*(.*)", line)
        if not match:
            errors.append((lineno, f"expected 'key: value', got {line!r}"))
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key not in LENGTH_KEYS + INT_KEYS + TEXT_KEYS:
            errors.append((lineno, f"unknown key {key!r}"))
            continue
        if key in seen:
            errors.append((lineno, f"duplicate key {key!r}"))
            continue
        seen.add(key)
        try:
            if key in TEXT_KEYS:
                if not value:
                    raise ValueError("name cannot be empty")
                spec.name = value
            elif key in INT_KEYS:
                count = int(value)
                if count < 1:
                    raise ValueError("stringers must be at least 1")
                spec.stringers = count
            else:
                length = parse_length(value)
                if length <= 0:
                    raise ValueError(f"{key} must be greater than zero")
                setattr(spec, key, length)
        except ValueError as exc:
            errors.append((lineno, str(exc)))
    if "total_rise" not in seen:
        errors.append((None, "missing required key 'total_rise'"))
    return spec, errors


def solve(spec: Spec) -> Plan:
    """Solve riser count, tread depth and stringer geometry."""
    if spec.total_rise is None:
        raise ValueError("total_rise is required")
    total = spec.total_rise
    if spec.riser is not None:
        risers = max(1, round(total / spec.riser))
    else:
        risers = max(1, math.ceil(total / RISER_MAX))
    riser = total / risers
    if spec.tread is not None:
        tread = spec.tread
    else:
        tread = max(TREAD_MIN, TWO_R_T_TARGET - 2 * riser)
    run = (risers - 1) * tread
    length = math.hypot(float(run), float(total))
    angle = math.degrees(math.atan2(float(total), float(run)))
    need = length + float(spec.stringer_allowance)
    board = next((ft for ft in BOARD_LENGTHS_FT if ft * 12 >= need), None)
    return Plan(
        risers=risers,
        riser=riser,
        tread=tread,
        total_rise=total,
        run=run,
        stringer_length=length,
        angle=angle,
        two_r_t=2 * riser + tread,
        board_ft=board,
        stringers=spec.stringers,
        need_length=need,
    )


def check(spec: Spec, plan: Plan) -> list[Check]:
    """Run each applicable code rule; optional inputs add their own rules."""
    results: list[Check] = []

    def rule(name: str, ok: bool, message: str) -> None:
        results.append(Check(name, ok, message))

    rule("risers", plan.risers >= 2, f"{plan.risers} risers (need at least 2)")
    rule("riser-max", plan.riser <= RISER_MAX,
         f"riser {fmt_in(plan.riser)} vs max {fmt_in(RISER_MAX)}")
    rule("riser-min", plan.riser >= RISER_MIN,
         f"riser {fmt_in(plan.riser)} vs min {fmt_in(RISER_MIN)}")
    rule("tread-min", plan.tread >= TREAD_MIN,
         f"tread {fmt_in(plan.tread)} vs min {fmt_in(TREAD_MIN)}")
    rule("2r+t", TWO_R_T_MIN <= plan.two_r_t <= TWO_R_T_MAX,
         f"2R+T = {fmt_in(plan.two_r_t)} (range {fmt_in(TWO_R_T_MIN)} to {fmt_in(TWO_R_T_MAX)})")
    if spec.nosing is not None:
        rule("nosing-max", spec.nosing <= NOSING_MAX,
             f"nosing {fmt_in(spec.nosing)} vs max {fmt_in(NOSING_MAX)}")
    if spec.headroom is not None:
        rule("headroom", spec.headroom >= HEADROOM_MIN,
             f"headroom {fmt_in(spec.headroom)} vs min {fmt_in(HEADROOM_MIN)}")
    if spec.handrail_height is not None:
        rule("handrail", HANDRAIL_MIN <= spec.handrail_height <= HANDRAIL_MAX,
             f"handrail {fmt_in(spec.handrail_height)} (range {fmt_in(HANDRAIL_MIN)} to {fmt_in(HANDRAIL_MAX)})")
    if spec.run_limit is not None:
        over = plan.run - spec.run_limit
        rule("run-limit", over <= 0,
             f"run {fmt_in(plan.run)} vs space {fmt_in(spec.run_limit)}"
             + (f", over by {fmt_in(over)}" if over > 0 else ""))
    rule("board", plan.board_ft is not None,
         f"stringer needs {fmt_in(plan.need_length)} incl. allowance"
         + ("" if plan.board_ft is not None else ", longer than the largest standard board"))
    return results


def cut_rows(plan: Plan) -> list[dict]:
    """Step geometry measured from the stringer's bottom front corner."""
    rows = []
    for k in range(1, plan.risers + 1):
        rows.append({
            "step": k,
            "riser_top": k * plan.riser,
            "riser_along": (k - 1) * plan.tread,
            "tread_end": k * plan.tread if k < plan.risers else None,
        })
    return rows


def render_cuts(spec: Spec, plan: Plan) -> str:
    lines = [f"{spec.name}: cut marks (from bottom front corner)", ""]
    lines.append(f"{'Step':>4}  {'Riser top':>10}  {'Riser at':>10}  {'Tread ends':>10}")
    for row in cut_rows(plan):
        ends = fmt_in(row["tread_end"]) if row["tread_end"] is not None else "landing"
        lines.append(f"{row['step']:>4}  {fmt_in(row['riser_top']):>10}  "
                     f"{fmt_in(row['riser_along']):>10}  {ends:>10}")
    lines.append("")
    bottom = plan.riser - spec.tread_thickness
    lines.append(f"Bottom riser: cut {fmt_in(plan.riser)} less tread thickness "
                 f"{fmt_in(spec.tread_thickness)} = {fmt_in(bottom)}")
    lines.append(f"Top: plumb cut at along {fmt_in(plan.run)}, seat on the header")
    return "\n".join(lines)


def profile(plan: Plan, cols: int = 48) -> str:
    """ASCII silhouette of the stair; '#' is the stringer board."""
    run, total = float(plan.run), float(plan.total_rise)
    if plan.risers < 2 or run <= 0:
        return "(no run: need at least 2 risers)"
    tread, riser = float(plan.tread), float(plan.riser)
    rows = max(4, round(cols * total / run / 2))
    out = []
    for r in range(rows, 0, -1):
        y = total * (r - 0.5) / rows
        line = ""
        for c in range(cols):
            x = run * (c + 0.5) / cols
            level = (math.floor(x / tread) + 1) * riser
            line += "#" if y <= level else " "
        out.append("|" + line)
    out.append("+" + "-" * cols)
    out.append(f" 0{' ' * (cols - len(fmt_in(plan.run)) - 1)}{fmt_in(plan.run)}")
    return "\n".join(out)


def plan_dict(spec: Spec, plan: Plan) -> dict:
    return {
        "name": spec.name,
        "risers": plan.risers,
        "riser_in": float(plan.riser),
        "tread_in": float(plan.tread),
        "total_rise_in": float(plan.total_rise),
        "total_run_in": float(plan.run),
        "two_r_plus_t_in": float(plan.two_r_t),
        "stringer_length_in": round(plan.stringer_length, 3),
        "angle_deg": round(plan.angle, 2),
        "stringers": plan.stringers,
        "board_ft": plan.board_ft,
        "failed_checks": [c.rule for c in check(spec, plan) if not c.ok],
    }


def render_plan(spec: Spec, plan: Plan) -> str:
    lines = [spec.name, ""]
    lines.append(f"  Risers      {plan.risers} at {fmt_in(plan.riser)} ({fmt_mm(plan.riser)})")
    lines.append(f"  Treads      {plan.risers - 1} at {fmt_in(plan.tread)} ({fmt_mm(plan.tread)})")
    lines.append(f"  Total rise  {fmt_in(plan.total_rise)} ({fmt_mm(plan.total_rise)})")
    lines.append(f"  Total run   {fmt_in(plan.run)} ({fmt_mm(plan.run)})")
    lines.append(f"  2R + T      {fmt_in(plan.two_r_t)}")
    lines.append(f"  Stringer    {fmt_in(plan.stringer_length)} long at {plan.angle:.1f} degrees")
    if plan.board_ft is not None:
        lines.append(f"  Boards      {plan.stringers} x {plan.board_ft} ft "
                     f"(needs {fmt_in(plan.need_length)} with allowance)")
    else:
        lines.append(f"  Boards      no standard board reaches {fmt_in(plan.need_length)}; splice needed")
    return "\n".join(lines)


def render_check(results: list[Check]) -> str:
    lines = []
    for item in results:
        lines.append(f"  {'PASS' if item.ok else 'FAIL'}  {item.rule:<11} {item.message}")
    failed = sum(1 for item in results if not item.ok)
    lines.append("")
    lines.append("All checks passed." if not failed else f"{failed} check(s) failed.")
    return "\n".join(lines)


def step_csv(plan: Plan) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["step", "riser_top_in", "riser_top_mm", "riser_along_in", "tread_end_in"])
    for row in cut_rows(plan):
        ends = "" if row["tread_end"] is None else f"{float(row['tread_end']):.4f}"
        writer.writerow([
            row["step"],
            f"{float(row['riser_top']):.4f}",
            f"{float(row['riser_top'] * MM_PER_IN):.2f}",
            f"{float(row['riser_along']):.4f}",
            ends,
        ])
    return buffer.getvalue()


def load(path: str, err) -> tuple[Spec, Plan] | None:
    """Read and solve a spec file, printing any problems to err."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"stringer: cannot read {path}: {exc.strerror}", file=err)
        return None
    spec, errors = parse_spec(text)
    if errors:
        for lineno, message in errors:
            where = f"{path}:{lineno}" if lineno else path
            print(f"{where}: {message}", file=err)
        return None
    return spec, solve(spec)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stringer",
        description="Offline stair-stringer planner driven by a plain-text spec file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="solve risers, treads, stringer and boards")
    p_plan.add_argument("file")
    p_plan.add_argument("--json", action="store_true", help="print JSON instead of text")

    for name, help_text in [
        ("check", "check against code limits; exit 1 on any failure"),
        ("cuts", "print step cut marks and the bottom riser note"),
        ("profile", "draw an ASCII profile of the stringer"),
        ("lint", "report line-numbered spec errors; exit 2 on any error"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("file")

    p_csv = sub.add_parser("csv", help="export the step table as CSV")
    p_csv.add_argument("file")
    p_csv.add_argument("-o", "--output", help="write to this file instead of stdout")

    p_init = sub.add_parser("init", help="write a starter spec file")
    p_init.add_argument("file")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out, err = sys.stdout, sys.stderr

    if args.command == "init":
        target = Path(args.file)
        if target.exists() and not args.force:
            print(f"stringer: {args.file} exists; use --force to overwrite", file=err)
            return 2
        target.write_text(TEMPLATE, encoding="utf-8")
        print(f"wrote {args.file}", file=out)
        return 0

    if args.command == "lint":
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"stringer: cannot read {args.file}: {exc.strerror}", file=err)
            return 2
        _, errors = parse_spec(text)
        for lineno, message in errors:
            where = f"{args.file}:{lineno}" if lineno else args.file
            print(f"{where}: {message}", file=err)
        if errors:
            return 2
        print(f"{args.file}: OK", file=out)
        return 0

    loaded = load(args.file, err)
    if loaded is None:
        return 2
    spec, plan = loaded

    if args.command == "plan":
        if args.json:
            print(json.dumps(plan_dict(spec, plan), indent=2), file=out)
        else:
            print(render_plan(spec, plan), file=out)
        return 0
    if args.command == "check":
        results = check(spec, plan)
        print(render_check(results), file=out)
        return 0 if all(item.ok for item in results) else 1
    if args.command == "cuts":
        print(render_cuts(spec, plan), file=out)
        return 0
    if args.command == "profile":
        print(profile(plan), file=out)
        return 0
    if args.command == "csv":
        text = step_csv(plan)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"wrote {args.output}", file=out)
        else:
            out.write(text)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

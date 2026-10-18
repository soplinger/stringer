import json
import math
from fractions import Fraction

import pytest

from stringer import (
    check,
    cut_rows,
    fmt_in,
    main,
    parse_length,
    parse_spec,
    profile,
    solve,
    step_csv,
)


def spec_from(text):
    spec, errors = parse_spec(text)
    assert errors == []
    return spec


def test_parse_length_accepts_common_forms():
    assert parse_length("96") == 96
    assert parse_length("96 in") == 96
    assert parse_length("7 5/8 in") == Fraction(61, 8)
    assert parse_length("3/4\"") == Fraction(3, 4)
    assert parse_length("8 ft") == 96
    assert parse_length("2438 mm") == Fraction(2438 * 5, 127)
    assert parse_length("7.75") == Fraction(31, 4)


def test_parse_length_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_length("tall")
    with pytest.raises(ValueError):
        parse_length("3 furlong")
    with pytest.raises(ValueError):
        parse_length("1/0 in")


def test_fmt_in_rounds_to_sixteenths():
    assert fmt_in(Fraction(61, 8)) == '7 5/8"'
    assert fmt_in(Fraction(3, 4)) == '3/4"'
    assert fmt_in(9) == '9"'
    assert fmt_in(Fraction(15, 16)) == '15/16"'
    assert fmt_in(Fraction(1, 33)) == '0"'


def test_parse_spec_reads_keys_and_ignores_comments():
    spec = spec_from(
        "# comment\n"
        "name: Shed stair  # trailing comment\n"
        "total_rise: 96 in\n"
        "stringers: 3\n"
        "nosing: 3/4 in\n"
    )
    assert spec.name == "Shed stair"
    assert spec.total_rise == 96
    assert spec.stringers == 3
    assert spec.nosing == Fraction(3, 4)


def test_parse_spec_reports_line_numbered_errors():
    _, errors = parse_spec(
        "total_rise: 96 in\n"
        "total_rise: 100 in\n"
        "colour: red\n"
        "riser: nope\n"
        "stringers: 0\n"
    )
    lines = {line: msg for line, msg in errors}
    assert lines[2] == "duplicate key 'total_rise'"
    assert lines[3] == "unknown key 'colour'"
    assert "not a length" in lines[4]
    assert lines[5] == "stringers must be at least 1"


def test_parse_spec_requires_total_rise():
    _, errors = parse_spec("name: no rise\n")
    assert (None, "missing required key 'total_rise'") in errors


def test_solve_uses_fewest_risers_under_max():
    plan = solve(spec_from("total_rise: 96 in\n"))
    assert plan.risers == 13  # ceil(96 / 7.75)
    assert plan.riser == Fraction(96, 13)
    assert plan.tread == 10  # 24.5 - 2R is below the 10 in minimum
    assert plan.run == 120


def test_solve_with_riser_target_rounds_count():
    plan = solve(spec_from("total_rise: 96 in\nriser: 7.5 in\n"))
    assert plan.risers == 13  # round(96 / 7.5 = 12.8)
    assert plan.riser == Fraction(96, 13)


def test_solve_tread_hits_two_r_plus_t_target_when_possible():
    plan = solve(spec_from("total_rise: 72 in\nriser: 7 in\n"))
    assert plan.risers == 10  # round(72 / 7)
    assert plan.riser == Fraction(36, 5)
    assert plan.two_r_t == Fraction(49, 2)  # tread is solved to 24.5 - 2R


def test_solve_explicit_tread_is_kept():
    plan = solve(spec_from("total_rise: 84 in\ntread: 11 in\n"))
    assert plan.tread == 11
    assert plan.run == (plan.risers - 1) * 11


def test_stringer_length_and_angle_match_pythagoras():
    plan = solve(spec_from("total_rise: 96 in\ntread: 10 in\n"))
    assert plan.stringer_length == pytest.approx(math.hypot(120, 96))
    assert plan.angle == pytest.approx(math.degrees(math.atan2(96, 120)))


def test_board_choice_is_smallest_standard_board_that_fits():
    plan = solve(spec_from("total_rise: 96 in\nstringer_allowance: 6 in\n"))
    # needs about 159.7 in, so 14 ft (168 in) is the smallest that fits
    assert plan.board_ft == 14


def test_board_none_when_stringer_too_long():
    plan = solve(spec_from("total_rise: 200 in\ntread: 30 in\n"))
    assert plan.board_ft is None


def test_check_passes_for_compliant_stair():
    spec = spec_from(
        "total_rise: 96 in\nrun_limit: 150 in\nnosing: 3/4 in\n"
        "headroom: 82 in\nhandrail_height: 36 in\n"
    )
    results = check(spec, solve(spec))
    assert all(item.ok for item in results)


def test_check_flags_riser_too_tall_and_bad_two_r_plus_t():
    spec = spec_from("total_rise: 72 in\nriser: 9 in\ntread: 10 in\n")
    failed = {item.rule for item in check(spec, solve(spec)) if not item.ok}
    assert "riser-max" in failed
    assert "2r+t" in failed


def test_check_flags_run_over_limit_and_low_headroom():
    spec = spec_from("total_rise: 96 in\nrun_limit: 100 in\nheadroom: 72 in\n")
    failed = {item.rule for item in check(spec, solve(spec)) if not item.ok}
    assert {"run-limit", "headroom"} <= failed


def test_cut_rows_place_each_riser_and_tread():
    plan = solve(spec_from("total_rise: 24 in\nriser: 6 in\ntread: 10 in\n"))
    rows = cut_rows(plan)
    assert len(rows) == 4
    assert rows[0]["riser_along"] == 0
    assert rows[1]["riser_along"] == 10
    assert rows[1]["riser_top"] == 12
    assert rows[-1]["tread_end"] is None


def test_profile_draws_a_filled_stringer():
    plan = solve(spec_from("total_rise: 96 in\ntread: 10 in\n"))
    art = profile(plan).splitlines()
    assert any("#" in line for line in art)
    assert art[-2].startswith("+")


def test_profile_handles_single_riser():
    plan = solve(spec_from("total_rise: 4 in\nriser: 8 in\n"))
    assert profile(plan).startswith("(no run")


def test_step_csv_has_one_row_per_riser():
    plan = solve(spec_from("total_rise: 96 in\nriser: 8 in\n"))
    lines = step_csv(plan).strip().splitlines()
    assert lines[0] == "step,riser_top_in,riser_top_mm,riser_along_in,tread_end_in"
    assert len(lines) == 1 + plan.risers
    assert lines[-1].endswith(",")


def test_cli_plan_json_round_trip(tmp_path, capsys):
    path = tmp_path / "a.stair"
    path.write_text("total_rise: 96 in\nstringers: 3\n", encoding="utf-8")
    assert main(["plan", "--json", str(path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["risers"] == 13
    assert data["stringers"] == 3
    assert data["board_ft"] == 14


def test_cli_check_exit_codes(tmp_path):
    good = tmp_path / "good.stair"
    good.write_text("total_rise: 96 in\n", encoding="utf-8")
    bad = tmp_path / "bad.stair"
    bad.write_text("total_rise: 72 in\nriser: 9 in\ntread: 10 in\n", encoding="utf-8")
    assert main(["check", str(good)]) == 0
    assert main(["check", str(bad)]) == 1


def test_cli_lint_reports_errors_with_exit_2(tmp_path, capsys):
    path = tmp_path / "typo.stair"
    path.write_text("total_rise: 96 in\ntotal_rse: 3\n", encoding="utf-8")
    assert main(["lint", str(path)]) == 2
    assert f"{path}:2: unknown key 'total_rse'" in capsys.readouterr().err


def test_cli_init_refuses_to_overwrite(tmp_path, capsys):
    path = tmp_path / "new.stair"
    assert main(["init", str(path)]) == 0
    assert main(["init", str(path)]) == 2
    assert main(["init", "--force", str(path)]) == 0
    assert "total_rise" in path.read_text(encoding="utf-8")
    assert "exists" in capsys.readouterr().err


def test_cli_csv_writes_output_file(tmp_path):
    spec_path = tmp_path / "a.stair"
    spec_path.write_text("total_rise: 48 in\nriser: 8 in\n", encoding="utf-8")
    out_path = tmp_path / "steps.csv"
    assert main(["csv", str(spec_path), "-o", str(out_path)]) == 0
    assert out_path.read_text(encoding="utf-8").count("\n") == 1 + 6


def test_cli_missing_file_exits_2(tmp_path, capsys):
    assert main(["plan", str(tmp_path / "nope.stair")]) == 2
    assert "cannot read" in capsys.readouterr().err

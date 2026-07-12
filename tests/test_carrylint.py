from __future__ import annotations

import textwrap

from cascade.lint.carrylint import (
    Violation,
    ViolationKind,
    check_carries,
    check_paths,
    main,
)

MODELS: set[str] = {"Downstream"}


def _kinds(violations: list[Violation]) -> list[ViolationKind]:
    return [violation.kind for violation in violations]


def _wrap(body: str) -> str:
    """A function body around a carry expression, so only in-function scanning is
    exercised (module-level construction is out of scope by design)."""
    return "def build(o):\n" + textwrap.indent(textwrap.dedent(body), "    ")


def test_matching_carry_is_clean() -> None:
    source = _wrap("return Downstream(velocity=o.velocity)")

    assert check_carries(source, MODELS) == []


def test_renamed_carry_fails() -> None:
    source = _wrap("return Downstream(speed=o.velocity)")

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]
    assert "velocity" in violations[0].detail and "speed" in violations[0].detail
    assert violations[0].blocking is True


def test_nested_chain_rename_fails() -> None:
    source = _wrap("return Downstream(speed=o.trip.velocity)")

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]


def test_nested_chain_matching_is_clean() -> None:
    source = _wrap("return Downstream(velocity=o.trip.velocity)")

    assert check_carries(source, MODELS) == []


def test_binop_transformation_allowed_even_when_name_differs() -> None:
    source = _wrap("return Downstream(speed=o.velocity * 2)")

    assert check_carries(source, MODELS) == []


def test_call_transformation_allowed_even_when_name_differs() -> None:
    source = _wrap("return Downstream(speed=round(o.velocity))")

    assert check_carries(source, MODELS) == []


def test_single_assignment_alias_rename_fails() -> None:
    source = _wrap(
        """
        tmp = o.velocity
        return Downstream(speed=tmp)
        """
    )

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]
    assert "velocity" in violations[0].detail and "speed" in violations[0].detail


def test_single_assignment_alias_name_preserving_is_clean() -> None:
    source = _wrap(
        """
        tmp = o.velocity
        return Downstream(velocity=tmp)
        """
    )

    assert check_carries(source, MODELS) == []


def test_reassigned_alias_is_not_treated_as_carry() -> None:
    source = _wrap(
        """
        tmp = o.velocity
        tmp = o.altitude
        return Downstream(speed=tmp)
        """
    )

    assert check_carries(source, MODELS) == []


def test_alias_assigned_from_non_chain_is_not_a_carry() -> None:
    source = _wrap(
        """
        tmp = o.velocity * 2
        return Downstream(speed=tmp)
        """
    )

    assert check_carries(source, MODELS) == []


def test_dict_literal_unpack_rename_fails() -> None:
    source = _wrap('return Downstream(**{"speed": o.velocity})')

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]


def test_dict_literal_unpack_matching_is_clean() -> None:
    source = _wrap('return Downstream(**{"velocity": o.velocity})')

    assert check_carries(source, MODELS) == []


def test_opaque_unpack_is_untraceable_warning() -> None:
    source = _wrap("return Downstream(**payload)")

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.UNTRACEABLE_CARRY]
    assert violations[0].blocking is False


def test_dict_with_computed_keys_is_untraceable_warning() -> None:
    source = _wrap("return Downstream(**{key: o.velocity})")

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.UNTRACEABLE_CARRY]


def test_positional_construction_is_warning() -> None:
    source = _wrap("return Downstream(o.velocity)")

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.POSITIONAL_CONSTRUCTION]
    assert violations[0].blocking is False


def test_non_model_constructor_calls_are_ignored() -> None:
    source = _wrap("return Other(speed=o.velocity)")

    assert check_carries(source, MODELS) == []


def test_self_attribute_chain_rename_fails_in_method() -> None:
    source = textwrap.dedent(
        """
        class Builder:
            def build(self):
                return Downstream(speed=self.velocity)
        """
    )

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]


def test_module_level_construction_is_out_of_scope() -> None:
    source = "value = Downstream(speed=o.velocity)\n"

    assert check_carries(source, MODELS) == []


def test_nested_function_is_scanned_with_its_own_parameters() -> None:
    source = textwrap.dedent(
        """
        def outer(o):
            def inner(p):
                return Downstream(speed=p.velocity)
            return inner
        """
    )

    violations = check_carries(source, MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]


def test_whole_parameter_passed_is_not_a_field_rename() -> None:
    source = _wrap("return Downstream(route=o)")

    assert check_carries(source, MODELS) == []


def test_check_paths_reports_path_and_aggregates(tmp_path) -> None:
    file_path = tmp_path / "flow.py"
    file_path.write_text(_wrap("return Downstream(speed=o.velocity)"))

    violations = check_paths([str(file_path)], MODELS)

    assert _kinds(violations) == [ViolationKind.RENAMED_CARRY]
    assert violations[0].path == str(file_path)
    assert violations[0].line >= 1


def test_cli_returns_one_on_blocking_violation(tmp_path, capsys) -> None:
    file_path = tmp_path / "flow.py"
    file_path.write_text(_wrap("return Downstream(speed=o.velocity)"))

    exit_code = main([str(file_path), "--models", "Downstream"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "RENAMED_CARRY" in output
    assert str(file_path) in output


def test_cli_returns_zero_on_warning_only(tmp_path, capsys) -> None:
    file_path = tmp_path / "flow.py"
    file_path.write_text(_wrap("return Downstream(**payload)"))

    exit_code = main([str(file_path), "--models", "Downstream"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "UNTRACEABLE_CARRY" in output


def test_cli_returns_zero_when_clean(tmp_path, capsys) -> None:
    file_path = tmp_path / "flow.py"
    file_path.write_text(_wrap("return Downstream(velocity=o.velocity)"))

    exit_code = main([str(file_path), "--models", "Downstream"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output == ""

from __future__ import annotations

from pydantic import BaseModel

from cascade.lint.derivlint import (
    Violation,
    ViolationKind,
    check_derivations,
    check_numbered_fields,
    check_paths,
    main,
)

MODELS: set[str] = {"C"}


def _of_kind(violations: list[Violation], kind: ViolationKind) -> list[Violation]:
    return [violation for violation in violations if violation.kind == kind]


# --------------------------------------------------------------------------- #
# Duplicate derivations
# --------------------------------------------------------------------------- #
def test_same_binop_in_two_factories_under_different_names_is_duplicate() -> None:
    source: str = (
        "def make_a(o: Order):\n"
        "    return C(speed=o.distance / o.time)\n"
        "\n"
        "def make_b(o: Order):\n"
        "    return C(rate=o.distance / o.time)\n"
    )

    violations: list[Violation] = check_derivations(source, MODELS)

    duplicates: list[Violation] = _of_kind(
        violations, ViolationKind.DUPLICATE_DERIVATION
    )
    assert len(duplicates) == 1
    fields: set[str] = {site.field for site in duplicates[0].sites}
    assert fields == {"speed", "rate"}
    assert "Order.distance / Order.time" in duplicates[0].detail


def test_alias_form_matches_direct_form() -> None:
    source: str = (
        "def make_a(o: Order):\n"
        "    return C(speed=o.distance / o.time)\n"
        "\n"
        "def make_b(o: Order):\n"
        "    d = o.distance\n"
        "    t = o.time\n"
        "    return C(rate=d / t)\n"
    )

    duplicates: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.DUPLICATE_DERIVATION
    )

    assert len(duplicates) == 1
    assert {site.field for site in duplicates[0].sites} == {"speed", "rate"}


def test_same_formula_over_different_typed_inputs_is_not_a_duplicate() -> None:
    source: str = (
        "def make_a(o: Order):\n"
        "    return C(speed=o.distance / o.time)\n"
        "\n"
        "def make_b(leg: Leg):\n"
        "    return C(rate=leg.distance / leg.time)\n"
    )

    duplicates: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.DUPLICATE_DERIVATION
    )

    assert duplicates == []


def test_commutativity_is_not_assumed() -> None:
    source: str = (
        "def make_a(o: Order):\n"
        "    return C(speed=o.distance / o.time)\n"
        "\n"
        "def make_b(o: Order):\n"
        "    return C(rate=o.time / o.distance)\n"
    )

    duplicates: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.DUPLICATE_DERIVATION
    )

    assert duplicates == []


def test_carries_constants_and_no_arg_calls_are_not_fingerprinted() -> None:
    source: str = (
        "K = 10\n"
        "def make_a(o: Order):\n"
        "    return C(alpha=o.distance, beta=42, gamma=o.snapshot())\n"
        "\n"
        "def make_b(o: Order):\n"
        "    return C(delta=o.distance, epsilon=42, zeta=o.snapshot())\n"
    )

    assert check_derivations(source, MODELS) == []


def test_computed_field_return_matching_factory_expression_is_duplicate() -> None:
    source: str = (
        "class Order(BaseModel):\n"
        "    @computed_field\n"
        "    @property\n"
        "    def derived(self) -> float:\n"
        "        return self.distance / self.time\n"
        "\n"
        "def make(o: Order):\n"
        "    return C(speed=o.distance / o.time)\n"
    )

    duplicates: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.DUPLICATE_DERIVATION
    )

    assert len(duplicates) == 1
    functions: set[str] = {site.function for site in duplicates[0].sites}
    assert functions == {"Order.derived", "make"}


def test_same_derivation_twice_in_one_function_field_is_not_a_duplicate() -> None:
    source: str = (
        "def make_a(o: Order):\n"
        "    x = C(speed=o.distance / o.time)\n"
        "    y = C(speed=o.distance / o.time)\n"
        "    return x, y\n"
    )

    assert (
        _of_kind(check_derivations(source, MODELS), ViolationKind.DUPLICATE_DERIVATION)
        == []
    )


# --------------------------------------------------------------------------- #
# Numbered names (source level)
# --------------------------------------------------------------------------- #
def test_numbered_constructor_kwarg_is_flagged() -> None:
    source: str = "def make(o: Order):\n    return C(velocity1=o.distance / o.time)\n"

    numbered: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.NUMBERED_NAME
    )

    assert len(numbered) == 1
    assert numbered[0].sites[0].field == "velocity1"


def test_numbered_computed_field_method_name_is_flagged() -> None:
    source: str = (
        "class Order(BaseModel):\n"
        "    @computed_field\n"
        "    def cost2(self) -> float:\n"
        "        return self.a + self.b\n"
    )

    numbered: list[Violation] = _of_kind(
        check_derivations(source, MODELS), ViolationKind.NUMBERED_NAME
    )

    assert len(numbered) == 1
    assert numbered[0].sites[0].field == "cost2"


# --------------------------------------------------------------------------- #
# Numbered names (real Pydantic models)
# --------------------------------------------------------------------------- #
class _Base(BaseModel):
    velocity1: float


class _Child(_Base):
    extra: int


class _Clean(BaseModel):
    velocity: float


def test_check_numbered_fields_flags_numbered_declared_field() -> None:
    violations: list[Violation] = check_numbered_fields([_Base])

    assert len(violations) == 1
    assert violations[0].kind == ViolationKind.NUMBERED_NAME
    assert violations[0].sites[0].field == "velocity1"
    assert violations[0].sites[0].function == "_Base"


def test_check_numbered_fields_counts_inherited_field_once() -> None:
    violations: list[Violation] = check_numbered_fields([_Base, _Child])

    assert len(violations) == 1
    assert violations[0].sites[0].function == "_Base"


def test_check_numbered_fields_clean_model_passes() -> None:
    assert check_numbered_fields([_Clean]) == []


# --------------------------------------------------------------------------- #
# Cross-file comparison
# --------------------------------------------------------------------------- #
def test_check_paths_compares_fingerprints_across_files(tmp_path) -> None:
    file_a = tmp_path / "factory_a.py"
    file_b = tmp_path / "factory_b.py"
    file_a.write_text(
        "def make_a(o: Order):\n    return C(speed=o.distance / o.time)\n"
    )
    file_b.write_text("def make_b(o: Order):\n    return C(rate=o.distance / o.time)\n")

    duplicates: list[Violation] = _of_kind(
        check_paths([str(file_a), str(file_b)], MODELS),
        ViolationKind.DUPLICATE_DERIVATION,
    )

    assert len(duplicates) == 1
    paths: set[str] = {site.path for site in duplicates[0].sites}
    assert paths == {str(file_a), str(file_b)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_exit_zero_when_clean(tmp_path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("def make(o: Order):\n    return C(speed=o.distance / o.time)\n")

    assert main([str(clean), "--models", "C"]) == 0


def test_cli_exit_one_when_duplicate(tmp_path) -> None:
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text(
        "def make_a(o: Order):\n    return C(speed=o.distance / o.time)\n"
    )
    file_b.write_text("def make_b(o: Order):\n    return C(rate=o.distance / o.time)\n")

    assert main([str(file_a), str(file_b), "--models", "C"]) == 1

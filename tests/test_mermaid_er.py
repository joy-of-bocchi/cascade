from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, create_model

from cascade.render import MermaidBackend


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def make_model(
    name: str,
    module: str,
    fields: dict[str, tuple[type, object]],
) -> type[BaseModel]:
    model: type[BaseModel] = create_model(
        name,
        __base__=FrozenModel,
        __module__=module,
        **fields,
    )
    return model


def test_mermaid_er_qualifies_duplicate_entity_names_and_keeps_unique_names_short() -> (
    None
):
    left: type[BaseModel] = make_model("Thing", "alpha.models", {"value": (int, ...)})
    right: type[BaseModel] = make_model("Thing", "beta.models", {"value": (int, ...)})
    carrier: type[BaseModel] = make_model(
        "Carrier",
        "carrier.models",
        {"left": (left, ...), "right": (right, ...)},
    )

    rendered: str = MermaidBackend().render_er([carrier])

    assert "  Carrier {" in rendered
    assert "  alpha_models_Thing {" in rendered
    assert "  beta_models_Thing {" in rendered
    assert '  Carrier ||--|| alpha_models_Thing : "left"' in rendered
    assert '  Carrier ||--|| beta_models_Thing : "right"' in rendered


def test_mermaid_er_raises_on_sanitized_entity_id_collision() -> None:
    first: type[BaseModel] = make_model("Thing", "same-name", {"value": (int, ...)})
    second: type[BaseModel] = make_model("Thing", "same_name", {"value": (int, ...)})
    carrier: type[BaseModel] = make_model(
        "Carrier",
        "carrier.models",
        {"first": (first, ...), "second": (second, ...)},
    )

    with pytest.raises(
        ValueError,
        match="Mermaid ER entity id collision for 'same_name_Thing'",
    ):
        MermaidBackend().render_er([carrier])

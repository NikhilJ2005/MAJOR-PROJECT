import pytest
from pydantic import ValidationError

from agent.state import ProjectSpec, pluralize, to_pascal, to_snake


def test_naming_helpers():
    assert to_snake("BlogPost") == "blog_post"
    assert to_snake("class") == "class_"
    assert to_pascal("order item") == "OrderItem"
    assert pluralize("category") == "categories"
    assert pluralize("box") == "boxes"
    assert pluralize("day") == "days"


def test_entities_sorted_by_foreign_keys_and_normalised():
    spec = ProjectSpec.model_validate(
        {
            "project_name": "Shop API",
            "entities": [
                {"name": "order item", "fields": [{"name": "orderId", "references": "order", "type": "str"}]},
                {"name": "Order", "fields": [{"name": "id"}, {"name": "total", "type": "float"}]},
            ],
        }
    )
    assert spec.project_name == "shop_api"
    assert [e.name for e in spec.entities] == ["Order", "OrderItem"]
    fk = spec.entities[1].fields[0]
    assert (fk.name, fk.type, fk.references) == ("order_id", "int", "Order")
    assert [f.name for f in spec.entities[0].fields] == ["total"]  # reserved `id` dropped


def test_auth_replaces_declared_user_entity():
    spec = ProjectSpec.model_validate(
        {"project_name": "x", "auth": True, "entities": [{"name": "User", "fields": []}, {"name": "Note", "fields": [{"name": "owner_id", "references": "User"}]}]}
    )
    assert [e.name for e in spec.entities] == ["Note"]


def test_unknown_reference_rejected():
    with pytest.raises(ValidationError, match="unknown entity"):
        ProjectSpec.model_validate({"project_name": "x", "entities": [{"name": "A", "fields": [{"name": "b_id", "references": "B"}]}]})


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="circular"):
        ProjectSpec.model_validate(
            {
                "project_name": "x",
                "entities": [
                    {"name": "A", "fields": [{"name": "b_id", "references": "B"}]},
                    {"name": "B", "fields": [{"name": "a_id", "references": "A"}]},
                ],
            }
        )

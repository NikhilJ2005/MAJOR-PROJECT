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


def test_roles_are_cleaned_and_admin_means_admin_emails():
    spec = ProjectSpec.model_validate({
        "project_name": "market", "auth": True,
        "roles": [{"name": "Client"}, {"name": "Freelancer"}, {"name": "Super_Admin"}, {"name": "client"}],
        "entities": [
            {"name": "Job", "write_roles": ["client", "nobody"]},
            {"name": "Fee", "write_roles": ["super_admin"]},
            {"name": "Note", "write_roles": ["client", "freelancer"]},
        ],
        "actions": [{"name": "close", "entity": "Job", "path": "/{item_id}/close", "roles": ["Admin"]}],
    })
    assert spec.role_names == ["client", "freelancer"]  # duplicates and super admins dropped
    assert spec.default_role == "client"
    jobs, fees, notes = (spec.entity(n) for n in ("Job", "Fee", "Note"))
    assert jobs.write_roles == ["client"]
    assert fees.write_access == "admin" and fees.write_roles == []
    assert notes.write_roles == []  # every role may write: nothing to check
    assert spec.actions[0].admin_only and spec.actions[0].roles == []


def test_features_need_logins_and_own_their_tables():
    spec = ProjectSpec.model_validate({
        "project_name": "x", "features": ["audit_log", "Rate Limit", "search"], "roles": [{"name": "a"}],
        "entities": [{"name": "Item"}],
    })
    assert spec.features == ["rate_limit", "search"] and spec.roles == []
    spec = ProjectSpec.model_validate({
        "project_name": "x", "auth": True, "features": ["audit_log"], "entities": [{"name": "AuditLog"}, {"name": "Item"}],
    })
    assert [e.name for e in spec.entities] == ["Item"]

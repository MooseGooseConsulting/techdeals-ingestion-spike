from datetime import UTC, datetime

from techdeals_ingestion.contracts import CaptureInput, WorkPackage, prepare_units
from techdeals_ingestion.storage import _meaning, _rebind, semantic_key


def test_reuse_key_excludes_acquisition_but_preserves_context(tmp_path):
    path = tmp_path / "source"
    path.write_text("one")

    def package(cid, acquisition, date, namespace="shop"):
        return WorkPackage(
            package_id=cid,
            captures=[
                CaptureInput(
                    capture_id=cid,
                    acquisition_key=acquisition,
                    path=path,
                    media_type="text/plain",
                    retrieved_at=date,
                    source_namespace=namespace,
                )
            ],
        )

    first = package("a", "fetch-a", None)
    second = package("b", "fetch-b", datetime.now(UTC))
    key = lambda p, fp="parser1": semantic_key(p, prepare_units(p)[0], fp)
    assert key(first) == key(second)
    assert key(first) != key(second, "parser2")
    assert key(first) != key(package("c", "fetch-c", None, "other-shop"))
    path.write_text("two")
    assert key(second) != semantic_key_from_original(first, tmp_path)


def semantic_key_from_original(package, tmp_path):
    old = tmp_path / "old"
    old.write_text("one")
    return semantic_key(
        package.model_copy(
            update={"captures": [package.captures[0].model_copy(update={"path": old})]}
        ),
        prepare_units(package)[0],
        "parser1",
    )


def test_rebinding_only_changes_evidence_capture_ids():
    value = {"title": "old", "evidence": [{"capture_id": "old", "locator": "$"}]}
    assert _rebind(value, {"old": "new"}) == {
        "title": "old",
        "evidence": [{"capture_id": "new", "locator": "$"}],
    }

    native = {"capture_id": "old", "locator": "native locator"}
    fact = {"value": native, "evidence": [{"capture_id": "old", "locator": "$"}]}
    rebound = _rebind(fact, {"old": "new"})
    assert rebound["value"] == native
    assert rebound["evidence"] == [{"capture_id": "new", "locator": "$"}]


def test_source_date_changes_affect_meaning_but_evidence_ids_do_not():
    from techdeals_ingestion.storage import _meaning

    first = {
        "source_dates": [
            {
                "value": "2026-10-20",
                "meaning": "auction end",
                "evidence": [{"capture_id": "a", "locator": "$"}],
            }
        ]
    }
    later = {
        "source_dates": [
            {
                "value": "2026-10-21",
                "meaning": "auction end",
                "evidence": [{"capture_id": "b", "locator": "$"}],
            }
        ]
    }
    assert _meaning(first) != _meaning(later)
    rebound = _rebind(first, {"a": "b"})
    assert _meaning(first) == _meaning(rebound)


def test_meaning_normalizes_local_graph_ids_but_preserves_scope_and_relationships():
    from techdeals_ingestion.contracts import Item, Offer, Price, Relationship
    from techdeals_ingestion.storage import _meaning

    def offer(prefix, reverse=False, price_target="cpu"):
        items = [
            Item(local_id=prefix + name, title=name, role="offered") for name in ["cpu", "ram"]
        ]
        return Offer(
            local_id=prefix + "offer",
            items=list(reversed(items)) if reverse else items,
            prices=[
                Price(
                    local_id=prefix + "price",
                    amount="10",
                    kind="ask",
                    scope_refs=[prefix + price_target],
                )
            ],
            relations=[
                Relationship(
                    subject_ref=prefix + "cpu", predicate="includes", object_ref=prefix + "ram"
                )
            ],
        ).model_dump(mode="json")

    first = offer("first-")
    renamed = offer("second-", reverse=True)
    assert _meaning(first) == _meaning(renamed)
    first["relations"][0]["object_ref"] = "first-price"
    renamed["relations"][0]["object_ref"] = "second-price"
    assert _meaning(first) == _meaning(renamed)
    assert _meaning(first) != _meaning(offer("third-", price_target="ram"))
    renamed["relations"][0]["object_ref"] = "second-offer"
    assert _meaning(first) != _meaning(renamed)


def test_meaning_preserves_native_json_and_identical_item_self_relationships():
    import copy

    from techdeals_ingestion.contracts import Fact, Item, Offer, Relationship

    data = {"local_id": "native-identifier", "evidence": "native-source-value"}
    fact = Fact(name="native", value=data).model_dump(mode="json")
    assert _meaning(fact)["value"] == data
    offer = Offer(
        local_id="offer",
        items=[Item(local_id=name, title="identical", role="included") for name in ("a", "b")],
        relations=[Relationship(subject_ref="a", predicate="paired_with", object_ref="b")],
    ).model_dump(mode="json")
    self_linked = copy.deepcopy(offer)
    self_linked["relations"][0]["object_ref"] = "a"
    assert _meaning(offer) != _meaning(self_linked)

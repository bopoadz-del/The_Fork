# tests/test_safety_classes.py
from app.blocks.safety_classes import (
    load_class_registry, get_active_classes, get_class_by_id, get_class_by_name,
    validate_registry, ClassEntry,
)
import pytest


def test_registry_loads_all_33_classes():
    entries = load_class_registry()
    assert len(entries) == 33
    assert entries[0].id == 0


def test_all_ids_unique():
    entries = load_class_registry()
    ids = [e.id for e in entries]
    assert len(set(ids)) == len(ids)


def test_all_names_unique():
    entries = load_class_registry()
    names = [e.name for e in entries]
    assert len(set(names)) == len(names)


def test_get_active_classes_returns_only_active():
    entries = get_active_classes()
    assert all(e.active for e in entries)
    assert all(e.weights_version for e in entries)


def test_get_class_by_id_known():
    e = get_class_by_id(0)
    assert e.name == "no_hardhat"


def test_get_class_by_id_unknown_raises():
    with pytest.raises(KeyError):
        get_class_by_id(999)


def test_get_class_by_name_known():
    e = get_class_by_name("concrete_crack")
    assert e.category == "qaqc"


def test_validate_rejects_duplicate_id():
    entries = [
        ClassEntry(id=0, name="a", category="safety", definition="", active=False, weights_version=None, min_examples_required=30),
        ClassEntry(id=0, name="b", category="safety", definition="", active=False, weights_version=None, min_examples_required=30),
    ]
    with pytest.raises(ValueError, match="duplicate id"):
        validate_registry(entries)


def test_validate_rejects_active_without_weights():
    entries = [
        ClassEntry(id=0, name="a", category="safety", definition="", active=True, weights_version=None, min_examples_required=30),
    ]
    with pytest.raises(ValueError, match="active.*weights"):
        validate_registry(entries)

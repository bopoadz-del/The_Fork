"""The block registry loads resiliently — one broken block module does not
abort the whole registry (and therefore the app)."""

from app.blocks import BLOCK_REGISTRY, FAILED_BLOCKS, _load_blocks


def test_registry_loads_and_is_populated():
    """A healthy install loads every block with no failures."""
    assert len(BLOCK_REGISTRY) >= 30
    assert "chat" in BLOCK_REGISTRY and "pdf" in BLOCK_REGISTRY
    assert FAILED_BLOCKS == {}, f"blocks failed to load: {FAILED_BLOCKS}"


def test_loader_isolates_a_broken_block():
    """A spec whose module cannot be imported is recorded in `failed` and the
    other blocks still load — the loader does not raise."""
    registry, failed = _load_blocks([
        ("good", "app.blocks.translate", "TranslateBlock"),
        ("broken_module", "app.blocks.does_not_exist_xyz", "Nope"),
        ("broken_class", "app.blocks.translate", "ClassThatDoesNotExist"),
        ("also_good", "app.blocks.vector_search", "VectorSearchBlock"),
    ])

    # The good blocks loaded.
    assert "good" in registry and "also_good" in registry
    # The broken ones were isolated, not raised.
    assert "broken_module" in failed
    assert "broken_class" in failed
    assert "broken_module" not in registry
    assert "broken_class" not in registry


def test_class_reexports_still_work():
    """`from app.blocks import XBlock` keeps working for loaded blocks."""
    from app.blocks import ChatBlock, LocalDriveBlock, VectorSearchBlock

    assert ChatBlock is BLOCK_REGISTRY["chat"]
    assert LocalDriveBlock is BLOCK_REGISTRY["local_drive"]
    assert VectorSearchBlock is BLOCK_REGISTRY["vector_search"]

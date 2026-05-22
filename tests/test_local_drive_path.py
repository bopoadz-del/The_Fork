"""local_drive: the listing directory is honored from the `path` param (not
only `folder_path` or input_data), and a non-directory yields an honest error
instead of a silent empty-but-successful result."""

from app.blocks.local_drive import LocalDriveBlock


async def test_list_honors_path_param(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")

    result = await LocalDriveBlock().process(
        None, {"operation": "list", "path": str(tmp_path)}
    )

    assert result["status"] == "success", result
    assert result["path"] == str(tmp_path)
    assert set(result["files"]) == {"a.txt", "b.txt"}


async def test_list_honors_folder_path_param(tmp_path):
    (tmp_path / "only.txt").write_text("z")

    result = await LocalDriveBlock().process(
        None, {"operation": "list", "folder_path": str(tmp_path)}
    )

    assert result["status"] == "success", result
    assert result["files"] == ["only.txt"]


async def test_list_nonexistent_path_returns_error(tmp_path):
    missing = str(tmp_path / "does-not-exist")

    result = await LocalDriveBlock().process(
        None, {"operation": "list", "path": missing}
    )

    assert result["status"] == "error", result
    assert "Not a directory" in result["error"]
    assert result["path"] == missing

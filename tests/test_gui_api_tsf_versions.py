from types import SimpleNamespace

from src.gui_api import tsf_versions


def test_tsf_inspection_adapter_serializes_existing_inspector(monkeypatch):
    latest = SimpleNamespace(
        version="20260809-latest",
    )
    loaded = [
        SimpleNamespace(
            pid=10,
            process_name="latest.exe",
            status="latest",
        ),
        SimpleNamespace(
            pid=11,
            process_name="LATEST.EXE",
            status="latest",
        ),
        SimpleNamespace(
            pid=20,
            process_name="old.exe",
            status="old",
        ),
    ]
    monkeypatch.setattr(tsf_versions, "find_latest_deployment", lambda: latest)
    monkeypatch.setattr(
        tsf_versions,
        "iter_loaded_dlls",
        lambda _latest: (loaded, ["PID 30: access denied"]),
    )

    result = tsf_versions.inspect_tsf_dll_versions()

    assert result["latest"]["version"] == "20260809-latest"
    assert len(result["hosts"]) == 2
    assert result["hosts"][1]["status"] == "old"
    assert result["latest_count"] == 1
    assert result["old_count"] == 1
    assert result["unknown_count"] == 0
    assert result["warnings"] == ["PID 30: access denied"]

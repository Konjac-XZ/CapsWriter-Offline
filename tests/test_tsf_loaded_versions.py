import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1] / "native" / "tsf_speech_tip" / "check_loaded_versions.py"
)


def _load_inspector():
    spec = importlib.util.spec_from_file_location("tsf_loaded_versions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_deployment(root: Path, version: str, x64: bytes, x86: bytes) -> Path:
    deployment = root / version
    for architecture, content in (("x64", x64), ("x86", x86)):
        target = deployment / architecture / "CapsWriterSpeechTip.dll"
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
    return deployment


def test_latest_deployment_uses_newest_complete_version(tmp_path):
    inspector = _load_inspector()
    _write_deployment(tmp_path, "20260804-100000-old-1", b"old64", b"old86")
    latest_root = _write_deployment(
        tmp_path, "20260804-110000-latest-2", b"latest64", b"latest86"
    )
    incomplete = tmp_path / "20260804-120000-incomplete-3" / "x64"
    incomplete.mkdir(parents=True)
    (incomplete / inspector.DLL_NAME).write_bytes(b"incomplete")

    latest = inspector.find_latest_deployment(tmp_path)

    assert latest.version == "20260804-110000-latest-2"
    assert latest.root == latest_root
    assert latest.hashes["x64"] == inspector.file_sha256(
        latest_root / "x64" / inspector.DLL_NAME
    )


def test_loaded_dll_is_classified_by_content_not_only_path(tmp_path):
    inspector = _load_inspector()
    versions_dir = tmp_path / "versions"
    latest_root = _write_deployment(
        versions_dir, "20260804-110000-latest-2", b"latest64", b"latest86"
    )
    latest = inspector.find_latest_deployment(versions_dir)
    copied_latest = tmp_path / "outside" / "x64" / inspector.DLL_NAME
    copied_latest.parent.mkdir(parents=True)
    copied_latest.write_bytes((latest_root / "x64" / inspector.DLL_NAME).read_bytes())

    loaded = inspector.classify_loaded_dll(
        pid=123,
        process_name="Editor.exe",
        path=copied_latest,
        latest=latest,
        versions_dir=versions_dir,
    )

    assert loaded.status == "latest"
    assert loaded.version == "external"
    assert loaded.architecture == "x64"


def test_old_deployment_reports_its_version(tmp_path):
    inspector = _load_inspector()
    old_root = _write_deployment(tmp_path, "20260804-100000-old-1", b"old64", b"old86")
    _write_deployment(tmp_path, "20260804-110000-latest-2", b"latest64", b"latest86")
    latest = inspector.find_latest_deployment(tmp_path)

    loaded = inspector.classify_loaded_dll(
        pid=456,
        process_name="OldEditor.exe",
        path=old_root / "x64" / inspector.DLL_NAME,
        latest=latest,
        versions_dir=tmp_path,
    )

    assert loaded.status == "old"
    assert loaded.version == "20260804-100000-old-1"
    assert loaded.architecture == "x64"


def test_tasklist_parser_keeps_only_process_rows():
    inspector = _load_inspector()
    output = "\n".join(
        [
            '"ChatGPT.exe","34788","Console","1","200,000 K","CapsWriterSpeechTip.dll"',
            '"Obsidian.exe","2944","Console","1","300,000 K","CapsWriterSpeechTip.dll"',
            "INFO: No tasks are running which match the specified criteria.",
            '"invalid.exe","not-a-pid"',
        ]
    )

    assert inspector.parse_tasklist_hosts(output) == [
        (34788, "ChatGPT.exe"),
        (2944, "Obsidian.exe"),
    ]


def test_process_report_prints_only_unique_process_names(capsys, tmp_path):
    inspector = _load_inspector()
    loaded = [
        inspector.LoadedDll(
            pid=1,
            process_name="Editor.exe",
            path=tmp_path / "old-a.dll",
            architecture="x64",
            version="old-a",
            sha256="a",
            status="old",
        ),
        inspector.LoadedDll(
            pid=2,
            process_name="Editor.exe",
            path=tmp_path / "old-b.dll",
            architecture="x64",
            version="old-b",
            sha256="b",
            status="old",
        ),
    ]

    inspector._print_processes("Old", loaded)

    assert capsys.readouterr().out == "Old (1):\n  Editor.exe\n"

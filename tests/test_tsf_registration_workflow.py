import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1] / "native" / "tsf_speech_tip" / "manage_registration.py"
)


def _load_workflow():
    spec = importlib.util.spec_from_file_location("tsf_registration_workflow", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_workflow_covers_both_release_architectures_and_ctest():
    workflow = _load_workflow()

    commands = [
        workflow.command_text(command) for command in workflow.build_and_test_commands()
    ]

    assert any("--preset vs2022-x64" in command for command in commands)
    assert any("--preset x64-release" in command for command in commands)
    assert any("--test-dir build-x64 -C Release" in command for command in commands)
    assert any("--preset vs2022-x86" in command for command in commands)
    assert any("--preset x86-release" in command for command in commands)
    assert any("--test-dir build-x86 -C Release" in command for command in commands)


def test_regsvr32_commands_use_the_matching_windows_architecture():
    workflow = _load_workflow()
    x64, x86 = workflow.architectures()
    deployed = workflow.deployment_paths("test-version")

    x64_command = workflow.command_text(workflow.regsvr32_command(x64, deployed["x64"]))
    x86_command = workflow.command_text(
        workflow.regsvr32_command(x86, deployed["x86"], unregister=True)
    )

    assert r"System32\regsvr32.exe" in x64_command
    assert r"installed\versions\test-version\x64\CapsWriterSpeechTip.dll" in x64_command
    assert r"SysWOW64\regsvr32.exe" in x86_command
    assert " /u " in x86_command
    assert r"installed\versions\test-version\x86\CapsWriterSpeechTip.dll" in x86_command


def test_deployment_paths_keep_architectures_in_one_immutable_version():
    workflow = _load_workflow()

    deployed = workflow.deployment_paths("20260803-test")

    assert deployed["x64"].parent.name == "x64"
    assert deployed["x86"].parent.name == "x86"
    assert deployed["x64"].parents[1] == deployed["x86"].parents[1]
    assert deployed["x64"].parents[1].name == "20260803-test"


def test_loaded_hosts_are_informational_and_do_not_block(monkeypatch, capsys):
    workflow = _load_workflow()
    monkeypatch.setattr(
        workflow,
        "loaded_hosts",
        lambda: ["explorer.exe, 1234", "ExampleEditor.exe, 5678"],
    )

    workflow.report_loaded_hosts()

    output = capsys.readouterr().out
    assert "keep using their already-loaded DLL" in output
    assert "registration will point new processes at the new version" in output
    assert "explorer.exe, 1234" in output
    assert "Close every process" not in output


def test_mutating_registration_requires_elevation_before_reading_state(monkeypatch):
    workflow = _load_workflow()
    monkeypatch.setattr(workflow.ctypes.windll.shell32, "IsUserAnAdmin", lambda: False)
    monkeypatch.setattr(
        workflow,
        "registration_states",
        lambda: (_ for _ in ()).throw(AssertionError("registration was read")),
    )

    with pytest.raises(workflow.WorkflowError, match="Administrator privileges"):
        workflow.install(skip_build=True, dry_run=False)


def test_install_dry_run_is_read_only_and_lists_verification_step():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "install", "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--preset x64-release" in result.stdout
    assert "--preset x86-release" in result.stdout
    assert "sign.cmd" in result.stdout
    assert r"installed\versions\<version-id>\x64" in result.stdout
    assert r"installed\versions\<version-id>\x86" in result.stdout
    assert "verify x64/x86 HKCU COM paths and the TSF language profile" in result.stdout
    assert "Close every process" not in result.stdout
    assert "Close every process" not in result.stderr

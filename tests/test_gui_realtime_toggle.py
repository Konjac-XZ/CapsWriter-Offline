from types import SimpleNamespace
from typing import cast

from PySide6.QtWidgets import QApplication, QCheckBox

from start_client_gui import GUI, _supports_realtime_toggle


class _ProviderManager:
    def __init__(self, provider):
        self.provider = provider
        self.updates = []

    def get_provider(self, provider_id):
        return self.provider if provider_id == "bytedance" else None

    def update_provider_setting(self, provider_id, key, value):
        self.updates.append((provider_id, key, value))
        self.provider.settings[key] = value
        return True


class _ProviderCombo:
    def currentData(self):
        return "bytedance"


def test_realtime_toggle_supports_bytedance_and_qwen_only():
    for provider_type in (
        "bytedance",
        "qwen-audio",
        "qwen-audio-legacy",
        "dashscope",
    ):
        assert _supports_realtime_toggle(SimpleNamespace(type=provider_type)) is True
    assert _supports_realtime_toggle(SimpleNamespace(type="xiaomi")) is False


def test_bytedance_realtime_checkbox_is_visible_and_synced():
    app = QApplication.instance() or QApplication([])
    provider = SimpleNamespace(type="bytedance", settings={"realtime": True})
    owner = SimpleNamespace(
        provider_manager=_ProviderManager(provider),
        provider_combo=_ProviderCombo(),
        asr_realtime_checkbox=QCheckBox("流式音频"),
    )

    GUI.sync_asr_realtime_control(cast(GUI, owner))

    assert owner.asr_realtime_checkbox.isChecked() is True
    assert owner.asr_realtime_checkbox.isHidden() is False
    owner.asr_realtime_checkbox.deleteLater()
    app.processEvents()


def test_bytedance_realtime_toggle_persists_and_restarts_workers():
    provider = SimpleNamespace(type="bytedance", settings={"realtime": True})
    manager = _ProviderManager(provider)
    messages = []
    restarts = []
    owner = SimpleNamespace(
        provider_manager=manager,
        provider_combo=_ProviderCombo(),
        append_colored_line=lambda *args: messages.append(args),
        restart_children_with_env=lambda: restarts.append(True),
    )

    GUI.on_asr_realtime_toggled(cast(GUI, owner), False)

    assert manager.updates == [("bytedance", "realtime", False)]
    assert provider.settings["realtime"] is False
    assert messages == [("语音识别已切换为录音文件模式",)]
    assert restarts == [True]

from types import SimpleNamespace
from typing import cast

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox

from src.provider.domain import InputMode, ModelRef
from start_client_gui import GUI


class _ModelCombo:
    def __init__(self, ref: ModelRef):
        self.ref = ref

    def currentData(self):
        return self.ref


class _ProviderManager:
    def __init__(self, modes, selected=InputMode.FILE_UPLOAD):
        self.model = SimpleNamespace(input_modes=frozenset(modes))
        self.selected = selected
        self.updates = []

    def get_model(self, ref):
        return self.model if ref == ModelRef("provider", "model") else None

    def get_model_mode(self, ref):
        assert ref == ModelRef("provider", "model")
        return self.selected

    def set_model_mode(self, ref, mode):
        self.updates.append((ref, mode))
        self.selected = mode
        return True


def test_realtime_checkbox_visibility_is_driven_by_model_capabilities():
    app = QApplication.instance() or QApplication([])
    ref = ModelRef("provider", "model")
    manager = _ProviderManager(
        {InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}, InputMode.LIVE_AUDIO
    )
    owner = SimpleNamespace(
        provider_manager=manager,
        model_combo=_ModelCombo(ref),
        asr_realtime_checkbox=QCheckBox("流式音频"),
    )

    GUI.sync_asr_realtime_control(cast(GUI, owner))

    assert owner.asr_realtime_checkbox.isHidden() is False
    assert owner.asr_realtime_checkbox.isChecked() is True
    owner.asr_realtime_checkbox.deleteLater()
    app.processEvents()


def test_single_mode_model_hides_realtime_checkbox():
    app = QApplication.instance() or QApplication([])
    ref = ModelRef("provider", "model")
    owner = SimpleNamespace(
        provider_manager=_ProviderManager({InputMode.FILE_UPLOAD}),
        model_combo=_ModelCombo(ref),
        asr_realtime_checkbox=QCheckBox("流式音频"),
    )

    GUI.sync_asr_realtime_control(cast(GUI, owner))

    assert owner.asr_realtime_checkbox.isHidden() is True
    owner.asr_realtime_checkbox.deleteLater()
    app.processEvents()


def test_realtime_toggle_persists_mode_for_selected_model():
    ref = ModelRef("provider", "model")
    manager = _ProviderManager({InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO})
    messages = []
    restarts = []
    owner = SimpleNamespace(
        provider_manager=manager,
        model_combo=_ModelCombo(ref),
        append_colored_line=lambda *args: messages.append(args),
        restart_children_with_env=lambda: restarts.append(True),
        sync_asr_realtime_control=lambda: None,
    )

    GUI.on_asr_realtime_toggled(cast(GUI, owner), True)

    assert manager.updates == [(ref, InputMode.LIVE_AUDIO)]
    assert messages == [("语音识别已切换为流式音频模式",)]
    assert restarts == [True]


def test_model_combo_keeps_duplicate_names_as_distinct_provider_refs():
    app = QApplication.instance() or QApplication([])
    alpha = ModelRef("alpha", "shared")
    beta = ModelRef("beta", "shared")
    manager = SimpleNamespace(
        list_models=lambda: [
            {"ref": alpha, "label": "Shared ASR · Alpha"},
            {"ref": beta, "label": "Shared ASR · Beta"},
        ],
        get_active_model_ref=lambda: beta,
    )
    combo = QComboBox()
    messages = []
    owner = SimpleNamespace(
        provider_manager=manager,
        model_combo=combo,
        log_message=lambda message: messages.append(message),
    )

    GUI.populate_model_combo(cast(GUI, owner))

    assert combo.count() == 2
    assert combo.itemData(0) == alpha
    assert combo.itemData(1) == beta
    assert combo.currentData() == beta
    assert messages == ["转录模型选择器已准备就绪，共 2 个选项"]
    combo.deleteLater()
    app.processEvents()

from types import SimpleNamespace
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication, QComboBox

from src.provider.domain import InputMode, ModelRef
from start_client_gui import AdaptivePopupComboBox, GUI


class _ModelCombo:
    def __init__(self, ref: ModelRef):
        self.ref = ref

    def currentData(self):
        return self.ref


class _CheckBox:
    def __init__(self) -> None:
        self._checked = False
        self._visible = False

    def blockSignals(self, _blocked: bool) -> None:
        pass

    def setChecked(self, checked: bool) -> None:
        self._checked = checked

    def setVisible(self, visible: bool) -> None:
        self._visible = visible

    def isChecked(self) -> bool:
        return self._checked

    def isHidden(self) -> bool:
        return not self._visible


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
    ref = ModelRef("provider", "model")
    manager = _ProviderManager(
        {InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}, InputMode.LIVE_AUDIO
    )
    owner = SimpleNamespace(
        provider_manager=manager,
        model_combo=_ModelCombo(ref),
        asr_realtime_checkbox=_CheckBox(),
    )

    GUI.sync_asr_realtime_control(cast(GUI, owner))

    assert owner.asr_realtime_checkbox.isHidden() is False
    assert owner.asr_realtime_checkbox.isChecked() is True


def test_single_mode_model_hides_realtime_checkbox():
    ref = ModelRef("provider", "model")
    owner = SimpleNamespace(
        provider_manager=_ProviderManager({InputMode.FILE_UPLOAD}),
        model_combo=_ModelCombo(ref),
        asr_realtime_checkbox=_CheckBox(),
    )

    GUI.sync_asr_realtime_control(cast(GUI, owner))

    assert owner.asr_realtime_checkbox.isHidden() is True


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


def test_model_combo_groups_sorts_and_keeps_distinct_provider_refs():
    app = QApplication.instance() or QApplication([])
    alpha = ModelRef("alpha", "shared")
    beta = ModelRef("beta", "shared")
    manager = SimpleNamespace(
        list_models=lambda: [
            {
                "ref": beta,
                "provider_id": "beta",
                "provider_name": "Beta Provider",
                "model_id": "shared",
                "name": "Shared ASR",
            },
            {
                "ref": ModelRef("alpha", "zulu"),
                "provider_id": "alpha",
                "provider_name": "Alpha Provider",
                "model_id": "zulu",
                "name": "Zulu ASR",
            },
            {
                "ref": alpha,
                "provider_id": "alpha",
                "provider_name": "Alpha Provider",
                "model_id": "shared",
                "name": "Shared ASR",
            },
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

    assert combo.count() == 5
    assert [combo.itemText(index) for index in range(combo.count())] == [
        "Alpha Provider",
        "    Shared ASR",
        "    Zulu ASR",
        "Beta Provider",
        "    Shared ASR",
    ]
    assert combo.itemData(0) is None
    assert combo.itemData(1) == alpha
    assert combo.itemData(2) == ModelRef("alpha", "zulu")
    assert combo.itemData(3) is None
    assert combo.itemData(4) == beta
    assert combo.currentData() == beta
    item_model = cast(QStandardItemModel, combo.model())
    assert item_model.item(0).isEnabled() is True
    assert not item_model.item(0).flags() & Qt.ItemFlag.ItemIsSelectable
    assert item_model.item(0).font().bold() is True
    assert item_model.item(3).isEnabled() is True
    assert not item_model.item(3).flags() & Qt.ItemFlag.ItemIsSelectable
    assert item_model.item(3).font().bold() is True
    assert messages == ["转录模型选择器已准备就绪，共 3 个选项"]
    combo.deleteLater()
    app.processEvents()


def test_model_combo_popup_uses_all_rows_when_space_allows():
    app = QApplication.instance() or QApplication([])
    combo = AdaptivePopupComboBox()
    combo.addItems(["Provider", "    Alpha", "    Beta", "    Gamma"])
    required_height = (
        sum(combo._popup_row_height(index) for index in range(combo.count())) + 10
    )

    visible_items = combo._fit_popup_to_available_height(required_height)

    assert visible_items == combo.count()
    assert combo.maxVisibleItems() == combo.count()
    combo.deleteLater()
    app.processEvents()


def test_model_combo_popup_scrolls_only_when_space_is_insufficient():
    app = QApplication.instance() or QApplication([])
    combo = AdaptivePopupComboBox()
    combo.addItems(["Provider", "    Alpha", "    Beta", "    Gamma"])
    one_row_height = combo._popup_row_height(0) + 4

    visible_items = combo._fit_popup_to_available_height(one_row_height)

    assert visible_items == 1
    assert combo.maxVisibleItems() == 1
    combo.deleteLater()
    app.processEvents()

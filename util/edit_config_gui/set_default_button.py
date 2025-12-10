from siui.components.button import (
    SiFlatButton,
)
from siui.core import SiGlobal


class SetDefaultButton(SiFlatButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSvgIcon(SiGlobal.siui.iconpack.get("ic_fluent_arrow_undo_regular"))
        self.setToolTip("恢复默认值")
        self.adjustSize()

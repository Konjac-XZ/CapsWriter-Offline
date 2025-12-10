import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from siui.components import (
    SiTitledWidgetGroup,
)
from siui.components.page import SiPage
from siui.components.titled_widget_group import SiTitledWidgetGroup

sys.path.append(str(Path().cwd()))
import sys
from pathlib import Path

from siui.components import (
    SiSimpleButton,
    SiTitledWidgetGroup,
)
from siui.core import SiGlobal

sys.path.append(str(Path().cwd()))
from siui.components import (
    SiDenseVContainer,
    SiOptionCardLinear,
)
from siui.components.option_card import SiOptionCardLinear
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.widgets import (
    SiDenseVContainer,
)


class AboutPage(SiPage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_ui()
        self.button_to_repo.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/H1DDENADM1N/CapsWriter-Offline")
            )
        )
        self.button_to_issues.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/H1DDENADM1N/CapsWriter-Offline/issues")
            )
        )

    def init_ui(self):
        self.setPadding(64)
        self.setScrollMaximumWidth(1000)
        self.setScrollAlignment(Qt.AlignLeft)
        self.setTitle("关于")

        # 创建控件组
        self.titled_widgets_group = SiTitledWidgetGroup(self)
        self.titled_widgets_group.setSpacing(32)
        self.titled_widgets_group.setAdjustWidgetsSize(True)

        # 创建控件
        with self.titled_widgets_group as group:
            group.addTitle("开源")

            # 项目主页
            self.button_to_repo = SiSimpleButton(self)
            self.button_to_repo.resize(32, 32)
            self.button_to_repo.attachment().load(
                SiGlobal.siui.iconpack.get("ic_fluent_open_regular")
            )
            self.option_card_repo = SiOptionCardLinear(self)
            self.option_card_repo.setTitle(
                "项目主页",
                "在 GitHub 上查看 CapsWriter-Offline 的项目主页\nhttps://github.com/H1DDENADM1N/CapsWriter-Offline",
            )
            self.option_card_repo.load(
                SiGlobal.siui.iconpack.get("ic_fluent_home_database_regular")
            )
            self.option_card_repo.addWidget(self.button_to_repo)

            # 反馈 Bug
            self.button_to_issues = SiSimpleButton(self)
            self.button_to_issues.resize(32, 32)
            self.button_to_issues.attachment().load(
                SiGlobal.siui.iconpack.get("ic_fluent_open_regular")
            )
            self.option_card_issues = SiOptionCardLinear(self)
            self.option_card_issues.setTitle(
                "反馈 Bug",
                "在 GitHub 上查看 CapsWriter-Offline 的 Issues 页\nhttps://github.com/H1DDENADM1N/CapsWriter-Offline/issues",
            )
            self.option_card_issues.load(
                SiGlobal.siui.iconpack.get("ic_fluent_bug_regular")
            )
            self.option_card_issues.addWidget(self.button_to_issues)

            # 设置项
            self.open_source_container = SiDenseVContainer(self)
            self.open_source_container.setFixedWidth(700)
            self.open_source_container.setAdjustWidgetsSize(True)
            self.open_source_container.addWidget(self.option_card_repo)
            self.open_source_container.addWidget(self.option_card_issues)
            group.addWidget(self.open_source_container)

        # 添加页脚的空白以增加美观性
        self.titled_widgets_group.addPlaceholder(64)

        # 设置控件组为页面对象
        self.setAttachment(self.titled_widgets_group)

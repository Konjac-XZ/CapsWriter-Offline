from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from siui.components import (
    SiDenseVContainer,
    SiLineEditWithDeletionButton,
    SiTitledWidgetGroup,
)
from siui.components.button import (
    SiLongPressButtonRefactor,
)
from siui.components.combobox import SiComboBox
from siui.components.option_card import SiOptionCardLinear
from siui.components.page import SiPage
from siui.components.spinbox.spinbox import SiIntSpinBox
from siui.components.titled_widget_group import SiTitledWidgetGroup
from siui.components.widgets import (
    SiDenseVContainer,
    SiSwitch,
)
from siui.core import SiGlobal

from .set_default_button import SetDefaultButton


class SenseVoiceArgsConfigPage(SiPage):
    def __init__(self, config, config_path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config
        self.config_path = config_path
        self.init_ui()
        self.num_threads_set_default.clicked.connect(
            lambda: self.num_threads.setValue(6)
        )
        self.feature_dim_set_default.clicked.connect(
            lambda: self.feature_dim.setValue(80)
        )
        self.decoding_method_set_default.clicked.connect(
            lambda: self.decoding_method.lineEdit().setText("greedy_search")
        )
        self.rule_fsts_set_default.clicked.connect(
            lambda: self.rule_fsts.lineEdit().setText("")
        )
        self.rule_fars_set_default.clicked.connect(
            lambda: self.rule_fars.lineEdit().setText("")
        )
        self.save.longPressed.connect(self.save_config)

    def init_ui(self):
        self.setPadding(64)
        self.setScrollMaximumWidth(1000)
        self.setScrollAlignment(Qt.AlignLeft)
        self.setTitle("SenseVoice 语音识别模型参数配置")

        # 创建控件组
        self.titled_widgets_group = SiTitledWidgetGroup(self)
        self.titled_widgets_group.setSpacing(32)
        self.titled_widgets_group.setAdjustWidgetsSize(True)

        # 保存配置按钮
        with self.titled_widgets_group as group:
            self.save = SiLongPressButtonRefactor(self)
            self.save.setSvgIcon(SiGlobal.siui.iconpack.get("ic_fluent_save_filled"))
            self.save.setIconSize(QSize(32, 32))
            self.save.setText("\t保存 SenseVoice 配置")
            self.save.setFont(QFont("Microsoft YaHei", 16))
            self.save.setToolTip(
                "点击按钮进行数据格式检查\n长按以确认将数据写入配置文件\n保存配置后请手动重启 服务端/客户端 以加载新配置生效"
            )
            self.save.resize(420, 64)
            self.save_container = SiDenseVContainer(self)
            self.save_container.setAlignment(Qt.AlignCenter)
            self.save_container.addWidget(self.save)
            group.addWidget(self.save_container)

        with self.titled_widgets_group as group:
            group.addTitle("通用")

            # 使用的线程数
            self.num_threads = SiIntSpinBox(self)
            self.num_threads.resize(256, 32)
            self.num_threads.setMinimum(1)
            self.num_threads.setMaximum(32)
            self.num_threads.setValue(self.config["sensevoice_args"]["num_threads"])
            self.num_threads_set_default = SetDefaultButton(self)
            self.num_threads_linear_attaching = SiOptionCardLinear(self)
            self.num_threads_linear_attaching.setTitle("使用的线程数", '默认值："6"')
            self.num_threads_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.num_threads_linear_attaching.addWidget(self.num_threads_set_default)
            self.num_threads_linear_attaching.addWidget(self.num_threads)

            # 采样率
            self.sample_rate = SiComboBox(self)
            self.sample_rate.resize(325, 32)
            self.sample_rate.addOption("16000")
            self.sample_rate.addOption("44100")
            self.sample_rate.addOption("48000")
            self.sample_rate.menu().setShowIcon(False)
            match str(self.config["sensevoice_args"]["sample_rate"]):
                case "16000":
                    self.sample_rate.menu().setIndex(0)
                case "44100":
                    self.sample_rate.menu().setIndex(1)
                case "48000":
                    self.sample_rate.menu().setIndex(2)
                case _:
                    self.sample_rate.menu().setIndex(0)
            self.sample_rate_linear_attaching = SiOptionCardLinear(self)
            self.sample_rate_linear_attaching.setTitle("采样率", '默认值："16000"')
            self.sample_rate_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.sample_rate_linear_attaching.addWidget(self.sample_rate)

            # 特征维度
            self.feature_dim = SiIntSpinBox(self)
            self.feature_dim.resize(256, 32)
            self.feature_dim.setMinimum(1)
            self.feature_dim.setMaximum(1024)
            self.feature_dim.setValue(self.config["sensevoice_args"]["feature_dim"])
            self.feature_dim_set_default = SetDefaultButton(self)
            self.feature_dim_linear_attaching = SiOptionCardLinear(self)
            self.feature_dim_linear_attaching.setTitle("特征维度", '默认值："80"')
            self.feature_dim_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.feature_dim_linear_attaching.addWidget(self.feature_dim_set_default)
            self.feature_dim_linear_attaching.addWidget(self.feature_dim)

            # 解码方法
            self.decoding_method = SiLineEditWithDeletionButton(self)
            self.decoding_method.resize(256, 32)
            self.decoding_method.lineEdit().setText(
                self.config["sensevoice_args"]["decoding_method"]
            )
            self.decoding_method_set_default = SetDefaultButton(self)
            self.decoding_method_linear_attaching = SiOptionCardLinear(self)
            self.decoding_method_linear_attaching.setTitle(
                "解码方法", '默认值："greedy_search"'
            )
            self.decoding_method_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.decoding_method_linear_attaching.addWidget(
                self.decoding_method_set_default
            )
            self.decoding_method_linear_attaching.addWidget(self.decoding_method)

            # 是否启用调试模式
            self.debug = SiSwitch(self)
            self.debug.setChecked(self.config["sensevoice_args"]["debug"])
            self.debug_linear_attaching = SiOptionCardLinear(self)
            self.debug_linear_attaching.setTitle("是否启用调试模式")
            self.debug_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_bug_regular")
            )
            self.debug_linear_attaching.addWidget(self.debug)

            # 推理设备（cpu, cuda, coreml）
            self.provider = SiComboBox(self)
            self.provider.resize(325, 32)
            self.provider.addOption("cpu")
            self.provider.addOption("cuda")
            self.provider.addOption("coreml")
            self.provider.menu().setShowIcon(False)
            match str(self.config["sensevoice_args"]["provider"]):
                case "cpu":
                    self.provider.menu().setIndex(0)
                case "cuda":
                    self.provider.menu().setIndex(1)
                case "coreml":
                    self.provider.menu().setIndex(2)
                case _:
                    self.provider.menu().setIndex(0)
            self.provider_linear_attaching = SiOptionCardLinear(self)
            self.provider_linear_attaching.setTitle("推理设备", '默认值："cpu"')
            self.provider_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.provider_linear_attaching.addWidget(self.provider)

            # 识别语言（auto, zh, en, ja, ko, yue）
            self.language = SiComboBox(self)
            self.language.resize(325, 32)
            self.language.addOption("auto")
            self.language.addOption("zh")
            self.language.addOption("en")
            self.language.addOption("ja")
            self.language.addOption("ko")
            self.language.addOption("yue")
            self.language.menu().setShowIcon(False)
            match str(self.config["sensevoice_args"]["language"]):
                case "auto":
                    self.language.menu().setIndex(0)
                case "zh":
                    self.language.menu().setIndex(1)
                case "en":
                    self.language.menu().setIndex(2)
                case "ja":
                    self.language.menu().setIndex(3)
                case "ko":
                    self.language.menu().setIndex(4)
                case "yue":
                    self.language.menu().setIndex(5)
                case _:
                    self.language.menu().setIndex(0)
            self.language_linear_attaching = SiOptionCardLinear(self)
            self.language_linear_attaching.setTitle("识别语言", '默认值："auto"')
            self.language_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.language_linear_attaching.addWidget(self.language)

            # 是否使用逆文本归一化
            self.use_itn = SiSwitch(self)
            self.use_itn.setChecked(self.config["sensevoice_args"]["use_itn"])
            self.use_itn_linear_attaching = SiOptionCardLinear(self)
            self.use_itn_linear_attaching.setTitle("是否使用逆文本归一化")
            self.use_itn_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.use_itn_linear_attaching.addWidget(self.use_itn)

            # 自定义规则 FST 文件路径
            self.rule_fsts = SiLineEditWithDeletionButton(self)
            self.rule_fsts.resize(256, 32)
            self.rule_fsts.lineEdit().setText(
                self.config["sensevoice_args"]["rule_fsts"]
            )
            self.rule_fsts_set_default = SetDefaultButton(self)
            self.rule_fsts_linear_attaching = SiOptionCardLinear(self)
            self.rule_fsts_linear_attaching.setTitle(
                "自定义规则 FST 文件路径", "默认值为空"
            )
            self.rule_fsts_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.rule_fsts_linear_attaching.addWidget(self.rule_fsts_set_default)
            self.rule_fsts_linear_attaching.addWidget(self.rule_fsts)

            # 自定义规则 FAR 文件路径
            self.rule_fars = SiLineEditWithDeletionButton(self)
            self.rule_fars.resize(256, 32)
            self.rule_fars.lineEdit().setText(
                self.config["sensevoice_args"]["rule_fars"]
            )
            self.rule_fars_set_default = SetDefaultButton(self)
            self.rule_fars_linear_attaching = SiOptionCardLinear(self)
            self.rule_fars_linear_attaching.setTitle(
                "自定义规则 FAR 文件路径", "默认值为空"
            )
            self.rule_fars_linear_attaching.load(
                SiGlobal.siui.iconpack.get("ic_fluent_settings_light")
            )
            self.rule_fars_linear_attaching.addWidget(self.rule_fars_set_default)
            self.rule_fars_linear_attaching.addWidget(self.rule_fars)

            # 设置项
            self.general_container = SiDenseVContainer(self)
            self.general_container.setFixedWidth(700)
            self.general_container.setAdjustWidgetsSize(True)
            self.general_container.addWidget(self.num_threads_linear_attaching)
            self.general_container.addWidget(self.sample_rate_linear_attaching)
            self.general_container.addWidget(self.feature_dim_linear_attaching)
            self.general_container.addWidget(self.decoding_method_linear_attaching)
            self.general_container.addWidget(self.debug_linear_attaching)
            self.general_container.addWidget(self.provider_linear_attaching)
            self.general_container.addWidget(self.language_linear_attaching)
            self.general_container.addWidget(self.use_itn_linear_attaching)
            self.general_container.addWidget(self.rule_fsts_linear_attaching)
            self.general_container.addWidget(self.rule_fars_linear_attaching)

            group.addWidget(self.general_container)
        # 添加页脚的空白以增加美观性
        self.titled_widgets_group.addPlaceholder(64)

        # 设置控件组为页面对象
        self.setAttachment(self.titled_widgets_group)

    def save_config(self):
        def get_value_from_gui():
            self.config["sensevoice_args"]["num_threads"] = self.num_threads.value()
            self.config["sensevoice_args"]["sample_rate"] = int(
                self.sample_rate.value_label.text()
            )
            self.config["sensevoice_args"]["feature_dim"] = self.feature_dim.value()
            self.config["sensevoice_args"]["decoding_method"] = (
                self.decoding_method.line_edit.text()
            )
            self.config["sensevoice_args"]["debug"] = self.debug.isChecked()
            self.config["sensevoice_args"]["provider"] = (
                self.provider.value_label.text()
            )
            self.config["sensevoice_args"]["language"] = (
                self.language.value_label.text()
            )
            self.config["sensevoice_args"]["use_itn"] = self.use_itn.isChecked()
            self.config["sensevoice_args"]["rule_fsts"] = (
                self.rule_fsts.line_edit.text()
            )
            self.config["sensevoice_args"]["rule_fars"] = (
                self.rule_fars.line_edit.text()
            )

        def print_config():
            from rich.console import Console
            from rich.table import Table

            from util.edit_config_gui.clearly_type import clearly_type

            console = Console()
            table = Table(title="保存 SenseVoice 语音识别模型参数配置")
            table.add_column("属性名", style="cyan")
            table.add_column("类型", style="magenta")
            table.add_column("值", style="green")
            table.add_row(
                "num_threads",
                clearly_type(self.config["sensevoice_args"]["num_threads"]),
                str(self.config["sensevoice_args"]["num_threads"]),
            )
            table.add_row(
                "sample_rate",
                clearly_type(self.config["sensevoice_args"]["sample_rate"]),
                str(self.config["sensevoice_args"]["sample_rate"]),
            )
            table.add_row(
                "feature_dim",
                clearly_type(self.config["sensevoice_args"]["feature_dim"]),
                str(self.config["sensevoice_args"]["feature_dim"]),
            )
            table.add_row(
                "decoding_method",
                clearly_type(self.config["sensevoice_args"]["decoding_method"]),
                str(self.config["sensevoice_args"]["decoding_method"]),
            )
            table.add_row(
                "debug",
                clearly_type(self.config["sensevoice_args"]["debug"]),
                str(self.config["sensevoice_args"]["debug"]),
            )
            table.add_row(
                "provider",
                clearly_type(self.config["sensevoice_args"]["provider"]),
                str(self.config["sensevoice_args"]["provider"]),
            )
            table.add_row(
                "language",
                clearly_type(self.config["sensevoice_args"]["language"]),
                str(self.config["sensevoice_args"]["language"]),
            )
            table.add_row(
                "use_itn",
                clearly_type(self.config["sensevoice_args"]["use_itn"]),
                str(self.config["sensevoice_args"]["use_itn"]),
            )
            table.add_row(
                "rule_fsts",
                clearly_type(self.config["sensevoice_args"]["rule_fsts"]),
                str(self.config["sensevoice_args"]["rule_fsts"]),
            )
            table.add_row(
                "rule_fars",
                clearly_type(self.config["sensevoice_args"]["rule_fars"]),
                str(self.config["sensevoice_args"]["rule_fars"]),
            )
            console.print(table)

        from siui.core import SiGlobal

        from util.edit_config_gui.write_toml import write_toml

        try:
            get_value_from_gui()
            print_config()
            write_toml(self.config, self.config_path)
            SiGlobal.siui.windows["MAIN_WINDOW"].LayerRightMessageSidebar().send(
                "保存 SenseVoice 配置成功！\n手动重启服务端以加载新配置。",
                msg_type=1,
                fold_after=2000,
            )
        except Exception as e:
            SiGlobal.siui.windows["MAIN_WINDOW"].LayerRightMessageSidebar().send(
                f"保存 SenseVoice 配置失败！\n错误信息：{e}",
                msg_type=4,
            )

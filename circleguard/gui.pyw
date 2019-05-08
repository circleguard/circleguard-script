import sys
import os
from pathlib import Path
from multiprocessing.pool import ThreadPool
from queue import Queue, Empty
from functools import partial
import pathlib
import logging

# pylint: disable=no-name-in-module
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QWidget, QTabWidget, QTextEdit, QPushButton, QLabel,
                             QVBoxLayout, QShortcut, QGridLayout, QApplication, QMainWindow)
from PyQt5.QtGui import QPalette, QColor, QIcon, QKeySequence, QTextCursor
# pylint: enable=no-name-in-module

from circleguard import Circleguard, set_options
from circleguard import __version__ as cg_version

from widgets import (Threshold, set_event_window, InputWidget,
                     FolderChooser, SpinBox, IdWidgetCombined,
                     OptionWidget, CompareTopPlays, CompareTopUsers, ThresholdCombined, LoglevelWidget)
from settings import THRESHOLD, API_KEY, DARK_THEME, CACHING, update_default, CACHE_DIR

ROOT_PATH = pathlib.Path(__file__).parent.absolute()
__version__ = "0.1d"

log = logging.getLogger(__name__)


def resource_path(str_path):
    """
    Returns a Path representing where to look for resource files for the program,
    such as databases or images.

    This location changes if the program is run from an application built with pyinstaller.
    """

    if hasattr(sys, '_MEIPASS'):  # being run from a pyinstall'd app
        return Path(sys._MEIPASS) / Path(str_path)  # pylint: disable=no-member
    return ROOT_PATH / Path(str_path)


class WindowWrapper(QMainWindow):
    def __init__(self):
        super(WindowWrapper, self).__init__()
        self.main_window = MainWindow()
        self.setCentralWidget(self.main_window)
        self.show()
        QShortcut(QKeySequence(Qt.CTRL+Qt.Key_Right), self, self.tab_right)
        QShortcut(QKeySequence(Qt.CTRL+Qt.Key_Left), self, self.tab_left)

        self.setWindowTitle(f"Circleguard (Backend v{cg_version} / Frontend v{__version__})")

        self.setWindowIcon(QIcon(str(resource_path("resources/logo.ico"))))

    # I know, I know...we have a stupid amount of layers.
    # WindowWrapper -> MainWindow -> MainTab -> Tabs
    def tab_right(self):
        tabs = self.main_window.main_tab.tabs
        tabs.setCurrentIndex(tabs.currentIndex() + 1)

    def tab_left(self):
        tabs = self.main_window.main_tab.tabs
        tabs.setCurrentIndex(tabs.currentIndex() - 1)

    def mousePressEvent(self, event):
        focused = self.focusWidget()
        if focused is not None:
            focused.clearFocus()
        super(WindowWrapper, self).mousePressEvent(event)


class MainWindow(QWidget):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.tab_widget = QTabWidget()
        self.main_tab = MainTab()
        self.settings_tab = SettingsTab()
        self.tab_widget.addTab(self.main_tab, "Main Tab")
        self.tab_widget.addTab(self.settings_tab, "Settings Tab")

        # so when we switch from settings tab to main tab, whatever tab we're on gets changed if we delete our api key
        self.tab_widget.currentChanged.connect(self.main_tab.switch_run_button)

        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.tab_widget)
        self.setLayout(self.main_layout)



class MainTab(QWidget):
    TAB_REGISTER = [
        {"name":  "MAP",   "requires_api": True},
        {"name": "SCREEN", "requires_api": True},
        {"name": "LOCAL",  "requires_api": False},
        {"name": "VERIFY", "requires_api": True},
    ]

    def __init__(self):
        super(MainTab, self).__init__()
        self.q = Queue()

        tabs = QTabWidget()
        self.map_tab = MapTab()
        self.user_tab = UserTab()
        self.local_tab = LocalTab()
        self.verify_tab = VerifyTab()
        tabs.addTab(self.map_tab, "Check Map")
        tabs.addTab(self.user_tab, "Screen User")
        tabs.addTab(self.local_tab, "Check Local Replays")
        tabs.addTab(self.verify_tab, "Verify")
        self.tabs = tabs
        self.tabs.currentChanged.connect(self.switch_run_button)

        terminal = QTextEdit()
        terminal.setReadOnly(True)
        terminal.ensureCursorVisible()
        self.terminal = terminal

        self.run_button = QPushButton()
        self.run_button.setText("Run")
        self.run_button.clicked.connect(self.run)

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        layout.addWidget(self.terminal)
        layout.addWidget(self.run_button)
        self.setLayout(layout)

        self.switch_run_button()  # disable run button if there is no api key
        self.start_timer()

    def start_timer(self):
        timer = QTimer(self)
        timer.timeout.connect(self.print_results)
        timer.start(250)

    def write(self, text):
        self.terminal.append(str(text).strip())
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        cursor = QTextCursor(self.terminal.document())
        cursor.movePosition(QTextCursor.End)
        self.terminal.setTextCursor(cursor)

    def run(self):
        pool = ThreadPool(processes=1)
        pool.apply_async(self.run_circleguard)

    def switch_run_button(self):
        self.run_button.setEnabled(not MainTab.TAB_REGISTER[self.tabs.currentIndex()]["requires_api"] if API_KEY == "" else True)

    def run_circleguard(self):
        try:
            cg = Circleguard(API_KEY, resource_path("db/cache.db"))
            current_tab = self.tabs.currentIndex()

            if MainTab.TAB_REGISTER[current_tab]["name"] == "MAP":
                tab = self.map_tab
                # TODO: generic failure terminal print method, 'please enter a map id' or 'that map has no leaderboard scores, please double check the id'
                # maybe fancy flashing red stars for required fields
                map_id_str = tab.id_combined.map_id.field.text()
                map_id = int(map_id_str) if len(map_id_str) > 0 else 0
                num = tab.compare_top.slider.value()
                thresh = tab.threshold.thresh_slider.value()
                gen = cg.map_check(map_id, num=num, thresh=thresh)

            if MainTab.TAB_REGISTER[current_tab]["name"] == "SCREEN":
                tab = self.user_tab
                user_id = int(tab.user_id.field.text())
                num = tab.compare_top_map.slider.value()
                thresh = tab.threshold.thresh_slider.value()
                gen = cg.user_check(user_id, num)

            if MainTab.TAB_REGISTER[current_tab]["name"] == "LOCAL":
                tab = self.local_tab
                path = tab.folder_chooser.path
                gen = cg.local_check(path)

            if MainTab.TAB_REGISTER[current_tab]["name"] == "VERIFY":
                tab = self.verify_tab
                map_id = int(tab.map_id.field.text())
                user_id_1 = int(tab.user_id_1.field.text())
                user_id_2 = int(tab.user_id_2.field.text())
                gen = cg.verify(map_id, user_id_1, user_id_2)

            for result in gen:
                self.q.put(result)

        except Exception:
            log.exception("ERROR!! while running cg:")

    def print_results(self):
        try:
            while True:
                result = self.q.get(block=False)
                # if result.ischeat:
                self.write(f"{result.similiarity:0.1f} similarity. {result.replay1.username} vs {result.replay2.username}, {result.later_name} set later")
                QApplication.beep()
                QApplication.alert(self)
        except Empty:
            pass


class MapTab(QWidget):
    def __init__(self):
        super(MapTab, self).__init__()

        self.info = QLabel(self)
        self.info.setText("Compare the top plays of a Map's leaderboard.\nIf a user is given, "
                          "it will compare that user's play on the map against the top plays.")

        self.id_combined = IdWidgetCombined()
        self.compare_top = CompareTopUsers()

        self.threshold = ThresholdCombined()

        layout = QGridLayout()
        layout.addWidget(self.info, 0, 0, 1, 1)
        layout.addWidget(self.id_combined, 1, 0, 2, 1)
        layout.addWidget(self.compare_top, 4, 0, 1, 1)
        layout.addWidget(self.threshold, 5, 0, 2, 1)

        self.setLayout(layout)


class UserTab(QWidget):
    def __init__(self):
        super(UserTab, self).__init__()
        self.info = QLabel(self)
        self.info.setText("Compare a user's top plays against the map's leaderboard.")

        self.user_id = InputWidget("User Id", "User id, as seen in the profile url", type_="id")
        self.compare_top_user = CompareTopUsers()
        self.compare_top_map = CompareTopPlays()
        self.threshold = ThresholdCombined()

        layout = QGridLayout()
        layout.addWidget(self.info, 0, 0, 1, 1)
        layout.addWidget(self.user_id, 1, 0, 1, 1)
        layout.addWidget(self.compare_top_map, 2, 0, 1, 1)
        layout.addWidget(self.compare_top_user, 3, 0, 1, 1)
        layout.addWidget(self.threshold, 4, 0, 2, 1)

        self.setLayout(layout)


class LocalTab(QWidget):
    def __init__(self):
        super(LocalTab, self).__init__()
        self.info = QLabel(self)
        self.info.setText("Compare local replays in a given folder.\n"
                          "If a Map is given, it will compare the local replays against the leaderboard of the map.\n"
                          "If both a user and a map are given, it will compare the local replays against the user's "
                          "score on that map.")
        self.folder_chooser = FolderChooser("Replay folder")
        self.id_combined = IdWidgetCombined()
        self.compare_top = CompareTopUsers()
        self.threshold = ThresholdCombined()
        self.id_combined.map_id.field.textChanged.connect(self.switch_compare)
        self.switch_compare()

        self.grid = QGridLayout()
        self.grid.addWidget(self.info, 0, 0, 1, 1)
        self.grid.addWidget(self.folder_chooser, 1, 0, 1, 1)
        self.grid.addWidget(self.id_combined, 2, 0, 2, 1)
        self.grid.addWidget(self.compare_top, 4, 0, 1, 1)
        self.grid.addWidget(self.threshold, 5, 0, 2, 1)

        self.setLayout(self.grid)

    def switch_compare(self):
        self.compare_top.update_user(self.id_combined.map_id.field.text() != "")


class VerifyTab(QWidget):
    def __init__(self):
        super(VerifyTab, self).__init__()
        self.info = QLabel(self)
        self.info.setText("Verifies that the scores are steals of each other.")

        self.map_id = InputWidget("Map Id", "Beatmap id, not the mapset id!", type_="id")
        self.user_id_1 = InputWidget("User Id #1", "User id, as seen in the profile url", type_="id")
        self.user_id_2 = InputWidget("User Id #2", "User id, as seen in the profile url", type_="id")

        self.threshold = ThresholdCombined()

        layout = QGridLayout()
        layout.addWidget(self.info, 0, 0, 1, 1)
        layout.addWidget(self.map_id, 1, 0, 1, 1)
        layout.addWidget(self.user_id_1, 2, 0, 1, 1)
        layout.addWidget(self.user_id_2, 3, 0, 1, 1)
        layout.addWidget(self.threshold, 4, 0, 2, 1)

        self.setLayout(layout)


class SettingsTab(QWidget):
    def __init__(self):
        super(SettingsTab, self).__init__()
        self.info = QLabel(self)
        self.info.setText(f"Backend Version: {cg_version}<br/>"
                          f"Frontend Version: {__version__}<br/>"
                          f"Repository: <a href=\"https://github.com/circleguard/circleguard\">github.com/circleguard/circleguard</a>")
        self.info.setTextFormat(Qt.RichText)
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setOpenExternalLinks(True)
        self.info.setAlignment(Qt.AlignCenter)

        self.apikey_widget = InputWidget("Api Key", "", type_="password")
        self.apikey_widget.field.setText(API_KEY)
        self.apikey_widget.field.textChanged.connect(partial(update_default, "api_key"))
        self.apikey_widget.field.textChanged.connect(set_api_key)

        self.thresh_widget = Threshold()
        self.thresh_widget.thresh_spinbox.valueChanged.connect(partial(update_default, "threshold"))

        self.darkmode = OptionWidget("Dark mode", "We wouldn't feel right shipping a product without darkmode")
        self.darkmode.box.stateChanged.connect(switch_theme)
        self.darkmode.box.setChecked(DARK_THEME)

        self.cache = OptionWidget("Caching", "Downloaded replays will be cached locally")
        self.cache.box.stateChanged.connect(partial(update_default, "caching"))
        self.cache.box.setChecked(CACHING)

        self.cache_dir = FolderChooser("Cache Path")
        self.cache_dir.path_signal.connect(partial(update_default, "cache_dir"))
        self.cache_dir.update_dir(CACHE_DIR)

        self.loglevel = LoglevelWidget("")
        self.loglevel.combobox.currentIndexChanged.connect(self.set_circleguard_loglevel)
        self.set_circleguard_loglevel()  # set the default loglevel in cg, not just in gui

        self.grid = QGridLayout()
        self.grid.addWidget(self.info, 0, 0, 1, 1)
        self.grid.addWidget(self.apikey_widget, 1, 0, 1, 1)
        self.grid.addWidget(self.thresh_widget, 2, 0, 1, 1)
        self.grid.addWidget(self.cache_dir, 3, 0, 1, 1)
        self.grid.addWidget(self.cache, 4, 0, 1, 1)
        self.grid.addWidget(self.darkmode, 5, 0, 1, 1)
        self.grid.addWidget(self.loglevel, 6, 0, 1, 1)

        self.setLayout(self.grid)

    def set_circleguard_loglevel(self):
        set_options(loglevel=self.loglevel.combobox.currentData())


def set_api_key(key):
    global API_KEY
    API_KEY = key


def switch_theme(dark):
    update_default("dark_theme", 1 if dark else 0)
    if dark:
        dark_p = QPalette()

        dark_p.setColor(QPalette.Window, QColor(53, 53, 53))
        dark_p.setColor(QPalette.WindowText, Qt.white)
        dark_p.setColor(QPalette.Base, QColor(25, 25, 25))
        dark_p.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        dark_p.setColor(QPalette.ToolTipBase, QColor(53, 53, 53))
        dark_p.setColor(QPalette.ToolTipText, Qt.white)
        dark_p.setColor(QPalette.Text, Qt.white)
        dark_p.setColor(QPalette.Button, QColor(53, 53, 53))
        dark_p.setColor(QPalette.ButtonText, Qt.white)
        dark_p.setColor(QPalette.BrightText, Qt.red)
        dark_p.setColor(QPalette.Link, QColor(42, 130, 218))
        dark_p.setColor(QPalette.Highlight, QColor(218, 130, 42))
        dark_p.setColor(QPalette.Inactive, QPalette.Highlight, Qt.lightGray)
        dark_p.setColor(QPalette.HighlightedText, Qt.black)
        dark_p.setColor(QPalette.Disabled, QPalette.Text, Qt.darkGray)
        dark_p.setColor(QPalette.Disabled, QPalette.ButtonText, Qt.darkGray)
        dark_p.setColor(QPalette.Disabled, QPalette.Highlight, Qt.darkGray)
        dark_p.setColor(QPalette.Disabled, QPalette.Base, QColor(53, 53, 53))

        app.setPalette(dark_p)
        app.setStyleSheet("QToolTip { color: #ffffff; "
                          "background-color: #2a2a2a; "
                          "border: 1px solid white; }")
    else:
        app.setPalette(app.style().standardPalette())
        updated_palette = QPalette()
        # fixes inactive items not being greyed out
        updated_palette.setColor(QPalette.Disabled, QPalette.ButtonText, Qt.darkGray)
        updated_palette.setColor(QPalette.Disabled, QPalette.Highlight, Qt.darkGray)
        updated_palette.setColor(QPalette.Inactive, QPalette.Highlight, QColor(240, 240, 240))
        app.setPalette(updated_palette)
        app.setStyleSheet("QToolTip { color: #000000; "
                          "background-color: #D5D5D5; "
                          "border: 1px solid white; }")


if __name__ == "__main__":
    # create and open window
    app = QApplication([])
    app.setStyle("Fusion")
    WINDOW = WindowWrapper()
    set_event_window(WINDOW)
    WINDOW.resize(600, 500)
    WINDOW.show()
    app.exec_()

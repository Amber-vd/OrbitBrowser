import sys
import os
import json
import html
from datetime import datetime
from urllib.parse import quote_plus

from openai import OpenAI

from PyQt6.QtCore import (
    QUrl, Qt, QSize, QThread, pyqtSignal, QTimer, QStringListModel,
    QSettings
)
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLineEdit, QPushButton, QLabel, QListWidget,
    QListWidgetItem, QDialog, QTextBrowser, QMessageBox, QMenu,
    QProgressBar, QDockWidget, QCompleter, QFormLayout, QComboBox,
    QDialogButtonBox, QFileDialog, QInputDialog, QToolButton
)

from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings
)


DATA_FOLDER = os.path.join(os.path.expanduser("~"), "OrbitBrowserData")
os.makedirs(DATA_FOLDER, exist_ok=True)

BOOKMARKS_FILE = os.path.join(DATA_FOLDER, "bookmarks.json")
HISTORY_FILE = os.path.join(DATA_FOLDER, "history.json")
SETTINGS = QSettings("OrbitBrowser", "OrbitBrowser")


YOUTUBE_SCRIPT = """
(function() {
    if (window.orbitFullscreenInstalled) return;
    window.orbitFullscreenInstalled = true;
    document.addEventListener('keydown', function(event) {
        if (event.key.toLowerCase() !== 'f') return;
        if (event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
        const target = event.target;
        if (target && (target.tagName === 'INPUT' ||
            target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
        const video = document.querySelector('video');
        if (!video) return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        if (document.fullscreenElement) {
            document.exitFullscreen();
            return;
        }
        const player = video.closest('#movie_player') ||
            video.closest('ytd-reel-video-renderer') || video;
        if (player.requestFullscreen) player.requestFullscreen().catch(function() {});
    }, true);
})();
"""


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, value):
    try:
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(value, file, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError as error:
        print("Could not save browser data:", error)


class BrowserPage(QWebEnginePage):
    def __init__(self, profile, popup_callback, parent=None):
        super().__init__(profile, parent)
        self.popup_callback = popup_callback

    def createWindow(self, window_type):
        return self.popup_callback()


class AIWorker(QThread):
    response_received = pyqtSignal(str)
    error_received = pyqtSignal(str)

    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def run(self):
        try:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise Exception("GROQ_API_KEY was not found in your Windows environment variables.")
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1",
                timeout=60.0
            )
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=self.messages
            )
            answer = response.choices[0].message.content or "I didn't receive a response."
            self.response_received.emit(answer)
        except Exception as error:
            self.error_received.emit(str(error))


class OrbitAIWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages = [{
            "role": "system",
            "content": (
                "You are Orbit AI, the assistant built into OrbitBrowser. "
                "Be friendly, clear, helpful, and conversational. Help with "
                "coding, technology, browsing, and general questions."
            )
        }]
        self.worker = None
        layout = QVBoxLayout(self)
        title = QLabel("ORBIT AI")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #c49aff;")
        layout.addWidget(title)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        self.chat.append("<b>Orbit AI:</b> Hey! What can I help you with?")
        layout.addWidget(self.chat)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Orbit AI...")
        self.input.returnPressed.connect(self.send_message)
        self.send_button = QPushButton("SEND")
        self.send_button.clicked.connect(self.send_message)
        row.addWidget(self.input)
        row.addWidget(self.send_button)
        layout.addLayout(row)

    def safe_text(self, value):
        return html.escape(value).replace("\n", "<br>")

    def send_message(self):
        message = self.input.text().strip()
        if not message or self.worker is not None:
            return
        self.chat.append("<b>You:</b> " + self.safe_text(message))
        self.input.clear()
        self.messages.append({"role": "user", "content": message})
        self.chat.append("<i>Orbit AI is thinking...</i>")
        self.send_button.setEnabled(False)
        self.input.setEnabled(False)
        self.worker = AIWorker(self.messages.copy())
        self.worker.response_received.connect(self.handle_response)
        self.worker.error_received.connect(self.handle_error)
        self.worker.finished.connect(self.request_finished)
        self.worker.start()

    def handle_response(self, answer):
        self.chat.append("<b>Orbit AI:</b> " + self.safe_text(answer))
        self.messages.append({"role": "assistant", "content": answer})

    def handle_error(self, error):
        self.chat.append("<b>Orbit AI error:</b> " + self.safe_text(error))
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def request_finished(self):
        self.send_button.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()
        self.worker = None


class DownloadsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrbitBrowser Downloads")
        self.resize(680, 430)
        self.download_rows = {}
        layout = QVBoxLayout(self)
        self.download_list = QListWidget()
        layout.addWidget(self.download_list)
        row = QHBoxLayout()
        open_folder = QPushButton("OPEN DOWNLOADS FOLDER")
        open_folder.clicked.connect(self.open_folder)
        row.addWidget(open_folder)
        row.addStretch()
        layout.addLayout(row)

    def downloads_folder(self):
        folder = SETTINGS.value("downloads_folder", os.path.join(os.path.expanduser("~"), "Downloads"))
        os.makedirs(folder, exist_ok=True)
        return folder

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.downloads_folder()))

    def add_download(self, download):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 112))
        self.download_list.addItem(item)
        row = QWidget()
        row_layout = QVBoxLayout(row)
        name = QLabel(download.downloadFileName())
        name.setWordWrap(True)
        progress = QProgressBar()
        status = QLabel("Starting download...")
        buttons = QHBoxLayout()
        cancel = QPushButton("CANCEL")
        open_file = QPushButton("OPEN FILE")
        open_file.setEnabled(False)
        buttons.addWidget(cancel)
        buttons.addWidget(open_file)
        row_layout.addWidget(name)
        row_layout.addWidget(progress)
        row_layout.addWidget(status)
        row_layout.addLayout(buttons)
        self.download_list.setItemWidget(item, row)
        self.download_rows[download] = (progress, status, cancel, open_file)

        def update_progress():
            total = download.totalBytes()
            received = download.receivedBytes()
            if total > 0:
                progress.setRange(0, 100)
                progress.setValue(max(0, min(100, int(received * 100 / total))))
                status.setText(f"{received // 1024:,} KB of {total // 1024:,} KB")

        def update_state(state):
            state_name = str(state).split(".")[-1].lower()
            if "completed" in state_name:
                progress.setValue(100)
                status.setText("Download complete")
                cancel.setEnabled(False)
                open_file.setEnabled(True)
            elif "cancel" in state_name:
                status.setText("Download cancelled")
                cancel.setEnabled(False)
            elif "interrupt" in state_name:
                status.setText("Download interrupted")
                cancel.setEnabled(False)

        def open_downloaded_file():
            path = os.path.join(download.downloadDirectory(), download.downloadFileName())
            if os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QMessageBox.warning(self, "File not found", "The downloaded file could not be found.")

        cancel.clicked.connect(download.cancel)
        open_file.clicked.connect(open_downloaded_file)
        download.receivedBytesChanged.connect(update_progress)
        download.stateChanged.connect(update_state)
        update_progress()


class BookmarksDialog(QDialog):
    open_requested = pyqtSignal(str)

    def __init__(self, bookmarks, parent=None):
        super().__init__(parent)
        self.bookmarks = bookmarks
        self.setWindowTitle("OrbitBrowser Bookmarks")
        self.resize(600, 420)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for bookmark in bookmarks:
            item = QListWidgetItem(bookmark.get("title", bookmark.get("url", "")) + "\n" + bookmark.get("url", ""))
            item.setData(Qt.ItemDataRole.UserRole, bookmark.get("url", ""))
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(self.open_selected)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        open_button = QPushButton("OPEN")
        remove_button = QPushButton("REMOVE")
        close_button = QPushButton("CLOSE")
        open_button.clicked.connect(self.open_selected)
        remove_button.clicked.connect(self.remove_selected)
        close_button.clicked.connect(self.accept)
        row.addWidget(open_button)
        row.addWidget(remove_button)
        row.addWidget(close_button)
        layout.addLayout(row)

    def open_selected(self, item=None):
        item = item or self.list.currentItem()
        if item:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def remove_selected(self):
        row = self.list.currentRow()
        if row >= 0:
            self.bookmarks.pop(row)
            self.list.takeItem(row)


class HistoryDialog(QDialog):
    open_requested = pyqtSignal(str)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle("OrbitBrowser History")
        self.resize(700, 460)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for entry in reversed(history):
            title = entry.get("title") or entry.get("url", "")
            when = entry.get("time", "")
            item = QListWidgetItem(f"{title}\n{entry.get('url', '')}\n{when}")
            item.setData(Qt.ItemDataRole.UserRole, entry.get("url", ""))
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(self.open_selected)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        open_button = QPushButton("OPEN")
        clear_button = QPushButton("CLEAR HISTORY")
        close_button = QPushButton("CLOSE")
        open_button.clicked.connect(self.open_selected)
        clear_button.clicked.connect(self.clear_history)
        close_button.clicked.connect(self.accept)
        row.addWidget(open_button)
        row.addWidget(clear_button)
        row.addWidget(close_button)
        layout.addLayout(row)

    def open_selected(self, item=None):
        item = item or self.list.currentItem()
        if item:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def clear_history(self):
        answer = QMessageBox.question(
            self, "Clear history", "Delete all browsing history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self.list.clear()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OrbitBrowser Settings")
        self.resize(520, 300)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.homepage = QLineEdit(str(SETTINGS.value("homepage", "https://www.google.com")))
        self.search_engine = QComboBox()
        self.search_engine.addItem("Google", "https://www.google.com/search?q=")
        self.search_engine.addItem("Bing", "https://www.bing.com/search?q=")
        self.search_engine.addItem("DuckDuckGo", "https://duckduckgo.com/?q=")
        saved_engine = SETTINGS.value("search_engine", "https://www.google.com/search?q=")
        index = self.search_engine.findData(saved_engine)
        if index >= 0:
            self.search_engine.setCurrentIndex(index)
        self.download_folder = QLineEdit(str(SETTINGS.value(
            "downloads_folder", os.path.join(os.path.expanduser("~"), "Downloads")
        )))
        browse = QPushButton("BROWSE")
        browse.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.download_folder)
        folder_row.addWidget(browse)
        folder_widget = QWidget()
        folder_widget.setLayout(folder_row)
        form.addRow("Homepage:", self.homepage)
        form.addRow("Search engine:", self.search_engine)
        form.addRow("Downloads folder:", folder_widget)
        layout.addLayout(form)
        self.suggestions = QComboBox()
        self.suggestions.addItems(["On", "Off"])
        self.suggestions.setCurrentText(str(SETTINGS.value("suggestions", "On")))
        form.addRow("Address suggestions:", self.suggestions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose downloads folder", self.download_folder.text())
        if folder:
            self.download_folder.setText(folder)

    def save_settings(self):
        SETTINGS.setValue("homepage", self.homepage.text().strip() or "https://www.google.com")
        SETTINGS.setValue("search_engine", self.search_engine.currentData())
        SETTINGS.setValue("downloads_folder", self.download_folder.text().strip())
        SETTINGS.setValue("suggestions", self.suggestions.currentText())


class OrbitBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OrbitBrowser")
        self.resize(1400, 880)
        self.bookmarks = read_json(BOOKMARKS_FILE, [])
        self.history = read_json(HISTORY_FILE, [])
        self.last_history_url = {}
        self.completer_timer = QTimer(self)
        self.completer_timer.setSingleShot(True)
        self.completer_timer.setInterval(180)
        self.completer_timer.timeout.connect(self.refresh_suggestion_data_once)

        storage = os.path.join(DATA_FOLDER, "Storage")
        cache = os.path.join(DATA_FOLDER, "Cache")
        os.makedirs(storage, exist_ok=True)
        os.makedirs(cache, exist_ok=True)
        self.profile = QWebEngineProfile("OrbitBrowser", self)
        self.profile.setPersistentStoragePath(storage)
        self.profile.setCachePath(cache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.downloadRequested.connect(self.handle_download)

        self.downloads_window = DownloadsWindow(self)
        self.ai_dock = QDockWidget("Orbit AI", self)
        self.ai_dock.setObjectName("OrbitAISidebar")
        self.ai_widget = OrbitAIWidget(self.ai_dock)
        self.ai_dock.setWidget(self.ai_widget)
        self.ai_dock.setMinimumWidth(310)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ai_dock)
        self.ai_dock.hide()

        self.build_interface()
        self.install_logo()
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #160b2b; color: white; }
            QPushButton, QToolButton {
                background-color: #54249a; color: white;
                border: 1px solid #9860dc; border-radius: 6px; padding: 7px;
            }
            QPushButton:hover, QToolButton:hover { background-color: #783bc4; }
            QLineEdit, QListWidget, QTextBrowser, QComboBox {
                background-color: #24143d; color: white;
                border: 1px solid #7546ad; padding: 6px;
            }
            QTabBar::tab { background-color: #291644; color: white; padding: 10px; }
            QTabBar::tab:selected { background-color: #7540b5; }
            QDockWidget::title { background: #291644; padding: 7px; }
        """)
        self.add_new_tab()

    def install_logo(self):
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo", "orbit_logo.png")
        if os.path.exists(logo_path):
            icon = QIcon(logo_path)
            self.setWindowIcon(icon)
            self.logo_label.setPixmap(QPixmap(logo_path).scaled(
                30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
            self.logo_label.setText("◉")

    def build_interface(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()

        self.logo_label = QLabel("◉")
        self.logo_label.setStyleSheet("font-size: 25px; color: #c49aff; font-weight: bold;")
        toolbar.addWidget(self.logo_label)

        self.back_button = QPushButton("BACK")
        self.back_button.clicked.connect(lambda: self.current_browser() and self.current_browser().back())
        self.forward_button = QPushButton("FORWARD")
        self.forward_button.clicked.connect(lambda: self.current_browser() and self.current_browser().forward())
        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.clicked.connect(lambda: self.current_browser() and self.current_browser().reload())
        self.home_button = QPushButton("HOME")
        self.home_button.clicked.connect(self.go_home)
        self.new_tab_button = QPushButton("+ NEW TAB")
        self.new_tab_button.clicked.connect(self.add_new_tab)

        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Search or enter a website...")
        self.address_bar.returnPressed.connect(self.navigate)
        self.completer_model = QStringListModel(self)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.address_bar.setCompleter(self.completer)
        self.address_bar.textEdited.connect(self.schedule_suggestions)
        self.completer.activated[str].connect(self.open_suggestion)

        self.bookmark_button = QPushButton("☆ BOOKMARK")
        self.bookmark_button.clicked.connect(self.add_bookmark)
        self.bookmarks_button = QPushButton("BOOKMARKS")
        self.bookmarks_button.clicked.connect(self.show_bookmarks)
        self.history_button = QPushButton("HISTORY")
        self.history_button.clicked.connect(self.show_history)
        self.download_button = QPushButton("DOWNLOADS")
        self.download_button.clicked.connect(self.show_downloads)
        self.ai_button = QPushButton("ORBIT AI")
        self.ai_button.clicked.connect(self.toggle_ai)
        self.settings_button = QPushButton("SETTINGS")
        self.settings_button.clicked.connect(self.show_settings)

        for button in (
            self.back_button, self.forward_button, self.refresh_button,
            self.home_button, self.new_tab_button
        ):
            toolbar.addWidget(button)
        toolbar.addWidget(self.address_bar, 1)
        for button in (
            self.bookmark_button, self.bookmarks_button, self.history_button,
            self.download_button, self.ai_button, self.settings_button
        ):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.update_address)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self.tab_context_menu)
        layout.addWidget(self.tabs)

    def current_browser(self):
        return self.tabs.currentWidget() if hasattr(self, "tabs") else None

    def add_new_tab(self, url=None):
        browser = QWebEngineView()
        page = BrowserPage(self.profile, self.open_popup_tab, browser)
        browser.setPage(page)
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(self.handle_permission)
        if hasattr(page, "featurePermissionRequested"):
            page.featurePermissionRequested.connect(self.handle_legacy_permission)

        settings = browser.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)

        page.loadFinished.connect(
            lambda success, p=page: p.runJavaScript(YOUTUBE_SCRIPT) if success else None
        )
        browser.urlChanged.connect(lambda changed_url, b=browser: self.update_tab(b, changed_url))
        browser.titleChanged.connect(lambda title, b=browser: self.update_title(b, title))
        index = self.tabs.addTab(browser, "New Tab")
        self.tabs.setCurrentIndex(index)
        browser.setUrl(url if isinstance(url, QUrl) else QUrl(url) if url else QUrl("https://www.google.com"))
        return page

    def handle_permission(self, permission):
        try:
            feature_name = str(permission.feature()).split(".")[-1]
            origin = permission.origin().toString()
            if "AudioVideo" in feature_name or "AudioVideoCapture" in feature_name:
                description = "microphone and camera"
            elif "Audio" in feature_name:
                description = "microphone"
            elif "Video" in feature_name:
                description = "camera"
            else:
                return
            answer = QMessageBox.question(
                self, "Website Permission",
                origin + " wants to use your " + description + ".\n\nAllow?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            permission.grant() if answer == QMessageBox.StandardButton.Yes else permission.deny()
        except Exception as error:
            print("Permission error:", error)

    def handle_legacy_permission(self, origin, feature):
        page = self.sender()
        feature_name = str(feature).split(".")[-1]
        if "MediaAudioVideo" in feature_name:
            description = "microphone and camera"
        elif "MediaAudioCapture" in feature_name:
            description = "microphone"
        elif "MediaVideoCapture" in feature_name:
            description = "camera"
        else:
            return
        answer = QMessageBox.question(
            self, "Website Permission",
            origin.toString() + " wants to use your " + description + ".\n\nAllow?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        policy = (
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            if answer == QMessageBox.StandardButton.Yes
            else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )
        page.setFeaturePermission(origin, feature, policy)

    def open_popup_tab(self):
        return self.add_new_tab()

    def close_tab(self, index):
        if self.tabs.count() <= 1:
            self.add_new_tab()
            browser = self.tabs.widget(index)
            self.tabs.removeTab(index)
            if browser:
                browser.deleteLater()
            return
        browser = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if browser:
            browser.deleteLater()

    def tab_context_menu(self, position):
        index = self.tabs.tabBar().tabAt(position)
        if index < 0:
            return
        browser = self.tabs.widget(index)
        menu = QMenu(self)
        mute_action = menu.addAction("Unmute Tab" if browser.page().isAudioMuted() else "Mute Tab")
        reload_action = menu.addAction("Reload Tab")
        duplicate_action = menu.addAction("Duplicate Tab")
        menu.addSeparator()
        close_action = menu.addAction("Close Tab")
        close_others_action = menu.addAction("Close Other Tabs")
        chosen = menu.exec(self.tabs.tabBar().mapToGlobal(position))
        if chosen == mute_action:
            browser.page().setAudioMuted(not browser.page().isAudioMuted())
        elif chosen == reload_action:
            browser.reload()
        elif chosen == duplicate_action:
            self.add_new_tab(browser.url())
        elif chosen == close_action:
            self.close_tab(index)
        elif chosen == close_others_action:
            for tab_index in reversed(range(self.tabs.count())):
                if self.tabs.widget(tab_index) != browser:
                    self.close_tab(tab_index)

    def navigate(self):
        text = self.address_bar.text().strip()
        if not text:
            return
        if "://" in text:
            url = text
        elif "." in text and " " not in text:
            url = "https://" + text
        else:
            engine = SETTINGS.value("search_engine", "https://www.google.com/search?q=")
            url = engine + quote_plus(text)
        self.current_browser().setUrl(QUrl(url))

    def go_home(self):
        homepage = SETTINGS.value("homepage", "https://www.google.com")
        self.current_browser().setUrl(QUrl(str(homepage)))

    def update_address(self, index=None):
        browser = self.current_browser()
        if browser:
            self.address_bar.setText(browser.url().toString())

    def update_tab(self, browser, url):
        index = self.tabs.indexOf(browser)
        if index >= 0:
            title = browser.title() or url.host()
            self.tabs.setTabText(index, title[:25])
        if browser == self.current_browser():
            self.address_bar.setText(url.toString())
        self.record_history(browser, url)
        self.refresh_suggestion_data()

    def update_title(self, browser, title):
        index = self.tabs.indexOf(browser)
        if index >= 0 and title:
            self.tabs.setTabText(index, title[:25])

    def record_history(self, browser, url):
        address = url.toString()
        if not address or url.scheme() not in ("http", "https"):
            return
        if self.last_history_url.get(browser) == address:
            return
        self.last_history_url[browser] = address
        self.history.append({
            "title": browser.title() or address,
            "url": address,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        self.history = self.history[-3000:]
        write_json(HISTORY_FILE, self.history)

    def add_bookmark(self):
        browser = self.current_browser()
        if not browser:
            return
        url = browser.url().toString()
        if not url or url == "about:blank":
            return
        if any(item.get("url") == url for item in self.bookmarks):
            QMessageBox.information(self, "Bookmarks", "This page is already bookmarked.")
            return
        self.bookmarks.append({"title": browser.title() or url, "url": url})
        write_json(BOOKMARKS_FILE, self.bookmarks)
        self.refresh_suggestion_data()
        QMessageBox.information(self, "Bookmarks", "Bookmark saved!")

    def show_bookmarks(self):
        dialog = BookmarksDialog(self.bookmarks.copy(), self)
        dialog.open_requested.connect(self.open_address)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.bookmarks = dialog.bookmarks
            write_json(BOOKMARKS_FILE, self.bookmarks)
            self.refresh_suggestion_data()
        else:
            # Keep removals even if the dialog is closed with its close button.
            self.bookmarks = dialog.bookmarks
            write_json(BOOKMARKS_FILE, self.bookmarks)
            self.refresh_suggestion_data()

    def show_history(self):
        dialog = HistoryDialog(self.history, self)
        dialog.open_requested.connect(self.open_address)
        dialog.exec()
        self.history = dialog.history
        write_json(HISTORY_FILE, self.history)
        self.refresh_suggestion_data()

    def open_address(self, address):
        if address:
            self.current_browser().setUrl(QUrl(address))

    def refresh_suggestion_data(self):
        if not hasattr(self, "completer_model"):
            return
        if str(SETTINGS.value("suggestions", "On")) == "Off":
            self.completer_model.setStringList([])
            return
        values = []
        for entry in self.bookmarks:
            values.extend([entry.get("title", ""), entry.get("url", "")])
        for entry in reversed(self.history[-250:]):
            values.extend([entry.get("title", ""), entry.get("url", "")])
        unique = list(dict.fromkeys(value for value in values if value))
        self.completer_model.setStringList(unique)

    def schedule_suggestions(self, text):
        self.completer_timer.stop()
        self.completer_timer.start()

    def refresh_suggestion_data_once(self):
        self.refresh_suggestion_data()
        if self.address_bar.text().strip() and self.completer_model.rowCount():
            self.completer.complete()

    def open_suggestion(self, text):
        if "://" in text or ("." in text and " " not in text):
            self.open_address(text)
        else:
            self.address_bar.setText(text)
            self.navigate()

    def show_downloads(self):
        self.downloads_window.show()
        self.downloads_window.raise_()
        self.downloads_window.activateWindow()

    def handle_download(self, download):
        folder = str(SETTINGS.value(
            "downloads_folder", os.path.join(os.path.expanduser("~"), "Downloads")
        ))
        os.makedirs(folder, exist_ok=True)
        download.setDownloadDirectory(folder)
        download.accept()
        self.downloads_window.add_download(download)
        self.show_downloads()

    def toggle_ai(self):
        self.ai_dock.setVisible(not self.ai_dock.isVisible())
        if self.ai_dock.isVisible():
            self.ai_widget.input.setFocus()

    def show_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.save_settings()
            self.refresh_suggestion_data()
            QMessageBox.information(self, "Settings", "Settings saved.")

    def closeEvent(self, event):
        write_json(BOOKMARKS_FILE, self.bookmarks)
        write_json(HISTORY_FILE, self.history)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("OrbitBrowser")
    window = OrbitBrowser()
    window.show()
    sys.exit(app.exec())

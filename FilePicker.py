# -*- coding: utf-8 -*-
"""
檔案選擇器 (File Picker) - 仿 File Manager+ 操作介面
執行環境：Android 手機 (Samsung S23 Ultra) + Pydroid3 + PySide6

功能：
    - 瀏覽資料夾（上一層、回首頁、路徑列可直接輸入跳轉）
    - 上方導覽列有「上一層」「首頁」「快速切換」「深色模式切換」四個圖示按鈕。
      「首頁」與「快速切換」是兩組可各自記憶的書籤路徑，圖示顏色代表目前
      作用中的是哪一個（藍色＝作用中，灰色＝未作用）；啟動時預設作用中的
      是「首頁」。
    - 顯示資料夾／檔案清單，資料夾排前面
    - 檔案：單擊切換選取。資料夾：長按選取整個目錄，單擊（未長按、未拖曳）
      則取消選取；雙擊資料夾仍是進入該資料夾瀏覽。
    - 檔名搜尋過濾
    - 新增資料夾（刪除資料夾請改用：選取該資料夾後按「確定」→「刪除」）
    - 全選 / 清除 / 貼上 / 確定
    - 按下「確定」後，彈出檔案操作選單，提供一般檔案管理程式常見的功能：
        複製、刪除、變更屬性（可設定讀/寫/執行權限）、
        重新命名（單一項目）、使用其他應用程式開啟（單一檔案）
      使用者仍可自行修改 process_selected_files() 接上自己的邏輯。
    - 清單支援手指滑動（拖曳手勢）瀏覽，不需精準拉動捲動軸。
    - 切換路徑、複製、刪除、變更屬性...等任何可能造成反應延遲的動作，
      執行期間都會顯示遮罩＋旋轉動畫，且都在背景執行緒進行，不會卡住介面。
    - 在路徑輸入欄位按下 Enter：只要輸入的是有效資料夾路徑，不論跟目前
      路徑是否相同，都會依照目前作用中的是「首頁」還是「快速切換」，
      把它存為對應的路徑，並寫入程式旁的 filepicker_config.json 設定檔
      對應的項目（home_dir 或 quick_dir）；下次按同一個按鈕或重新開啟
      程式都會直接使用這個路徑；設定完成會彈出通知對話盒告知。
    - 變更亮/暗模式後，會自動寫入 filepicker_config.json 設定檔中的 dark_mode 項目，
      下次重開程式時自動載入主題設定。

使用方式：
    在 Pydroid3 開啟本檔案並執行即可。
    第一次執行若清單是空的，代表尚未授予儲存權限，
    請至 Android 設定 -> App -> Pydroid 3 -> 權限 -> 開啟「檔案和媒體」權限後再重開程式。

注意：
    「使用其他應用程式開啟」在 Android 上需要透過 pyjnius 呼叫系統 Intent，
    並且該 App 需要有 FileProvider 設定才能安全地把檔案路徑分享給其他 App。
    在 Pydroid3 直接執行的腳本通常沒有這個設定，此功能會嘗試呼叫，
    失敗時會顯示提示訊息，不會讓程式崩潰；在 Windows 上則改用系統預設程式開啟。
"""

import os
import sys
import json
import base64
import math
import time
import shutil
import stat as stat_module
import mimetypes
import subprocess
from datetime import datetime

from PySide6.QtCore import (
    Qt,
    Signal,
    QSize,
    QPointF,
    QRectF,
    QThread,
    QTimer)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QScroller,
    QMessageBox,
    QScrollBar,
    QSizePolicy,
    QFrame)
from PySide6.QtGui import (
    QFont,
    QPainter,
    QPixmap,
    QIcon,
    QPainterPath,
    QPolygonF,
    QPen,
    QColor)

# -----------------------------------------------------------------
# 嘗試在 Android 上請求儲存權限
# (若非 Android或模組不存在則忽略，
#  方便桌機測試）
# -----------------------------------------------------------------
def request_android_storage_permission():
    try:
        from android.permissions import (
            request_permissions,
            Permission) # type: ignore
        request_permissions([
            Permission.READ_EXTERNAL_STORAGE,
            Permission.WRITE_EXTERNAL_STORAGE,
        ])
    except Exception:
        pass

#取得Nutika編譯成的exe檔實體所在路徑
def get_exe_dir():
    if getattr(sys, 'frozen', False) or "__nuitka_version__" in globals():
        # 取得實體 exe 所在目錄
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

# 預設起始路徑：Android 內部儲存空間根目錄
DEFAULT_ROOT = "/storage/emulated/0"
if not os.path.isdir(DEFAULT_ROOT):
    #DEFAULT_ROOT = os.path.expanduser("~")
    DEFAULT_ROOT = get_exe_dir()
DEFAULT_RAPID = "/storage/emulated/0/download"
if not os.path.isdir(DEFAULT_RAPID):
    #DEFAULT_RAPID = os.path.expanduser("~")
    DEFAULT_RAPID = get_exe_dir()

# 設定檔：記住使用者自訂的首頁路徑、快速切換路徑與深色模式 setting
#CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FilePicker_Config.json")
CONFIG_PATH = f"{get_exe_dir()}/FilePicker_Config.json"


def get_checkbox_icon_path():
    """
    把 CHECKBOX_CHECK_ICON_B64 解碼寫入程式旁的 PNG 檔案，回傳絕對路徑。

    Qt Style Sheet 的 url() 只認得實際檔案路徑（或 qrc: 資源路徑），
    並不支援 data:image/...;base64,... 這種內嵌資料格式（先前用這種寫法
    導致勾號完全不顯示，就是因為 QSS 根本沒能把它當成圖片載入）。
    因此改成：程式啟動時把 base64 資料解碼成一個實體 PNG 檔案，
    之後 QSS 一律用這個檔案的路徑來顯示打勾圖示。
    """
    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_filepicker_check_icon.png")
    try:
        if not os.path.exists(icon_path):
            with open(icon_path, "wb") as f:
                f.write(base64.b64decode(CHECKBOX_CHECK_ICON_B64))
    except Exception:
        pass
    return icon_path


def load_path_config(key):
    """讀取設定檔中指定 key 儲存的路徑；讀取失敗或該路徑已不存在則回傳 None。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        path = data.get(key)
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return None


def save_path_config(key, path):
    """把指定 key 的路徑寫入設定檔（JSON），供下次啟動程式時讀取。"""
    data = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    data[key] = path
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_theme_config():
    """讀取設定檔中的深色模式設定；若不存在則預設傳回 False (淺色模式)。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("dark_mode", False)
    except Exception:
        return False


def save_theme_config(dark_mode: bool):
    """把深色/淺色主題設定寫入 JSON 設定檔中。"""
    data = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    data["dark_mode"] = dark_mode
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 「首頁」／「快速切換」兩個書籤按鈕的圖示顏色：藍色代表目前作用中，
# 灰色代表未作用，跟深色/淺色主題無關，固定用這兩個顏色。
BOOKMARK_ACTIVE_COLOR = "#1a73e8"
BOOKMARK_INACTIVE_COLOR = "#9e9e9e"

# 核取方塊（QCheckBox）打勾圖示：白色勾號的 PNG（base64，64x64 透明底），
# 搭配下方樣式表產生「藍框、藍底、白勾」的外觀。
#
# 注意：Qt Style Sheet 的 url() 不支援 data:...;base64,... 這種內嵌資料
# 寫法（不論 SVG 或 PNG 都一樣不會顯示，只會看到底色，看不到圖）；
# 它只認得實際檔案路徑或 qrc: 資源路徑。因此改為：程式啟動套用主題時，
# 由 get_checkbox_icon_path() 把這段 base64 解碼寫成程式旁的實體 PNG
# 檔案（_filepicker_check_icon.png），QSS 再用該檔案的路徑載入圖示。
CHECKBOX_CHECK_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAADA0lEQVR42u2Zv27TUBTGbTdhqEAQ"
    "kIJg6wOUKU1hrBTBC/QBuqAOgNRH6MTKWhWGPkAHmJDoDlLZurBVgg5IpS0LlWib+MdyrnRkHP9L"
    "7Ni395MsOzeJ5e8753z33mPPc3BwcHBwcHBwcGg8AB/wryvxOfV5DgiuDXl1PQ/cifvOavLAQ+At"
    "8AM4Ad4Dy/JdYCv5QFL/PvCd/3EFDExJ2FjzLbn+JIQvgFCOCxn7BtywrhRMRIEtFe0oRiLGslVZ"
    "oCK/LkSHxMMIsGKNAIr8QEU+HEN+CPwCbluxPlBpvyhOP5IjDiYrXloRfXH8ALgHHCSkfqjGN6yY"
    "BiV9A7neSzA9Lcq6NWsAVffbKeTN+Jb8vm1D3bflvJGR/J4RzQbyJvKrQu5yjOObtD8QjwhsqHvj"
    "+D3gTM3pcdPdSGaFRdsc/y5wqIjGOb4ZH+isaWzDwTi+nL+krPRM3T8vnXxVDYccjn8p5zeVkK+i"
    "4VDA8XdLn+4SGg4f1C6rNcXIr2V0/K9Ax5RLqfUOPBjTcBgq82lPwfH7wHkGxz8FFkpf6akHeycP"
    "8Fc1HEyEToClopmglrhd4DiD44fAk9KnO5X6t4Cfing0Isg8nVsElWHzwH5tHD8iQEcIhglpWUgE"
    "Vfc7tXH8aAlIhD5GHmRiEZTjb6bc24iyU/kGR9VnT0V/NKkIMY6ftrXdlzIJKt/gqFJ4KuQmEkEZ"
    "65I4flppHQPdme7tIw9dWAR1n4WU+xhRzoF+LTY4qmaLitAWP+nIIiaL469Vano5XDuvCH017e1m"
    "dPzNSRdYdRLhN/AIeJ3T8VteHVFQhD9CcJTR8evdx88pQkgyzP8OgU5jurkFRAgT1vhnQK9xLa2c"
    "IsSRN36wWuu6L0mEq8gbnLbXVBQQwZjedmMjP4EIZuyzboJ6NiAiwql6Ta0jPxSBuvq9nzVQIjyO"
    "7CJ1NrywJvVTNlDP1OtsgCPgVbTNbqsIpp8QSMNzBbg57XZ6IzIhbWyW8CsQwfc8zxhd6Ps+noOD"
    "g4ODg4ODw+zxDzIo1Bk/BlQYAAAAAElFTkSuQmCC"
)

# 忙碌動畫至少顯示的時間（毫秒）。
MIN_BUSY_MS = 450


def human_size(num_bytes: int) -> str:
    """把 bytes 轉成人類可讀格式"""
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < step:
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= step
    return f"{num_bytes:.1f}PB"


def dialog_border_style(dark_mode: bool) -> str:
    """
    回傳彈出對話框的樣式字串：邊框顏色刻意採用與底色形成強烈對比（互補）的顏色，
    確保對話框在畫面上（尤其深色模式下）清楚可辨識，不會與背景融在一起。
    """
    if dark_mode:
        bg = "#1e1e1e"
        border = "#f2a93b"   # 暖橘色，與深色底形成互補對比
    else:
        bg = "#ffffff"
        border = "#1a56b0"   # 深藍色，與淺色底形成互補對比
    return f"QDialog {{ background-color: {bg}; border: 3px solid {border}; border-radius: 10px; }}"


# -----------------------------------------------------------------
# 以 QPainter 手繪的向量圖示
# -----------------------------------------------------------------
def _new_pixmap(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    return pm


def icon_folder(size=28):
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#c9861b"), 1))
    p.setBrush(QColor("#f2a93b"))
    p.drawRoundedRect(QRectF(size * 0.06, size * 0.22, size * 0.42, size * 0.16), 2, 2)
    p.drawRoundedRect(QRectF(size * 0.06, size * 0.34, size * 0.88, size * 0.54), 3, 3)
    p.end()
    return QIcon(pm)


def icon_file(size=28):
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    w = size * 0.62
    x0 = (size - w) / 2
    fold = w * 0.32
    p.setPen(QPen(QColor("#8a8a8a"), 1))
    p.setBrush(QColor("#ffffff"))
    body = QPainterPath()
    body.moveTo(x0, size * 0.06)
    body.lineTo(x0 + w - fold, size * 0.06)
    body.lineTo(x0 + w, size * 0.06 + fold)
    body.lineTo(x0 + w, size * 0.94)
    body.lineTo(x0, size * 0.94)
    body.closeSubpath()
    p.drawPath(body)
    p.setBrush(QColor("#e2e2e2"))
    fold_tri = QPainterPath()
    fold_tri.moveTo(x0 + w - fold, size * 0.06)
    fold_tri.lineTo(x0 + w, size * 0.06 + fold)
    fold_tri.lineTo(x0 + w - fold, size * 0.06 + fold)
    fold_tri.closeSubpath()
    p.drawPath(fold_tri)
    p.end()
    return QIcon(pm)


def icon_back(size=28, color="#333333"):
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    tri = QPolygonF([
        QPointF(size * 0.68, size * 0.14),
        QPointF(size * 0.30, size * 0.5),
        QPointF(size * 0.68, size * 0.86),
    ])
    p.drawPolygon(tri)
    p.end()
    return QIcon(pm)


def icon_home(size=28, color="#333333"):
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    roof = QPolygonF([
        QPointF(size * 0.5, size * 0.10),
        QPointF(size * 0.90, size * 0.48),
        QPointF(size * 0.10, size * 0.48),
    ])
    p.drawPolygon(roof)
    p.drawRect(QRectF(size * 0.24, size * 0.48, size * 0.52, size * 0.40))
    p.end()
    return QIcon(pm)


def icon_search(size=28, color="#555555"):
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 2.4))
    p.setBrush(Qt.NoBrush)
    r = size * 0.28
    cx, cy = size * 0.42, size * 0.42
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx + r * 0.72, cy + r * 0.72), QPointF(size * 0.86, size * 0.86))
    p.end()
    return QIcon(pm)


def icon_theme_toggle(size=28, dark=False):
    """深色模式切換鈕圖示：淺色模式顯示月亮(切到暗)，暗色模式顯示太陽(切到亮)"""
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = "#f0f0f0" if dark else "#333333"
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    if dark:
        # 太陽
        r = size * 0.22
        cx, cy = size * 0.5, size * 0.5
        p.drawEllipse(QPointF(cx, cy), r, r)
        pen = QPen(QColor(color), 2.2)
        p.setPen(pen)
        for i in range(8):
            ang = math.radians(i * 45)
            x1 = cx + math.cos(ang) * r * 1.5
            y1 = cy + math.sin(ang) * r * 1.5
            x2 = cx + math.cos(ang) * r * 2.1
            y2 = cy + math.sin(ang) * r * 2.1
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    else:
        # 月亮
        path = QPainterPath()
        path.addEllipse(QPointF(size * 0.46, size * 0.5), size * 0.32, size * 0.32)
        cut = QPainterPath()
        cut.addEllipse(QPointF(size * 0.60, size * 0.42), size * 0.28, size * 0.28)
        path = path.subtracted(cut)
        p.drawPath(path)
    p.end()
    return QIcon(pm)

def icon_star(size=28, color="#333333"):
    """五角星圖示，用於「快速切換」書籤按鈕"""
    pm = _new_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    cx, cy = size * 0.5, size * 0.5
    outer_r = size * 0.46
    inner_r = size * 0.19
    points = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = outer_r if i % 2 == 0 else inner_r
        points.append(QPointF(cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    p.drawPolygon(QPolygonF(points))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 單一項目的自訂 Widget
# ---------------------------------------------------------------------------
class FileItemWidget(QWidget):
    navigate_requested = Signal(str)      # 點到資料夾名稱 -> 要求進入
    check_toggled = Signal(str, bool)     # 選取狀態改變 (路徑, 是否已選取)

    SEL_BG_LIGHT = "#1a73e8"
    SEL_BG_DARK = "#3a6ea8"

    def __init__(self, full_path: str, is_dir: bool, dark: bool = False, parent=None):
        super().__init__(parent)
        self.full_path = full_path
        self.is_dir = is_dir
        self.dark = dark
        self.selected = False
        name = os.path.basename(full_path) or full_path

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)

        info = ""
        try:
            if is_dir:
                info = "資料夾"
            else:
                size = os.path.getsize(full_path)
                info = human_size(size)
            mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M")
            info = f"{info}　{mtime}"
        except Exception:
            info = ""

        if is_dir:
            self._normal_name_color = "#5fa8ff" if dark else "#1a5aa8"
        else:
            self._normal_name_color = "#f0f0f0" if dark else "#1f1f1f"
        self._normal_info_color = "#aaaaaa" if dark else "#757575"
        self._selected_text_color = "#ffffff"

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.name_label = QLabel(name)
        name_font = QFont("Sans Serif", 16)
        if is_dir:
            name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setStyleSheet("background: transparent;")
        self.info_label = QLabel(info)
        self.info_label.setFont(QFont("Sans Serif", 11))
        self.info_label.setStyleSheet("background: transparent;")
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.info_label)

        self.icon_label = QLabel()
        icon_size = 30
        icon = icon_folder(icon_size) if is_dir else icon_file(icon_size)
        self.icon_label.setPixmap(icon.pixmap(icon_size, icon_size))
        self.icon_label.setFixedSize(icon_size, icon_size)
        self.icon_label.setStyleSheet("background: transparent;")

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(10)
        content_row.addWidget(self.icon_label)
        content_row.addLayout(text_col, stretch=1)

        text_wrap = QWidget()
        text_wrap.setStyleSheet("background: transparent;")
        text_wrap.setLayout(content_row)
        text_wrap.setCursor(Qt.PointingHandCursor)
        text_wrap.mousePressEvent = self._label_mouse_press          # type: ignore
        text_wrap.mouseMoveEvent = self._label_mouse_move            # type: ignore
        text_wrap.mouseReleaseEvent = self._label_mouse_release      # type: ignore
        text_wrap.mouseDoubleClickEvent = self._label_mouse_double_click  # type: ignore
        layout.addWidget(text_wrap, stretch=1)

        self.setMinimumHeight(58)
        self._refresh_colors()

        self.host_list = None
        self._press_y = None
        self._dragging = False
        self._DRAG_THRESHOLD = 6

        self._LONG_PRESS_MS = 500
        self._long_press_fired = False
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(self._LONG_PRESS_MS)
        self._long_press_timer.timeout.connect(self._on_long_press)

    def paintEvent(self, event):
        if self.selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            color = QColor(self.SEL_BG_DARK if self.dark else self.SEL_BG_LIGHT)
            rect = QRectF(self.rect()).adjusted(2, 1, -2, -1)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(rect, 8, 8)
            p.end()
        super().paintEvent(event)

    def _refresh_colors(self):
        if self.selected:
            name_color = self._selected_text_color
            info_color = self._selected_text_color
        else:
            name_color = self._normal_name_color
            info_color = self._normal_info_color
        self.name_label.setStyleSheet(f"color: {name_color}; background: transparent;")
        self.info_label.setStyleSheet(f"color: {info_color}; background: transparent;")

    def _toggle_selected(self):
        self.selected = not self.selected
        self._refresh_colors()
        self.update()
        self.check_toggled.emit(self.full_path, self.selected)

    def _event_y(self, event):
        return event.position().y() if hasattr(event, "position") else event.y()

    def _label_mouse_press(self, event):
        self._press_y = self._event_y(event)
        self._dragging = False
        self._long_press_fired = False
        if self.is_dir:
            self._long_press_timer.start()
        event.accept()

    def _label_mouse_move(self, event):
        if self._press_y is None:
            return
        y = self._event_y(event)
        delta = self._press_y - y
        if abs(delta) > self._DRAG_THRESHOLD:
            self._dragging = True
            if self.is_dir:
                self._long_press_timer.stop()
        event.accept()

    def _on_long_press(self):
        if self._dragging or not self.is_dir:
            return
        self._long_press_fired = True
        if not self.selected:
            self._toggle_selected()

    def _label_mouse_release(self, event):
        if self.is_dir:
            self._long_press_timer.stop()
            if not self._dragging and not self._long_press_fired:
                if self.selected:
                    self._toggle_selected()
        else:
            if not self._dragging:
                self._toggle_selected()
        self._press_y = None
        self._dragging = False
        self._long_press_fired = False
        event.accept()

    def _label_mouse_double_click(self, event):
        if self.is_dir:
            self.navigate_requested.emit(self.full_path)
        event.accept()

    def set_checked(self, checked: bool):
        self.selected = checked
        self._refresh_colors()
        self.update()


# ---------------------------------------------------------------------------
# 新增資料夾對話框
# ---------------------------------------------------------------------------
class CreateFolderDialog(QDialog):
    def __init__(self, dark_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增資料夾")
        self.setModal(True)
        self.setStyleSheet(dialog_border_style(dark_mode))
        if parent is not None:
            self.setMaximumWidth(parent.width())

        text_color = "#f0f0f0" if dark_mode else "#1f1f1f"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        label = QLabel("資料夾名稱：")
        label.setStyleSheet(f"color: {text_color};")
        layout.addWidget(label)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("新資料夾")
        self.name_edit.setMinimumHeight(52)
        self.name_edit.setFont(QFont("Sans Serif", 14))
        layout.addWidget(self.name_edit)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("確定")
        for b in (cancel_btn, ok_btn):
            b.setMinimumHeight(52)
            b.setFont(QFont("Sans Serif", 15, QFont.Bold))
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_name(self):
        return self.name_edit.text().strip()


# ---------------------------------------------------------------------------
# 重新命名對話框
# ---------------------------------------------------------------------------
class RenameDialog(QDialog):
    def __init__(self, current_name, dark_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重新命名")
        self.setModal(True)
        self.setStyleSheet(dialog_border_style(dark_mode))
        if parent is not None:
            self.setMaximumWidth(parent.width())

        text_color = "#f0f0f0" if dark_mode else "#1f1f1f"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        label = QLabel("新名稱：")
        label.setStyleSheet(f"color: {text_color};")
        layout.addWidget(label)

        self.name_edit = QLineEdit(current_name)
        self.name_edit.setMinimumHeight(52)
        self.name_edit.setFont(QFont("Sans Serif", 14))
        layout.addWidget(self.name_edit)

        base, ext = os.path.splitext(current_name)
        if ext:
            self.name_edit.setSelection(0, len(base))
        else:
            self.name_edit.selectAll()

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("確定")
        for b in (cancel_btn, ok_btn):
            b.setMinimumHeight(52)
            b.setFont(QFont("Sans Serif", 15, QFont.Bold))
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_name(self):
        return self.name_edit.text().strip()


# ---------------------------------------------------------------------------
# 變更屬性（權限）對話框
# ---------------------------------------------------------------------------
class PermissionDialog(QDialog):
    def __init__(self, paths, dark_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("變更屬性")
        self.setModal(True)
        self.setStyleSheet(dialog_border_style(dark_mode))
        if parent is not None:
            self.setMaximumWidth(parent.width())

        self.paths = paths
        text_color = "#f0f0f0" if dark_mode else "#1f1f1f"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        if len(paths) == 1:
            title = os.path.basename(paths[0].rstrip("/")) or paths[0]
            try:
                current_mode = stat_module.S_IMODE(os.stat(paths[0]).st_mode)
            except Exception:
                current_mode = 0o644
        else:
            title = f"已選取 {len(paths)} 個項目（將套用相同權限）"
            current_mode = 0o644

        info = QLabel(title)
        info.setStyleSheet(f"color: {text_color};")
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QGridLayout()
        grid.setSpacing(8)
        headers = ["", "讀取", "寫入", "執行"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet(f"color: {text_color}; font-weight: bold;")
            grid.addWidget(lbl, 0, col)

        rows = [("擁有者", 0o400, 0o200, 0o100),
                ("群組", 0o040, 0o020, 0o010),
                ("其他", 0o004, 0o002, 0o001)]

        self.checks = {}
        for row_idx, (row_label, r_bit, w_bit, x_bit) in enumerate(rows, start=1):
            lbl = QLabel(row_label)
            lbl.setStyleSheet(f"color: {text_color};")
            grid.addWidget(lbl, row_idx, 0)
            for col_idx, bit in enumerate((r_bit, w_bit, x_bit), start=1):
                cb = QCheckBox()
                cb.setChecked(bool(current_mode & bit))
                cb.stateChanged.connect(self._update_octal_label)
                grid.addWidget(cb, row_idx, col_idx)
                self.checks[bit] = cb

        layout.addLayout(grid)

        self.octal_label = QLabel()
        self.octal_label.setStyleSheet(f"color: {text_color};")
        layout.addWidget(self.octal_label)
        self._update_octal_label()

        quick_row = QHBoxLayout()
        btn_exec = QPushButton("設為可執行 (755)")
        btn_normal = QPushButton("一般檔案 (644)")
        for b in (btn_exec, btn_normal):
            b.setMinimumHeight(48)
        btn_exec.clicked.connect(lambda: self._apply_preset(0o755))
        btn_normal.clicked.connect(lambda: self._apply_preset(0o644))
        quick_row.addWidget(btn_exec)
        quick_row.addWidget(btn_normal)
        layout.addLayout(quick_row)

        has_dir = any(os.path.isdir(p) for p in paths)
        self.recursive_check = QCheckBox("套用到資料夾內所有子項目（遞迴）")
        self.recursive_check.setStyleSheet(f"color: {text_color};")
        self.recursive_check.setVisible(has_dir)
        layout.addWidget(self.recursive_check)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("確定")
        for b in (cancel_btn, ok_btn):
            b.setMinimumHeight(52)
            b.setFont(QFont("Sans Serif", 15, QFont.Bold))
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _current_mode(self):
        mode = 0
        for bit, cb in self.checks.items():
            if cb.isChecked():
                mode |= bit
        return mode

    def _update_octal_label(self, *_):
        mode = self._current_mode()
        self.octal_label.setText(f"權限值：{oct(mode)[2:].zfill(3)}")

    def _apply_preset(self, mode):
        for bit, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(mode & bit))
            cb.blockSignals(False)
        self._update_octal_label()

    def get_result(self):
        return self._current_mode(), self.recursive_check.isChecked()


# ---------------------------------------------------------------------------
# 確定選取後彈出的檔案操作選單
# ---------------------------------------------------------------------------
class FileActionSheet(QDialog):
    def __init__(self, paths, dark_mode, window):
        super().__init__(window)
        self.paths = paths
        self.window_ref = window
        self.setWindowTitle("檔案操作")
        self.setModal(True)
        self.setStyleSheet(dialog_border_style(dark_mode))
        self.setMaximumWidth(window.width())

        text_color = "#f0f0f0" if dark_mode else "#1f1f1f"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        count = len(paths)
        preview_names = "\n".join(os.path.basename(p.rstrip("/")) for p in paths[:5])
        if count > 5:
            preview_names += f"\n...等共 {count} 個項目"
        info_label = QLabel(f"已選取 {count} 個項目：\n{preview_names}")
        info_label.setStyleSheet(f"color: {text_color};")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        def make_btn(text, handler, enabled=True):
            b = QPushButton(text)
            b.setMinimumHeight(54)
            b.setFont(QFont("Sans Serif", 15))
            b.setEnabled(enabled)
            b.clicked.connect(handler)
            layout.addWidget(b)
            return b

        make_btn("複製", self._do_copy)
        make_btn("刪除", self._do_delete)
        make_btn("變更屬性", self._do_attrs)

        single_file = count == 1 and not os.path.isdir(paths[0])
        make_btn("使用其他應用程式開啟", self._do_open_with, enabled=single_file)

        single_item = count == 1
        make_btn("重新命名", self._do_rename, enabled=single_item)

        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(54)
        cancel_btn.setFont(QFont("Sans Serif", 15, QFont.Bold))
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

    def _do_copy(self):
        self.window_ref.do_copy(self.paths)
        self.accept()

    def _do_delete(self):
        self.window_ref.do_delete(self.paths)
        self.accept()

    def _do_attrs(self):
        self.window_ref.do_change_attributes(self.paths)
        self.accept()

    def _do_open_with(self):
        self.window_ref.open_with_other_app(self.paths[0])
        self.accept()

    def _do_rename(self):
        self.window_ref.do_rename(self.paths[0])
        self.accept()


# ---------------------------------------------------------------------------
# 背景工作執行緒
# ---------------------------------------------------------------------------

# 追蹤所有「正在執行中」的背景工作執行緒，刻意放在模組層級而非依附於
# 特定 FilePickerWindow 實例。原因：pick_mode（選圖/選資料夾）用完即丟，
# 使用者選定後視窗會馬上 close()+deleteLater()。若 Worker 物件只被該視窗
# 實例的屬性持有，一旦視窗被回收，而背景執行緒（例如正在 os.listdir()
# 掃描很大或很慢的資料夾）還沒跑完，PySide 在銷毀這個仍在執行中的
# QThread 物件時，其解構子會呼叫 wait() 等待執行緒真正結束——這會讓
# 主執行緒整個卡住，使用者會覺得「點了按鈕之後過了很久才有反應」。
# 改成模組層級持有，執行緒即使在來源視窗關閉後仍可以在背景跑完，
# 完成時才從這裡移除，不會被提早強制銷毀。
_ACTIVE_WORKERS = set()


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self.func = func
        self.on_success = None
        self.on_error = None

    def run(self):
        try:
            result = self.func()
        except Exception as e:
            self.failed.emit(e)
        else:
            self.succeeded.emit(result)


# ---------------------------------------------------------------------------
# 忙碌中遮罩
# ---------------------------------------------------------------------------
class BusyOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._angle = 0
        self._label_text = "處理中..."
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._on_tick)
        self.hide()

    def start(self, text="處理中..."):
        self._label_text = text
        self._angle = 0
        self.resize_to_parent()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def resize_to_parent(self):
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())

    def _on_tick(self):
        self._angle = (self._angle + 16) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

        cx, cy = self.width() / 2, self.height() / 2 - 10
        radius = 22

        pen = QPen(QColor("#ffffff"), 5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        start_angle = self._angle * 16
        span_angle = 100 * 16
        p.drawArc(rect, -start_angle, span_angle)

        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Sans Serif", 14, QFont.Bold))
        text_rect = QRectF(0, cy + radius + 14, self.width(), 40)
        p.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, self._label_text)
        p.end()

    def mousePressEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()


# ---------------------------------------------------------------------------
# 主視窗
# ---------------------------------------------------------------------------
class FilePickerWindow(QWidget):
    # pick_mode=True 時，使用者選定符合條件的單一檔案後會發出這個訊號 (完整檔案路徑)，
    # 供呼叫端（例如其他程式的「選取測試圖片」按鈕）連接使用。
    image_selected = Signal(str)
    # pick_folder=True 時，使用者選定資料夾後會發出這個訊號 (完整資料夾路徑)，
    # 供呼叫端（例如其他程式的「選擇儲存位置」按鈕）連接使用。
    folder_selected = Signal(str)

    def __init__(self, pick_mode: bool = False, file_filter_exts=None, start_dir=None,
                 pick_folder: bool = False):
        """
        pick_mode:
            False (預設) -> 原本完整的檔案總管行為（複製/刪除/變更屬性...）。
            True         -> 精簡的「選取檔案」模式：按下「確定」後不會跳出檔案操作選單，
                            而是驗證選取的單一檔案是否符合 file_filter_exts，
                            符合就發出 image_selected 訊號並關閉視窗。
        file_filter_exts:
            pick_mode=True 時，限制清單只顯示副檔名符合的檔案（資料夾一律顯示）。
            例如 ['.png', '.jpg', '.jpeg']。傳入 None 則不過濾檔案類型。
        start_dir:
            指定開啟時預設進入的資料夾；未提供則沿用原本的「首頁」路徑邏輯。
        pick_folder:
            True -> 精簡的「選取資料夾」模式：導覽到目標資料夾後按「選取此資料夾」，
                    會以目前所在資料夾（或勾選的單一資料夾）發出 folder_selected 訊號並關閉視窗。
                    可與 pick_mode 一起視為簡化操作介面，但 pick_folder 優先於檔案選取邏輯。
        """
        super().__init__()
        self.pick_folder = pick_folder
        self.pick_mode = pick_mode or pick_folder
        self.file_filter_exts = (
            {e.lower() for e in file_filter_exts} if file_filter_exts else None
        )

        if pick_folder:
            title = "選取資料夾"
        elif pick_mode:
            title = "選取圖片檔案"
        else:
            title = "檔案選擇器"
        self.setWindowTitle(title)
        self.resize(480, 800)

        self.home_dir = load_path_config("home_dir") or DEFAULT_ROOT
        self.quick_dir = load_path_config("quick_dir") or DEFAULT_RAPID
        self.active_bookmark = "home"
        self.current_dir = start_dir if (start_dir and os.path.isdir(start_dir)) else self.home_dir
        self.selected_paths = set()
        self.history = []
        self.dark_mode = load_theme_config()  # 開啟時讀取 JSON 設定檔的主題模式
        self.clipboard_paths = []
        self._current_entries = []
        self._busy_count = 0

        self._build_ui()
        if self.pick_mode:
            self._apply_pick_mode_ui()
        self._apply_theme()
        self.busy_overlay = BusyOverlay(self)
        self.load_directory(self.current_dir)

    def _apply_pick_mode_ui(self):
        """精簡選取模式下，隱藏跟複製/貼上無關的按鈕，並依模式調整「確定」按鈕文字。"""
        self.btn_paste.setVisible(False)
        if self.pick_folder:
            # 選資料夾模式：保留「新增資料夾」，方便直接建立新資料夾當作儲存位置。
            self.btn_new_folder.setVisible(True)
            self.btn_confirm.setText("選取此資料夾")
            self.selected_label.setToolTip("可直接按「選取此資料夾」使用目前所在資料夾，\n"
                                            "或勾選單一資料夾後再按下確定。")
        else:
            self.btn_new_folder.setVisible(False)
            self.btn_confirm.setText("選取此圖片")
            if self.file_filter_exts:
                exts_text = "、".join(sorted(self.file_filter_exts))
                self.selected_label.setToolTip(f"僅顯示：{exts_text}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "busy_overlay"):
            self.busy_overlay.resize_to_parent()

    # ---------------- UI 建構 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 上方導覽列：上一層 / 首頁 / 快速切換 / 深色模式切換
        nav_row = QHBoxLayout()
        self.btn_up = QPushButton(icon_back(24), "")
        self.btn_up.setIconSize(QSize(24, 24))
        self.btn_up.setMinimumHeight(56)
        self.btn_up.setFixedWidth(64)
        self.btn_up.clicked.connect(self.go_up)

        self.btn_home = QPushButton(icon_home(24), "")
        self.btn_home.setIconSize(QSize(24, 24))
        self.btn_home.setMinimumHeight(56)
        self.btn_home.setFixedWidth(64)
        self.btn_home.clicked.connect(self.go_home)

        self.btn_quick = QPushButton(icon_star(24), "")
        self.btn_quick.setIconSize(QSize(24, 24))
        self.btn_quick.setMinimumHeight(56)
        self.btn_quick.setFixedWidth(64)
        self.btn_quick.clicked.connect(self.go_quick_switch)

        self.btn_theme = QPushButton(icon_theme_toggle(24, dark=self.dark_mode), "")
        self.btn_theme.setIconSize(QSize(24, 24))
        self.btn_theme.setMinimumHeight(56)
        self.btn_theme.setFixedWidth(64)
        self.btn_theme.clicked.connect(self.toggle_theme)

        nav_row.addWidget(self.btn_up)
        nav_row.addWidget(self.btn_home)
        nav_row.addWidget(self.btn_quick)
        nav_row.addWidget(self.btn_theme)
        nav_row.addStretch(1)
        root.addLayout(nav_row)

        self.path_edit = QLineEdit()
        self.path_edit.setMinimumHeight(52)
        self.path_edit.setFont(QFont("Sans Serif", 14))
        self.path_edit.returnPressed.connect(self._on_path_edit_enter)
        root.addWidget(self.path_edit)

        # 資料夾操作列：新增資料夾
        folder_ops_row = QHBoxLayout()
        self.btn_new_folder = QPushButton("新增資料夾")
        self.btn_new_folder.setMinimumHeight(50)
        self.btn_new_folder.setFont(QFont("Sans Serif", 13, QFont.Bold))
        self.btn_new_folder.clicked.connect(self.create_new_folder)
        folder_ops_row.addWidget(self.btn_new_folder)
        root.addLayout(folder_ops_row)

        # 搜尋列
        self.search_edit = QLineEdit()
        self.search_action = self.search_edit.addAction(icon_search(20), QLineEdit.LeadingPosition)
        self.search_edit.setPlaceholderText("搜尋檔名...")
        self.search_edit.setMinimumHeight(52)
        self.search_edit.setFont(QFont("Sans Serif", 14))
        self.search_edit.textChanged.connect(self._on_search_changed)
        root.addWidget(self.search_edit)

        # 檔案清單
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(4)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        QScroller.grabGesture(self.list_widget.viewport(), QScroller.LeftMouseButtonGesture)
        root.addWidget(self.list_widget, stretch=1)

        # 分隔線
        self.divider_line = QFrame()
        self.divider_line.setFrameShape(QFrame.HLine)
        self.divider_line.setStyleSheet("color: #d0d0d0;")
        root.addWidget(self.divider_line)

        # 已選數量標籤
        self.selected_label = QLabel("已選取 0 個項目")
        self.selected_label.setFont(QFont("Sans Serif", 13))
        self.selected_label.setStyleSheet("color: #444444;")
        root.addWidget(self.selected_label)

        # 底部操作列
        btn_row = QHBoxLayout()
        self.btn_select_all = QPushButton("全選")
        self.btn_clear = QPushButton("取消")
        self.btn_paste = QPushButton("貼上")
        self.btn_confirm = QPushButton("執行")

        for b in (self.btn_select_all, self.btn_clear, self.btn_paste, self.btn_confirm):
            b.setMinimumHeight(58)
            b.setFont(QFont("Sans Serif", 15, QFont.Bold))

        self.btn_select_all.clicked.connect(self.select_all_in_view)
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_paste.clicked.connect(self.do_paste)
        self.btn_confirm.clicked.connect(self.confirm_selection)
        self.btn_paste.setEnabled(False)

        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_paste)
        btn_row.addWidget(self.btn_confirm)
        root.addLayout(btn_row)

    def _apply_theme(self):
        app = QApplication.instance()
        _check_icon_path = get_checkbox_icon_path().replace("\\", "/")
        if self.dark_mode:
            app.setStyleSheet(f"""
                QWidget {{ background-color: #121212; }}
                QLineEdit {{
                    background-color: #1e1e1e; color: #f0f0f0;
                    border: 1px solid #444; border-radius: 8px; padding: 6px 10px;
                }}
                QListWidget {{
                    background-color: #1a1a1a; border: 1px solid #333; border-radius: 8px;
                }}
                QListWidget::item {{ border-bottom: 1px solid #2a2a2a; }}
                QListWidget::item:selected {{ background-color: transparent; }}
                QPushButton {{
                    background-color: #2a2a2a; color: #f0f0f0;
                    border: 1px solid #444; border-radius: 10px;
                }}
                QPushButton:pressed {{ background-color: #3a5a7a; }}
                QPushButton:disabled {{ background-color: #1e1e1e; color: #666666; }}
                QScrollBar:vertical {{ background: #2a2a2a; width: 24px; margin: 24px 0 24px 0; }}
                QScrollBar::handle:vertical {{ background: #666; border-radius: 8px; min-height: 30px; }}
                QScrollBar::add-line:vertical {{
                    height: 24px; subcontrol-position: bottom; subcontrol-origin: margin;
                }}
                QScrollBar::sub-line:vertical {{
                    height: 24px; subcontrol-position: top; subcontrol-origin: margin;
                }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

                QScrollBar:horizontal {{ background: #2a2a2a; height: 24px; margin: 0 24px 0 24px; }}
                QScrollBar::handle:horizontal {{ background: #666; border-radius: 8px; min-width: 30px; }}
                QScrollBar::add-line:horizontal {{
                    width: 24px; subcontrol-position: right; subcontrol-origin: margin;
                }}
                QScrollBar::sub-line:horizontal {{
                    width: 24px; subcontrol-position: left; subcontrol-origin: margin;
                }}
                QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

                QMessageBox {{ background-color: #1e1e1e; border: 1px solid #555; border-radius: 6px; }}
                QMessageBox QLabel {{ color: #f0f0f0; background-color: #1e1e1e; }}

                QCheckBox {{ color: #f0f0f0; spacing: 8px; }}
                QCheckBox::indicator {{
                    width: 22px; height: 22px;
                    border: 2px solid {BOOKMARK_ACTIVE_COLOR};
                    border-radius: 4px;
                    background-color: #1e1e1e;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {BOOKMARK_ACTIVE_COLOR};
                    image: url("{_check_icon_path}");
                }}
                QCheckBox::indicator:disabled {{
                    border: 2px solid #555555; background-color: #2a2a2a;
                }}
            """)
        else:
            app.setStyleSheet(f"""
                QWidget {{ background-color: #fafafa; }}
                QLineEdit {{
                    background-color: #ffffff; color: #1f1f1f;
                    border: 1px solid #ccc; border-radius: 8px; padding: 6px 10px;
                }}
                QListWidget {{
                    background-color: #ffffff; border: 1px solid #ddd; border-radius: 8px;
                }}
                QListWidget::item {{ border-bottom: 1px solid #eee; }}
                QListWidget::item:selected {{ background-color: transparent; }}
                QPushButton {{
                    background-color: #f0f0f0; color: #1f1f1f;
                    border: 1px solid #ccc; border-radius: 10px;
                }}
                QPushButton:pressed {{ background-color: #cfe3fb; }}
                QPushButton:disabled {{ background-color: #f5f5f5; color: #aaaaaa; }}
                QScrollBar:vertical {{ background: #f0f0f0; width: 24px; margin: 24px 0 24px 0; }}
                QScrollBar::handle:vertical {{ background: #bbb; border-radius: 8px; min-height: 30px; }}
                QScrollBar::add-line:vertical {{
                    height: 24px; subcontrol-position: bottom; subcontrol-origin: margin;
                }}
                QScrollBar::sub-line:vertical {{
                    height: 24px; subcontrol-position: top; subcontrol-origin: margin;
                }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

                QScrollBar:horizontal {{ background: #f0f0f0; height: 24px; margin: 0 24px 0 24px; }}
                QScrollBar::handle:horizontal {{ background: #bbb; border-radius: 8px; min-width: 30px; }}
                QScrollBar::add-line:horizontal {{
                    width: 24px; subcontrol-position: right; subcontrol-origin: margin;
                }}
                QScrollBar::sub-line:horizontal {{
                    width: 24px; subcontrol-position: left; subcontrol-origin: margin;
                }}
                QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

                QMessageBox {{ background-color: #ffffff; border: 1px solid #999; border-radius: 6px; }}
                QMessageBox QLabel {{ color: #1f1f1f; background-color: #ffffff; }}

                QCheckBox {{ color: #1f1f1f; spacing: 8px; }}
                QCheckBox::indicator {{
                    width: 22px; height: 22px;
                    border: 2px solid {BOOKMARK_ACTIVE_COLOR};
                    border-radius: 4px;
                    background-color: #ffffff;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {BOOKMARK_ACTIVE_COLOR};
                    image: url("{_check_icon_path}");
                }}
                QCheckBox::indicator:disabled {{
                    border: 2px solid #cccccc; background-color: #f0f0f0;
                }}
            """)

        icon_color = "#e8e8e8" if self.dark_mode else "#333333"
        self.btn_up.setIcon(icon_back(24, icon_color))
        self._update_bookmark_icons()
        self.btn_theme.setIcon(icon_theme_toggle(24, dark=self.dark_mode))
        self.search_action.setIcon(icon_search(20, icon_color))
        self.divider_line.setStyleSheet(f"color: {'#333333' if self.dark_mode else '#d0d0d0'};")
        self.selected_label.setStyleSheet(f"color: {'#cccccc' if self.dark_mode else '#444444'};")

    def _update_bookmark_icons(self):
        home_color = BOOKMARK_ACTIVE_COLOR if self.active_bookmark == "home" else BOOKMARK_INACTIVE_COLOR
        quick_color = BOOKMARK_ACTIVE_COLOR if self.active_bookmark == "quick" else BOOKMARK_INACTIVE_COLOR
        self.btn_home.setIcon(icon_home(24, home_color))
        self.btn_quick.setIcon(icon_star(24, quick_color))

    # ---------------- 背景工作 / 忙碌動畫 ----------------
    def _run_busy(self, text, func, on_success=None, on_error=None):
        self._busy_count += 1
        self.busy_overlay.start(text)

        worker = Worker(func)
        worker.on_success = on_success
        worker.on_error = on_error
        worker.start_time = time.monotonic()
        _ACTIVE_WORKERS.add(worker)

        worker.succeeded.connect(self._on_worker_succeeded)
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    def _remaining_min_busy_ms(self, worker) -> int:
        elapsed_ms = (time.monotonic() - getattr(worker, "start_time", time.monotonic())) * 1000
        return max(0, int(MIN_BUSY_MS - elapsed_ms))

    def _on_worker_succeeded(self, result):
        worker = self.sender()
        try:
            remaining = self._remaining_min_busy_ms(worker)
        except RuntimeError:
            # 視窗本身（self）已經被關閉銷毀，只需把執行緒從追蹤集合移除即可。
            _ACTIVE_WORKERS.discard(worker)
            return
        if remaining > 0:
            QTimer.singleShot(remaining, lambda: self._complete_success(worker, result))
        else:
            self._complete_success(worker, result)

    def _on_worker_failed(self, err):
        worker = self.sender()
        try:
            remaining = self._remaining_min_busy_ms(worker)
        except RuntimeError:
            _ACTIVE_WORKERS.discard(worker)
            return
        if remaining > 0:
            QTimer.singleShot(remaining, lambda: self._complete_error(worker, err))
        else:
            self._complete_error(worker, err)

    def _complete_success(self, worker, result):
        try:
            self._finish_worker(worker)
        except RuntimeError:
            _ACTIVE_WORKERS.discard(worker)
            return
        if worker is not None and worker.on_success:
            try:
                worker.on_success(result)
            except RuntimeError:
                # 對應的視窗/元件已被銷毀，忽略即可（該視窗已經不需要這個結果了）。
                pass

    def _complete_error(self, worker, err):
        try:
            self._finish_worker(worker)
        except RuntimeError:
            _ACTIVE_WORKERS.discard(worker)
            return
        if worker is not None and worker.on_error:
            try:
                worker.on_error(err)
            except RuntimeError:
                pass
        else:
            try:
                QMessageBox.warning(self, "發生錯誤", str(err))
            except RuntimeError:
                pass

    def _finish_worker(self, worker):
        _ACTIVE_WORKERS.discard(worker)
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.busy_overlay.stop()

    def toggle_theme(self):
        """切換主題樣式並同步更新 JSON 檔寫入記憶"""
        self.dark_mode = not self.dark_mode
        save_theme_config(self.dark_mode)
        self._apply_theme()
        self._populate_list(self._current_entries, filter_text=self.search_edit.text())

    # ---------------- 資料夾載入 ----------------
    def load_directory(self, path: str, push_history: bool = True):
        folder_name = os.path.basename(path.rstrip("/")) or path

        def _work():
            return os.listdir(path)

        def _on_ok(entries):
            if push_history and path != self.current_dir:
                self.history.append(self.current_dir)
            self.current_dir = path
            self.path_edit.setText(path)
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self._populate_list(entries)

        def _on_err(err):
            if isinstance(err, PermissionError):
                QMessageBox.warning(self, "權限不足", "無法讀取此資料夾，請確認已授予儲存權限。")
            else:
                QMessageBox.warning(self, "錯誤", f"無法開啟資料夾：\n{err}")

        self._run_busy(f"讀取「{folder_name}」中...", _work, _on_ok, _on_err)

    def _refresh_current_dir(self):
        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            try:
                return os.listdir(target_dir)
            except Exception:
                return []

        def _on_ok(entries):
            self._populate_list(entries, filter_text=filter_text)

        self._run_busy("重新整理中...", _work, _on_ok)

    def _populate_list(self, entries, filter_text: str = ""):
        self._current_entries = entries
        self.list_widget.clear()
        filter_text = filter_text.lower().strip()

        dirs, files = [], []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(self.current_dir, name)
            if filter_text and filter_text not in name.lower():
                continue
            if os.path.isdir(full):
                dirs.append(full)
            else:
                if self.pick_mode and self.file_filter_exts:
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in self.file_filter_exts:
                        continue
                files.append(full)

        dirs.sort(key=lambda p: os.path.basename(p).lower())
        files.sort(key=lambda p: os.path.basename(p).lower())

        for full in dirs + files:
            is_dir = full in dirs
            item = QListWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            widget = FileItemWidget(full, is_dir, dark=self.dark_mode)
            widget.host_list = self.list_widget
            widget.navigate_requested.connect(self.load_directory)
            widget.check_toggled.connect(self._on_item_checked)
            widget.set_checked(full in self.selected_paths)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        self._update_selected_label()

    # ---------------- 事件處理 ----------------
    def _on_search_changed(self, text):
        self._populate_list(self._current_entries, filter_text=text)

    def _on_path_edit_enter(self):
        new_path = self.path_edit.text().strip()
        if not os.path.isdir(new_path):
            QMessageBox.warning(self, "路徑錯誤", "找不到此資料夾。")
            self.path_edit.setText(self.current_dir)
            return

        self._set_bookmark_directory(new_path)
        self.load_directory(new_path)

    def _set_bookmark_directory(self, path):
        if self.active_bookmark == "quick":
            self.quick_dir = path
            config_key, label = "quick_dir", "快速切換"
        else:
            self.home_dir = path
            config_key, label = "home_dir", "首頁"

        try:
            save_path_config(config_key, path)
            QMessageBox.information(
                self, f"已更新「{label}」路徑",
                f"已將下列路徑設為「{label}」，並儲存到設定檔：\n{path}\n\n"
                f"下次按「{label}」按鈕或重新開啟程式都會使用這個路徑。")
        except Exception as e:
            QMessageBox.warning(
                self, "設定檔寫入失敗",
                f"「{label}」路徑已更新為：\n{path}\n\n但寫入設定檔時發生錯誤：\n{e}")

    def _on_item_checked(self, path, checked):
        if checked:
            self.selected_paths.add(path)
        else:
            self.selected_paths.discard(path)
        self._update_selected_label()

    def _update_selected_label(self):
        self.selected_label.setText(f"已選取 {len(self.selected_paths)} 個項目")

    # ---------------- 導覽 ----------------
    def go_up(self):
        parent = os.path.dirname(self.current_dir.rstrip("/"))
        if parent and parent != self.current_dir:
            self.load_directory(parent)

    def go_home(self):
        self.active_bookmark = "home"
        self._update_bookmark_icons()
        self.load_directory(self.home_dir)

    def go_quick_switch(self):
        self.active_bookmark = "quick"
        self._update_bookmark_icons()
        if self.quick_dir and os.path.isdir(self.quick_dir):
            self.load_directory(self.quick_dir)
        else:
            QMessageBox.information(
                self, "尚未設定快速切換路徑",
                "「快速切換」目前還沒有設定路徑。\n"
                "請在路徑欄輸入想要的路徑後按 Enter，即可把它設為快速切換路徑。")

    # ---------------- 選取操作 ----------------
    def select_all_in_view(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            if widget is not None:
                widget.set_checked(True)
                self.selected_paths.add(widget.full_path)
        self._update_selected_label()

    def clear_selection(self):
        self.selected_paths.clear()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            widget = self.list_widget.itemWidget(item)
            if widget is not None:
                widget.set_checked(False)
        self._update_selected_label()

    def confirm_selection(self):
        if self.pick_folder:
            self._finish_pick_folder()
            return
        if not self.selected_paths:
            QMessageBox.information(self, "提示", "尚未選取任何檔案。")
            return
        paths = sorted(self.selected_paths)
        self.process_selected_files(paths)

    def _finish_pick_folder(self):
        """pick_folder 專用：以目前所在資料夾，或勾選的單一資料夾，作為選取結果。"""
        if len(self.selected_paths) == 1:
            candidate = next(iter(self.selected_paths))
            if not os.path.isdir(candidate):
                QMessageBox.information(self, "提示", "請勾選資料夾，或不勾選任何項目以選取目前所在資料夾。")
                return
            target = candidate
        elif len(self.selected_paths) > 1:
            QMessageBox.information(self, "提示", "請只勾選一個資料夾，或不勾選以選取目前所在資料夾。")
            return
        else:
            target = self.current_dir
        self.folder_selected.emit(target)
        self.close()

    # ---------------- 後續處理 ----------------
    def process_selected_files(self, paths):
        if self.pick_mode:
            self._finish_pick(paths)
            return
        dlg = FileActionSheet(paths, self.dark_mode, self)
        dlg.exec()

    def _finish_pick(self, paths):
        """pick_mode 專用：驗證選取結果，符合條件就發出 image_selected 並關閉視窗。"""
        if len(paths) != 1:
            QMessageBox.information(self, "提示", "請只選取一個檔案。")
            return
        path = paths[0]
        if os.path.isdir(path):
            QMessageBox.information(self, "提示", "請選取檔案，不要選取資料夾。")
            return
        if self.file_filter_exts:
            ext = os.path.splitext(path)[1].lower()
            if ext not in self.file_filter_exts:
                exts_text = "、".join(sorted(self.file_filter_exts))
                QMessageBox.warning(self, "檔案類型不符", f"請選取下列類型的檔案：\n{exts_text}")
                return
        self.image_selected.emit(path)
        self.close()

    # ---------------- 新增資料夾 ----------------
    def create_new_folder(self):
        dlg = CreateFolderDialog(self.dark_mode, self)
        if dlg.exec() != QDialog.Accepted:
            return
        name = dlg.get_name()
        if not name:
            return
        new_path = os.path.join(self.current_dir, name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "建立失敗", "已存在同名的檔案或資料夾。")
            return

        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            os.makedirs(new_path)
            return os.listdir(target_dir)

        def _on_ok(entries):
            self._populate_list(entries, filter_text=filter_text)

        def _on_err(err):
            QMessageBox.warning(self, "建立失敗", str(err))

        self._run_busy("建立資料夾中...", _work, _on_ok, _on_err)

    # ---------------- 複製 / 貼上 ----------------
    def do_copy(self, paths):
        self.clipboard_paths = list(paths)
        self.btn_paste.setEnabled(bool(self.clipboard_paths))
        QMessageBox.information(
            self, "已複製",
            f"已複製 {len(paths)} 個項目，切換到目的資料夾後按「貼上」即可。")

    def _unique_dest_path(self, target_dir, name):
        base, ext = os.path.splitext(name)
        candidate = os.path.join(target_dir, name)
        i = 1
        while os.path.exists(candidate):
            candidate = os.path.join(target_dir, f"{base} ({i}){ext}")
            i += 1
        return candidate

    def do_paste(self):
        if not self.clipboard_paths:
            return

        clipboard_snapshot = list(self.clipboard_paths)
        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            ok, fail = 0, []
            for src in clipboard_snapshot:
                name = os.path.basename(src.rstrip("/")) or src
                if not os.path.exists(src):
                    fail.append(f"{name}（來源已不存在）")
                    continue
                try:
                    if os.path.isdir(src):
                        normalized_src = os.path.abspath(src)
                        normalized_target = os.path.abspath(target_dir)
                        if (normalized_target == normalized_src
                                or normalized_target.startswith(normalized_src + os.sep)):
                            fail.append(f"{name}（無法複製到自身或其子資料夾內）")
                            continue
                        dest = self._unique_dest_path(target_dir, name)
                        shutil.copytree(src, dest)
                    else:
                        dest = self._unique_dest_path(target_dir, name)
                        shutil.copy2(src, dest)
                    ok += 1
                except Exception as e:
                    fail.append(f"{name}（{e}）")
            entries = os.listdir(target_dir)
            return ok, fail, entries

        def _on_ok(result):
            ok, fail, entries = result
            self._populate_list(entries, filter_text=filter_text)
            msg = f"成功貼上 {ok} 個項目。"
            if fail:
                msg += "\n\n失敗：\n" + "\n".join(fail[:10])
            QMessageBox.information(self, "貼上完成", msg)

        self._run_busy("貼上中...", _work, _on_ok)

    # ---------------- 刪除 ----------------
    def do_delete(self, paths):
        names = "\n".join(os.path.basename(p.rstrip("/")) for p in paths[:10])
        if len(paths) > 10:
            names += f"\n...等共 {len(paths)} 個項目"
        reply = QMessageBox.question(
            self, "確認刪除",
            f"確定要刪除以下 {len(paths)} 個項目嗎？此動作無法復原！\n\n{names}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        paths_snapshot = list(paths)
        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            ok, fail = 0, []
            removed = []
            for p in paths_snapshot:
                name = os.path.basename(p.rstrip("/")) or p
                try:
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    removed.append(p)
                    ok += 1
                except Exception as e:
                    fail.append(f"{name}（{e}）")
            entries = os.listdir(target_dir)
            return ok, fail, removed, entries

        def _on_ok(result):
            ok, fail, removed, entries = result
            for p in removed:
                self.selected_paths.discard(p)
            self._populate_list(entries, filter_text=filter_text)
            msg = f"已刪除 {ok} 個項目。"
            if fail:
                msg += "\n\n失敗：\n" + "\n".join(fail[:10])
            QMessageBox.information(self, "刪除完成", msg)

        self._run_busy("刪除中...", _work, _on_ok)

    # ---------------- 變更屬性（權限，可設定執行屬性） ----------------
    def do_change_attributes(self, paths):
        dlg = PermissionDialog(paths, self.dark_mode, self)
        if dlg.exec() != QDialog.Accepted:
            return
        mode, recursive = dlg.get_result()

        paths_snapshot = list(paths)
        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            ok, fail = 0, []
            for p in paths_snapshot:
                name = os.path.basename(p.rstrip("/")) or p
                try:
                    os.chmod(p, mode)
                    if recursive and os.path.isdir(p):
                        for root_dir, sub_dirs, sub_files in os.walk(p):
                            for d in sub_dirs:
                                os.chmod(os.path.join(root_dir, d), mode)
                            for f in sub_files:
                                os.chmod(os.path.join(root_dir, f), mode)
                    ok += 1
                except Exception as e:
                    fail.append(f"{name}（{e}）")
            entries = os.listdir(target_dir)
            return ok, fail, entries

        def _on_ok(result):
            ok, fail, entries = result
            self._populate_list(entries, filter_text=filter_text)
            msg = f"已套用權限 {oct(mode)[2:].zfill(3)} 到 {ok} 個項目。"
            if fail:
                msg += "\n\n失敗：\n" + "\n".join(fail[:10])
            QMessageBox.information(self, "變更屬性完成", msg)

        self._run_busy("套用權限中...", _work, _on_ok)

    # ---------------- 重新命名 ----------------
    def do_rename(self, path):
        old_name = os.path.basename(path.rstrip("/")) or path
        dlg = RenameDialog(old_name, self.dark_mode, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_name = dlg.get_name()
        if not new_name or new_name == old_name:
            return

        parent_dir = os.path.dirname(path.rstrip("/"))
        new_path = os.path.join(parent_dir, new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "重新命名失敗", "目標名稱已存在。")
            return

        target_dir = self.current_dir
        filter_text = self.search_edit.text()

        def _work():
            os.rename(path, new_path)
            return os.listdir(target_dir)

        def _on_ok(entries):
            self.selected_paths.discard(path)
            self._populate_list(entries, filter_text=filter_text)

        def _on_err(err):
            QMessageBox.warning(self, "重新命名失敗", str(err))

        self._run_busy("重新命名中...", _work, _on_ok, _on_err)

    # ---------------- 使用其他應用程式開啟 ----------------
    def open_with_other_app(self, path):
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "*/*"
        opened = False

        try:
            from jnius import autoclass, cast  # type: ignore
            Intent = autoclass('android.content.Intent')
            File = autoclass('java.io.File')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            FileProvider = autoclass('androidx.core.content.FileProvider')
            activity = cast('android.app.Activity', PythonActivity.mActivity)
            context = activity.getApplicationContext()
            java_file = File(path)
            authority = context.getPackageName() + ".fileprovider"
            uri = FileProvider.getUriForFile(context, authority, java_file)
            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(uri, mime_type)
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            chooser = Intent.createChooser(intent, "選擇開啟方式")
            activity.startActivity(chooser)
            opened = True
        except Exception:
            opened = False

        if not opened:
            try:
                if sys.platform.startswith("win"):
                    os.startfile(path)  # type: ignore[attr-defined]
                    opened = True
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", path])
                    opened = True
            except Exception:
                opened = False

        if not opened:
            QMessageBox.warning(
                self, "無法開啟",
                "此裝置/環境不支援「使用其他應用程式開啟」。\n"
                "Android 上需要 App 具備 FileProvider 設定才能呼叫系統的開啟/分享選單，"
                "在 Pydroid3 直接執行的腳本可能無法使用此功能。")


def main():
    request_android_storage_permission()
    app = QApplication(sys.argv)
    window = FilePickerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
真圓度量測工具（周長／面積法 & 最小二乘法）— PySide6 行動版 v11
=====================================================

修改重點：
    1. 圈選區自動放大 (ROI Zoom)：畫好圈選區後，畫面會自動拉近聚焦於該區域，
       且鎖定此區域進行後續的濾鏡預覽、Otsu 計算與分析。
    2. 清除圈選重置 (Reset ROI)：按下「清除圈選」後，完全恢復為初始載入原圖時的全圖放大與顯示狀態。
    3. 最小二乘法擬合圓綠色線條加粗處理。

適用環境：Pydroid3 on Android / Windows / Linux
安裝需求：
    pip install pyside6 opencv-python numpy
"""

import sys
import os
import math
import traceback
import base64
import json
import cv2
import numpy as np
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QRect, QRectF, QPoint, QPointF, QSize, QTimer, QEvent
from PySide6.QtGui import (
    QImage, QPixmap, QPainter, QColor, QPen, QIcon, QPolygonF, QImageReader, QEventPoint
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QLineEdit,
    QSpinBox, QSlider, QCheckBox, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QSizePolicy, QStackedWidget, QFileDialog,
    QScrollArea, QDialog, QScroller, QScrollerProperties
)

"""精簡範例:啟動時驗證 license.lic,失敗則產生「驗證碼.txt」、提示後結束。
與 license_verify.py、GetDeviceCode.py 放在同一資料夾。"""
import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QMessageBox
from license_verify import check_license_online
from GetDeviceCode import write_device_code

PRODUCT = "A"   # 須與 license.lic 內的產品一致


def verify_or_exit(app: QApplication):
    try:
        return check_license_online(PRODUCT)       # 授權碼 + 主機板 SN + 網路時間
    except Exception:                              # LicenseError 或其他任何失敗
        try:
            write_device_code()                    # 寫出「驗證碼.txt」
        except Exception:                          # 讀不到 SN 等,仍要顯示提示
            pass
        QMessageBox.critical(None, "無法取得授權",
                             "憑證已失效或目前無法連網驗證,請使用「授權碼.txt」申請憑證")
        sys.exit(1)


# ----------------------------------------------------------------------
# 執行平台偵測：自動決定使用「手機版」或「Windows/桌機版」介面
# 優先順序：命令列 --ui=mobile|desktop  >  環境變數 APP_UI_MODE  >  自動偵測
# ----------------------------------------------------------------------
def detect_ui_mode():
    for arg in sys.argv[1:]:
        if arg.lower().startswith("--ui="):
            v = arg.split("=", 1)[1].strip().lower()
            if v in ("mobile", "desktop"):
                return v
    v = os.environ.get("APP_UI_MODE", "").strip().lower()
    if v in ("mobile", "desktop"):
        return v
    if sys.platform == "android" or hasattr(sys, "getandroidapilevel"):
        return "mobile"
    if any(k in os.environ for k in ("ANDROID_ROOT", "ANDROID_DATA", "ANDROID_STORAGE")):
        return "mobile"
    return "desktop"

UI_MODE = detect_ui_mode()
IS_MOBILE = (UI_MODE == "mobile")

def ui(mobile_val, desktop_val):
    """依目前模式挑選數值"""
    return mobile_val if IS_MOBILE else desktop_val

def sz(v):
    """尺寸縮放：手機版維持原值（大觸控目標），桌機版縮小為 70%"""
    return v if IS_MOBILE else max(1, int(round(v * 0.7)))

def fs(v):
    """字級縮放：桌機版縮小為 80%（最小 8）"""
    return v if IS_MOBILE else max(8, int(round(v * 0.8)))

_DESKTOP_QSS_MAP = [
    ("min-height: 28px;", "min-height: 22px;"),
    ("padding: 10px 6px;", "padding: 4px 6px;"),
    ("padding: 10px;", "padding: 6px 14px;"),
    ("padding: 8px; font-size", "padding: 4px 6px; font-size"),
    ("font-size: 15px;", "font-size: 13px;"),
    ("font-size: 19px;", "font-size: 16px;"),
    ("width: 28px;", "width: 14px;"),
    ("height: 28px;", "height: 14px;"),
    ("border-radius: 14px;", "border-radius: 7px;"),
    ("min-height: 40px;", "min-height: 30px;"),
    ("min-width: 40px;", "min-width: 30px;"),
    ("margin: 24px 0 24px 0;", "margin: 14px 0 14px 0;"),
    ("margin: 0 24px 0 24px;", "margin: 0 14px 0 14px;"),
    ("height: 24px;", "height: 14px;"),
    ("width: 24px;", "width: 14px;"),
]

def adapt_qss(qss):
    """桌機模式下把樣式表中偏大的觸控尺寸縮小；手機模式原樣回傳"""
    if IS_MOBILE:
        return qss
    for a, b in _DESKTOP_QSS_MAP:
        qss = qss.replace(a, b)
    return qss

def _set_app_qss(app, qss):
    app.setStyleSheet(adapt_qss(qss))

def get_exe_dir():
    # 判斷是否為 Nuitka 編譯後的環境
    if '__compiled__' in globals() or '__compiled__' in dir(__builtins__):
        # 也可以搭配 sys.frozen 判斷（Nuitka 預設也會設定 sys.frozen）
        return Path(sys.argv[0]).resolve().parent
    else:
        # 開發階段 (.py)
        return Path(__file__).resolve().parent

# ----------------------------------------------------------------------
# 崩潰記錄
# ----------------------------------------------------------------------
#CRASH_LOG_PATH = os.path.join(os.path.expanduser("~"), "roundness_app_crash.log")
# 使用 os.path.join 而非字串拼接，避免 Windows 下路徑分隔符與中文字元混雜造成的問題
CRASH_LOG_PATH = os.path.join(get_exe_dir(), "roundness_app_crash.log")

def _install_crash_logger():
    def _hook(exc_type, exc_value, exc_tb):
        try:
            with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("\n---- crash ----\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

# ----------------------------------------------------------------------
# 設定檔（記住使用者上次選擇的預設路徑）
# ----------------------------------------------------------------------

#CONFIG_PATH = os.path.join(os.path.expanduser("~"), "roundness_app_config.json")
# 使用 os.path.join 而非字串拼接，避免 Windows 下路徑分隔符（"\\" 與 "/"混用）
# 以及路徑中含中文字元時可能造成的問題
#CONFIG_PATH = os.path.join(get_exe_dir(), "roundness_app_config.json")
CONFIG_PATH = f"{get_exe_dir()}/roundness_app_config.json"

def load_config():
    """讀取 JSON 設定檔。
    明確以 utf-8 讀取，確保中文字（路徑、檔名等）不會因系統預設編碼
    （例如 Windows 常見的 cp950/big5）不同而讀取失敗或產生亂碼。
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_config(data: dict):
    """寫入 JSON 設定檔。
    - encoding="utf-8"：確保寫入的中文字（例如中文路徑）以 UTF-8 正確編碼儲存，
      不受 Windows 系統預設編碼（cp950/big5等）影響。
    - ensure_ascii=False：讓中文字直接以可讀的原始字元寫入 json，
      而不是被轉成 \\uXXXX 轉義序列。
    - 先寫入暫存檔再以 os.replace 原子性地取代正式檔案，
      避免程式在寫入過程中被中斷（例如視窗被強制關閉）導致設定檔損毀。
    """
    try:
        cfg_dir = os.path.dirname(CONFIG_PATH)
        if cfg_dir and not os.path.isdir(cfg_dir):
            os.makedirs(cfg_dir, exist_ok=True)
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        pass


def get_default_browse_dir():
    """讀取設定檔中使用者上次自訂的預設路徑，若無效則退回程式所在資料夾"""
    cfg = load_config()
    saved_dir = cfg.get("default_browse_dir")
    try:
        if saved_dir and os.path.isdir(saved_dir):
            return saved_dir
    except Exception:
        # Windows 下若路徑含有無法解碼的中文字元，os.path.isdir 可能拋出例外，
        # 此時直接退回程式所在資料夾，避免程式崩潰
        pass
    return get_script_dir()


def set_default_browse_dir(path: str):
    cfg = load_config()
    cfg["default_browse_dir"] = path
    save_config(cfg)


def get_default_save_dir():
    """讀取設定檔中使用者上次選定的「儲存資料夾」路徑。
    使用獨立鍵值 default_save_dir，避免與瀏覽用的 default_browse_dir（home）混用。
    """
    cfg = load_config()
    saved_dir = cfg.get("default_save_dir")
    try:
        if saved_dir and os.path.isdir(saved_dir):
            return saved_dir
    except Exception:
        pass
    return None


def set_default_save_dir(path: str):
    cfg = load_config()
    cfg["default_save_dir"] = path
    save_config(cfg)


def get_default_dark_mode():
    """讀取設定檔中使用者上次選擇的顯示模式，預設為暗模式"""
    cfg = load_config()
    val = cfg.get("dark_mode")
    if isinstance(val, bool):
        return val
    return True


def set_default_dark_mode(dark: bool):
    cfg = load_config()
    cfg["dark_mode"] = dark
    save_config(cfg)


def get_default_window_geometry():
    """讀取設定檔中使用者上次調整過的視窗大小與位置，無效則回傳 None"""
    cfg = load_config()
    geo = cfg.get("window_geometry")
    if isinstance(geo, dict):
        try:
            x = int(geo.get("x"))
            y = int(geo.get("y"))
            w = int(geo.get("w"))
            h = int(geo.get("h"))
            if w > 100 and h > 100:
                return {"x": x, "y": y, "w": w, "h": h}
        except Exception:
            pass
    return None


def set_default_window_geometry(x: int, y: int, w: int, h: int):
    """將視窗目前的大小與位置寫入設定檔"""
    cfg = load_config()
    cfg["window_geometry"] = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    save_config(cfg)

# ----------------------------------------------------------------------
# 程式圖示 Base64
# ----------------------------------------------------------------------
APP_ICON_BASE64 = (
    "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"
    "/weltZ9nL9h3SAAAAABJRU5ErkJggg=="
)

def _load_app_pixmap():
    try:
        data = base64.b64decode(APP_ICON_BASE64)
        pix = QPixmap()
        if not pix.loadFromData(data, "PNG"):
            return QPixmap()
        return pix
    except Exception:
        return QPixmap()

def get_app_icon():
    pix = _load_app_pixmap()
    if pix.isNull():
        return QIcon()
    return QIcon(pix)

# ----------------------------------------------------------------------
# 影像處理核心
# ----------------------------------------------------------------------
def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)

def imwrite_unicode(path, img):
    ext = os.path.splitext(path)[1]
    if not ext:
        ext = ".png"
        path = path + ext
    ok, encoded = cv2.imencode(ext, img)
    if not ok:
        return False
    try:
        encoded.tofile(path)
    except OSError:
        return False
    return True

def compute_roundness_area_perimeter(contour):
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, True)
    if perimeter == 0:
        return area, perimeter, 0.0
    circularity = 4.0 * math.pi * area / (perimeter ** 2)
    return area, perimeter, circularity

def fit_circle_lsc(points):
    x = points[:, 0]
    y = points[:, 1]
    A = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b = x ** 2 + y ** 2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    r = math.sqrt(max(c + cx ** 2 + cy ** 2, 0.0))
    return cx, cy, r

def compute_roundness_lsc(points):
    cx, cy, fit_r = fit_circle_lsc(points)
    dists = np.sqrt((points[:, 0] - cx) ** 2 + (points[:, 1] - cy) ** 2)
    r_min = float(dists.min())
    r_max = float(dists.max())

    roundness_error = r_max - r_min
    d_fit = 2 * fit_r
    d_in = 2 * r_min
    d_out = 2 * r_max

    if fit_r > 0:
        circularity = (fit_r - roundness_error) / fit_r
    else:
        circularity = 0.0

    return cx, cy, fit_r, r_min, r_max, d_fit, d_in, d_out, roundness_error, circularity

def approx_contour_corner_preserving(contour, epsilon_px=1.0):
    return cv2.approxPolyDP(contour, epsilon_px, True)

def process_binarization(img, thresh_val=128, invert=True):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    _, thresh = cv2.threshold(blurred, thresh_val, 255, thresh_type)
    return thresh

def auto_calculate_otsu_threshold(img, invert=True):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_type = (cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY) + cv2.THRESH_OTSU
    otsu_val, _ = cv2.threshold(blurred, 0, 255, thresh_type)
    return int(otsu_val)

def detect_circles(image_path=None, image_array=None, min_area=100, method="lsc", thresh_val=128, invert_thresh=True):
    if image_array is not None:
        img = image_array
    else:
        img = imread_unicode(image_path)
        if img is None:
            raise FileNotFoundError(f"無法讀取圖片：{image_path}")

    thresh = process_binarization(img, thresh_val=thresh_val, invert=invert_thresh)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    clean = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
    clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(
        clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )

    results = []
    output = img.copy()

    for i, raw_cnt in enumerate(contours):
        if cv2.contourArea(raw_cnt) < min_area:
            continue

        if method == "lsc":
            raw_pts = raw_cnt.reshape(-1, 2).astype(np.float64)
            cx, cy, fit_r, r_min, r_max, d_fit, d_in, d_out, roundness_error, circularity = compute_roundness_lsc(raw_pts)
            center = (int(round(cx)), int(round(cy)))

            result = {
                "id": i,
                "method": "lsc",
                "center": center,
                "radius": fit_r,
                "fit_radius": fit_r,
                "r_min": r_min,
                "r_max": r_max,
                "d_fit": d_fit,
                "d_in": d_in,
                "d_out": d_out,
                "roundness_error": roundness_error,
                "circularity": circularity,
            }

            cv2.circle(output, center, int(round(r_min)), (255, 0, 0), 2)
            cv2.circle(output, center, int(round(r_max)), (0, 0, 255), 2)
            # 將最後一個參數從 1 修改為 3，讓綠色線條更粗
            cv2.circle(output, center, int(round(fit_r)), (0, 255, 0), 3)
            cv2.drawMarker(output, center, (0, 255, 255), cv2.MARKER_CROSS, 14, 2)

            text = f"{circularity*100:.1f}%"
            font_scale = max(0.4, min(1.0, r_min / 60.0))
            thickness = 2 if font_scale >= 0.6 else 1
            text_size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
            
            text_x = center[0] - (text_size[0] // 2)
            text_y = center[1] + (text_size[1] // 2) + int(r_min * 0.25)
            
            cv2.putText(
                output, text,
                (text_x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), thickness
            )

            # 編號畫在真圓度文字的正下方
            id_text = str(i)
            id_text_size, _ = cv2.getTextSize(id_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
            id_text_x = center[0] - (id_text_size[0] // 2)
            id_text_y = text_y + id_text_size[1] + 6
            cv2.putText(
                output, id_text,
                (id_text_x, id_text_y),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), thickness
            )
        else:
            cnt_int = approx_contour_corner_preserving(raw_cnt)
            area, perimeter, circularity = compute_roundness_area_perimeter(cnt_int)
            circularity = min(circularity, 1.0)

            (x, y), enclosing_r = cv2.minEnclosingCircle(cnt_int)
            center = (int(x), int(y))

            result = {
                "id": i,
                "method": "area_perimeter",
                "center": center,
                "radius": enclosing_r,
                "area": area,
                "perimeter": perimeter,
                "circularity": circularity,
            }

            color = (0, int(255 * circularity), int(255 * (1 - circularity)))
            cv2.drawContours(output, [cnt_int], -1, color, 3)
            percent_text = f"{circularity*100:.1f}%"
            cv2.putText(
                output, percent_text,
                (center[0] - 25, center[1]),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2
            )

            # 編號畫在真圓度文字的正下方
            id_text = str(i)
            id_text_size, _ = cv2.getTextSize(id_text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            cv2.putText(
                output, id_text,
                (center[0] - (id_text_size[0] // 2), center[1] + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2
            )

        results.append(result)

    return results, output

def cvimg_to_qpixmap(cv_img):
    if len(cv_img.shape) == 2:
        rgb = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())

# ----------------------------------------------------------------------
# 背景工作線程
# ----------------------------------------------------------------------
class AnalyzeWorker(QThread):
    finished_ok = Signal(list, np.ndarray)
    failed = Signal(str)

    def __init__(self, min_area, image_path=None, image_array=None, method="lsc", thresh_val=128, invert_thresh=True):
        super().__init__()
        self.min_area = min_area
        self.image_path = image_path
        self.image_array = image_array
        self.method = method
        self.thresh_val = thresh_val
        self.invert_thresh = invert_thresh

    def run(self):
        try:
            results, output = detect_circles(
                image_path=self.image_path,
                image_array=self.image_array,
                min_area=self.min_area,
                method=self.method,
                thresh_val=self.thresh_val,
                invert_thresh=self.invert_thresh
            )
            self.finished_ok.emit(results, output)
        except Exception as e:
            self.failed.emit(str(e))

# ----------------------------------------------------------------------
# 繪圖 UI 輔助
# ----------------------------------------------------------------------
def make_icon(draw_func, size=30, force_white=False, dark=False):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    draw_func(painter, size, force_white, dark)
    painter.end()
    return QIcon(pixmap)

def _draw_folder(painter, size, force_white, dark):
    color = QColor("#FFFFFF") if force_white else (QColor("#F2C94C") if not dark else QColor("#D9B44A"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    tab = QRectF(size * 0.10, size * 0.22, size * 0.38, size * 0.16)
    body = QRectF(size * 0.10, size * 0.36, size * 0.80, size * 0.46)
    painter.drawRoundedRect(tab, 2, 2)
    painter.drawRoundedRect(body, 3, 3)

def _draw_image_file(painter, size, force_white, dark):
    frame_color = QColor("#FFFFFF") if force_white else (QColor("#2F6FED") if not dark else QColor("#8FB8FF"))
    painter.setPen(QPen(frame_color, max(1.5, size * 0.06)))
    painter.setBrush(Qt.NoBrush)
    rect = QRectF(size * 0.14, size * 0.14, size * 0.72, size * 0.72)
    painter.drawRoundedRect(rect, 3, 3)

    painter.setPen(Qt.NoPen)
    painter.setBrush(frame_color)
    painter.drawEllipse(QPointF(size * 0.33, size * 0.36), size * 0.07, size * 0.07)

    mountain = QPolygonF([
        QPointF(size * 0.18, size * 0.78),
        QPointF(size * 0.42, size * 0.50),
        QPointF(size * 0.58, size * 0.66),
        QPointF(size * 0.70, size * 0.50),
        QPointF(size * 0.82, size * 0.78),
    ])
    painter.drawPolygon(mountain)

def _draw_back_arrow(painter, size, force_white, dark):
    color = QColor("#FFFFFF")
    pen = QPen(color, max(2.2, size * 0.1))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    cy = size * 0.5
    painter.drawLine(QPointF(size * 0.68, size * 0.20), QPointF(size * 0.30, cy))
    painter.drawLine(QPointF(size * 0.30, cy), QPointF(size * 0.68, size * 0.80))

def _draw_home(painter, size, force_white, dark):
    color = QColor("#FFFFFF")
    pen = QPen(color, max(2.0, size * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    peak = QPointF(size * 0.5, size * 0.16)
    left_eave = QPointF(size * 0.16, size * 0.48)
    right_eave = QPointF(size * 0.84, size * 0.48)
    painter.drawLine(peak, left_eave)
    painter.drawLine(peak, right_eave)

    body = QRectF(size * 0.26, size * 0.48, size * 0.48, size * 0.36)
    painter.drawRect(body)

class ThemeIcon(QWidget):
    def __init__(self, dark_mode=False, parent=None):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.setFixedSize(sz(28), sz(28))

    def set_dark(self, dark_mode):
        self.dark_mode = dark_mode
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor("#F2C94C") if not self.dark_mode else QColor("#B7C4D6")
        p.setPen(QPen(color, 2))
        p.setBrush(color)
        r = self.rect().adjusted(6, 6, -6, -6)
        if not self.dark_mode:
            p.drawEllipse(r)
            cx, cy = self.rect().center().x(), self.rect().center().y()
            for ang in range(0, 360, 45):
                rad = math.radians(ang)
                x1 = cx + math.cos(rad) * 11
                y1 = cy + math.sin(rad) * 11
                x2 = cx + math.cos(rad) * 14
                y2 = cy + math.sin(rad) * 14
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
        else:
            p.drawEllipse(r)
            p.setBrush(QColor(26, 29, 36))
            p.setPen(Qt.NoPen)
            p.drawEllipse(r.adjusted(4, -3, 4, 3))
        p.end()

# ----------------------------------------------------------------------
# 圈選與顯示元件 (具備自動放大與完整還原機制)
# ----------------------------------------------------------------------
class ImageROIWidget(QWidget):
    """圖片顯示 + 圈選元件。
    - 單指/左鍵拖曳：圈選 ROI（圈選後自動放大聚焦）
    - 雙指：縮放與移動圖片（pinch + 雙指平移）
    - 滑鼠：滾輪縮放、右鍵或中鍵拖曳移動、左鍵雙擊重設視圖
    - 圈選區鎖定後，放大狀態下單指/左鍵拖曳也可移動圖片
    """
    roi_changed = Signal()

    MAX_ZOOM = 8.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(ui(280, 360))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(False)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

        self._pixmap = QPixmap()
        self._orig_size = QSize(0, 0)
        self._draw_rect = QRect()

        self._selecting = False
        self._start_pt = QPoint()
        self._cur_pt = QPoint()
        self._selection = None

        self._display_pixmap = QPixmap()
        self._committed_roi = None

        self._dark_mode = True

        # 檢視（縮放 / 平移）狀態
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._pinching = False
        self._pinch0 = None
        self._touch_blocked = False
        self._mouse_panning = False
        self._last_mouse_pos = QPointF()
        self._pinch_ids = None
        self._pinch_last = None
        self._touch_seen = False
        self._touch_start = None   # 單指按下位置：移動超過門檻才開始圈選/拖曳

        # 效能：快取裁切 / 縮放後的圖片，互動中改用快速繪製，停手後再補平滑繪製
        self._crop_cache = QPixmap()
        self._crop_cache_key = None
        self._proxy = QPixmap()
        self._proxy_key = None
        self._interacting = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(160)
        self._idle_timer.timeout.connect(self._on_idle)

    def set_dark(self, dark):
        self._dark_mode = dark
        self.update()

    # ---------------- 檢視縮放 / 平移 ----------------
    def reset_view(self):
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def _clamp_pan(self):
        b = self._draw_rect
        if b.isNull() or self._zoom <= 1.0:
            self._pan = QPointF(0, 0)
            return
        lim_x = b.width() * (self._zoom - 1.0) / 2.0
        lim_y = b.height() * (self._zoom - 1.0) / 2.0
        self._pan = QPointF(
            max(-lim_x, min(self._pan.x(), lim_x)),
            max(-lim_y, min(self._pan.y(), lim_y)),
        )

    def _set_view(self, zoom, pan):
        self._zoom = max(1.0, min(self.MAX_ZOOM, zoom))
        self._pan = QPointF(pan)
        self._clamp_pan()
        self._interacting = True
        self._idle_timer.start()
        self.update()

    def _on_idle(self):
        self._interacting = False
        self.update()

    def _view_rect(self):
        """圖片目前實際顯示在元件座標中的矩形（含縮放與平移）"""
        b = QRectF(self._draw_rect)
        if b.isNull():
            return b
        w = b.width() * self._zoom
        h = b.height() * self._zoom
        c = b.center() + self._pan
        return QRectF(c.x() - w / 2.0, c.y() - h / 2.0, w, h)

    def _visible_image_rect(self):
        return self._view_rect().intersected(QRectF(self.rect())).toRect()

    def _zoom_about(self, new_zoom, anchor):
        if self._draw_rect.isNull():
            return
        bc = QRectF(self._draw_rect).center()
        u = (anchor - (bc + self._pan)) / self._zoom
        new_zoom = max(1.0, min(self.MAX_ZOOM, new_zoom))
        self._set_view(new_zoom, anchor - bc - u * new_zoom)

    # ---------------- 圖片設定 ----------------
    def set_pixmap(self, pixmap: QPixmap, orig_size: QSize):
        self._pixmap = pixmap
        self._orig_size = orig_size
        self._selection = None
        self._display_pixmap = QPixmap()
        self._committed_roi = None
        self.reset_view()
        self._recompute_draw_rect()
        self.update()

    def show_processed_pixmap(self, pixmap: QPixmap):
        """顯示二值化或分析結果圖片"""
        self._display_pixmap = pixmap
        self._recompute_draw_rect()
        self.update()

    def clear_processed_overlay(self):
        """只清除疊加顯示用的處理結果，還原成未處理的底圖"""
        self._display_pixmap = QPixmap()
        self._recompute_draw_rect()
        self.update()

    def has_image(self):
        return not self._pixmap.isNull()

    def clear_selection(self):
        """清除圈選區並還原到初始原圖大小狀態"""
        self._selection = None
        self._selecting = False
        self._display_pixmap = QPixmap()
        self._committed_roi = None
        self.reset_view()
        self._recompute_draw_rect()
        self.update()
        self.roi_changed.emit()

    def _current_display_pixmap(self):
        pix = self._display_pixmap if not self._display_pixmap.isNull() else self._pixmap

        # 若已鎖定圈選區，則自動裁切放大該區域（結果快取，避免每次重繪都重新複製）
        if self._committed_roi and not pix.isNull():
            key = (pix.cacheKey(), self._committed_roi)
            if self._crop_cache_key != key:
                ox, oy, ow, oh = self._committed_roi
                self._crop_cache = pix.copy(ox, oy, ow, oh)
                self._crop_cache_key = key
            return self._crop_cache
        return pix

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recompute_draw_rect()

    def _recompute_draw_rect(self):
        pix = self._current_display_pixmap()
        if pix.isNull():
            self._draw_rect = QRect()
            return
        scaled = pix.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        self._draw_rect = QRect(x, y, scaled.width(), scaled.height())
        self._clamp_pan()

    def _clamp_to_view(self, pt: QPoint) -> QPoint:
        r = self._visible_image_rect()
        if r.isNull():
            return pt
        x = max(r.left(), min(pt.x(), r.right()))
        y = max(r.top(), min(pt.y(), r.bottom()))
        return QPoint(x, y)

    # ---------------- 選取 / 拖曳共用邏輯 ----------------
    def _sel_press(self, pt: QPoint):
        if not self.has_image() or self._draw_rect.isNull():
            return
        if self._committed_roi is not None:
            # 已鎖定圈選區：放大狀態下單指/左鍵拖曳 = 移動圖片
            if self._zoom > 1.0:
                self._mouse_panning = True
                self._last_mouse_pos = QPointF(pt)
            return
        self._selecting = True
        self._start_pt = self._clamp_to_view(pt)
        self._cur_pt = self._start_pt
        self.update()

    def _sel_move(self, pt: QPoint):
        if self._mouse_panning:
            cur = QPointF(pt)
            self._set_view(self._zoom, self._pan + (cur - self._last_mouse_pos))
            self._last_mouse_pos = cur
            return
        if not self._selecting:
            return
        self._cur_pt = self._clamp_to_view(pt)
        self.update()

    def _sel_release(self):
        if self._mouse_panning:
            self._mouse_panning = False
            return
        if not self._selecting:
            return
        self._selecting = False
        rect = QRect(self._start_pt, self._cur_pt).normalized()
        if rect.width() < 12 or rect.height() < 12:
            self._selection = None
            self.update()
            self.roi_changed.emit()
            return

        self._selection = rect
        roi = self._widget_rect_to_original(rect)

        if roi:
            self._committed_roi = roi  # 鎖定圈選區
            self.reset_view()          # 顯示裁切後的圖，回到 1 倍檢視
            self._recompute_draw_rect()
            self.roi_changed.emit()
        self.update()

    # ---------------- 滑鼠事件 ----------------
    def _ignore_mouse(self, event):
        """已收到過觸控事件後，觸控合成出來的滑鼠事件一律忽略；真滑鼠不受影響"""
        if not self._touch_seen:
            return False
        try:
            return event.source() != Qt.MouseEventNotSynthesized
        except Exception:
            return False

    def mousePressEvent(self, event):
        if self._ignore_mouse(event):
            return
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            if self._zoom > 1.0 and not self._selecting:
                self._mouse_panning = True
                self._last_mouse_pos = QPointF(event.position())
            return
        if event.button() != Qt.LeftButton:
            return
        self._sel_press(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self._ignore_mouse(event):
            return
        self._sel_move(event.position().toPoint())

    def mouseReleaseEvent(self, event):
        if self._ignore_mouse(event):
            return
        self._sel_release()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._zoom > 1.0:
            self._selecting = False
            self._mouse_panning = False
            self.reset_view()
        event.accept()

    def wheelEvent(self, event):
        if not self.has_image():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.0015 ** delta
        self._zoom_about(self._zoom * factor, QPointF(event.position()))
        event.accept()

    # ---------------- 觸控（雙指縮放 / 移動）----------------
    @staticmethod
    def _touch_points(e):
        try:
            return list(e.points())
        except AttributeError:
            return list(e.touchPoints())

    @staticmethod
    def _point_released(p):
        try:
            return p.state() == QEventPoint.State.Released
        except Exception:
            return p.state() == Qt.TouchPointReleased

    @staticmethod
    def _point_pos(p):
        try:
            return QPointF(p.position())
        except AttributeError:
            return QPointF(p.pos())

    @staticmethod
    def _point_id(p):
        try:
            return p.id()
        except Exception:
            return None

    def event(self, e):
        t = e.type()
        if t in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel):
            self._touch_seen = True
            try:
                self._handle_touch(e, t)
            except Exception:
                traceback.print_exc()
            e.accept()
            return True
        return super().event(e)

    def _handle_touch(self, e, t):
        # TouchEnd / TouchCancel 代表整個觸控序列結束（最後一根手指離開），
        # 不依賴各點的 state 判斷，避免某些 Android 版本漏報 Released 造成狀態卡住
        ended = t in (QEvent.TouchEnd, QEvent.TouchCancel)
        active = [] if ended else [p for p in self._touch_points(e)
                                   if not self._point_released(p)]
        n = len(active)

        if t == QEvent.TouchBegin:
            # 新的觸控序列開始：清掉任何殘留狀態
            self._pinching = False
            self._pinch_ids = None
            self._pinch_last = None
            self._touch_blocked = False
            self._mouse_panning = False
            self._selecting = False
            self._touch_start = None

        if n >= 2:
            # 第二根手指落下：取消尚未完成的圈選，並立刻重繪以清除暗色遮罩/藍點
            if self._selecting:
                self._selecting = False
                self._selection = None
            self._mouse_panning = False
            self._touch_start = None

            # 固定取編號最小的兩根手指，避免手指順序改變造成跳動
            ids = [self._point_id(p) for p in active]
            if None not in ids:
                active = [p for _, p in sorted(zip(ids, active), key=lambda t: t[0])]
            a = self._point_pos(active[0])
            b = self._point_pos(active[1])
            dist = max(1.0, math.hypot(a.x() - b.x(), a.y() - b.y()))
            center = QPointF((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0)

            last = self._pinch_last
            if last is not None and not self._draw_rect.isNull():
                ld, lc = last
                ratio = dist / ld
                jump = math.hypot(center.x() - lc.x(), center.y() - lc.y())
                # 單次事件內變化過大 = 手指組合改變（例如第三指），略過這一筆避免畫面跳動
                if 0.5 < ratio < 2.0 and jump < 200:
                    new_zoom = max(1.0, min(self.MAX_ZOOM, self._zoom * ratio))
                    bc = QRectF(self._draw_rect).center()
                    u = (lc - (bc + self._pan)) / self._zoom
                    self._set_view(new_zoom, center - bc - u * new_zoom)
            self._pinch_last = (dist, center)
            self._pinching = True
            self._touch_blocked = True
            self.update()
        elif n == 1:
            self._pinching = False
            self._pinch_ids = None
            self._pinch_last = None
            if self._touch_blocked:
                return                    # 雙指手勢結束後，等全部手指離開再恢復圈選
            pos = self._point_pos(active[0]).toPoint()
            if self._selecting or self._mouse_panning:
                self._sel_move(pos)
            else:
                if self._touch_start is None:
                    # 先只記錄落點，不立刻進入圈選：避免雙指手勢中先落下的那根手指
                    # 觸發圈選遮罩（畫面變暗＋小藍點）
                    self._touch_start = pos
                    return
                if (pos - self._touch_start).manhattanLength() < ui(18, 10):
                    return
                self._sel_press(self._touch_start)
                self._sel_move(pos)
        else:
            self._pinching = False
            self._pinch_ids = None
            self._pinch_last = None
            self._touch_blocked = False
            self._touch_start = None
            self._sel_release()

    # ---------------- 座標轉換 ----------------
    def _widget_rect_to_original(self, rect):
        vr = self._view_rect()
        if vr.isNull() or self._orig_size.isEmpty():
            return None

        scale_x = self._orig_size.width() / vr.width()
        scale_y = self._orig_size.height() / vr.height()

        rel_x = rect.x() - vr.x()
        rel_y = rect.y() - vr.y()

        ox = int(max(0, rel_x * scale_x))
        oy = int(max(0, rel_y * scale_y))
        ow = int(min(self._orig_size.width() - ox, rect.width() * scale_x))
        oh = int(min(self._orig_size.height() - oy, rect.height() * scale_y))

        if ow <= 0 or oh <= 0:
            return None
        return (ox, oy, ow, oh)

    def get_roi_original(self):
        return self._committed_roi

    def _get_proxy(self, pix):
        """產生一次性的代理縮圖（長邊約為元件長邊的 2 倍）。
        平滑縮小整張大圖很耗時（手機上可達數百毫秒，會讓觸控事件卡住）；
        預先縮成代理圖後，一般縮放/移動都從代理圖繪製，成本固定且很小。"""
        target = max(self.width(), self.height(), 200) * 2
        key = (pix.cacheKey(), target)
        if self._proxy_key != key:
            if max(pix.width(), pix.height()) <= target:
                self._proxy = pix
            else:
                self._proxy = pix.scaled(target, target, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation)
            self._proxy_key = key
        return self._proxy

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor(38, 43, 54) if self._dark_mode else QColor(228, 231, 236)
        painter.fillRect(self.rect(), bg)

        pix = self._current_display_pixmap()
        if pix.isNull():
            painter.setPen(QColor(150, 156, 168))
            painter.drawText(self.rect(), Qt.AlignCenter, "尚未選擇照片")
            painter.end()
            return

        fast = self._interacting
        vr = self._view_rect()
        vis = vr.intersected(QRectF(self.rect()))
        if not vis.isEmpty() and vr.width() > 0 and vr.height() > 0:
            proxy = self._get_proxy(pix)
            # 顯示寬度不超過代理圖寬度 -> 用代理圖（便宜）；再放大才改用原圖的可見區塊
            use_proxy = (proxy is pix) or (vr.width() <= proxy.width())
            source = proxy if use_proxy else pix
            sx = source.width() / vr.width()
            sy = source.height() / vr.height()
            src = QRectF((vis.x() - vr.x()) * sx, (vis.y() - vr.y()) * sy,
                         vis.width() * sx, vis.height() * sy)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, use_proxy or not fast)
            painter.drawPixmap(vis, source, src)

        # 未鎖定前的動態拉框繪製
        if self._selecting and self._selection is None and not self._pinching:
            active_rect = QRect(self._start_pt, self._cur_pt).normalized()
            if active_rect.width() >= 6 and active_rect.height() >= 6:
                overlay = QColor(0, 0, 0, 130)
                dr = self._visible_image_rect()
                top = QRect(dr.left(), dr.top(), dr.width(), max(0, active_rect.top() - dr.top()))
                bottom = QRect(dr.left(), active_rect.bottom() + 1, dr.width(),
                                max(0, dr.bottom() - active_rect.bottom()))
                left = QRect(dr.left(), active_rect.top(),
                             max(0, active_rect.left() - dr.left()), active_rect.height())
                right = QRect(active_rect.right() + 1, active_rect.top(),
                              max(0, dr.right() - active_rect.right()), active_rect.height())
                for band in (top, bottom, left, right):
                    if band.width() > 0 and band.height() > 0:
                        painter.fillRect(band, overlay)

                highlight = QColor("#3D7BFF") if not self._dark_mode else QColor("#5B93FF")
                pen = QPen(highlight, 2, Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(active_rect)

        painter.end()

# ----------------------------------------------------------------------
# 檔案瀏覽頁
# ----------------------------------------------------------------------
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

class ClickableLabel(QLabel):
    """可偵測雙擊的 QLabel（用於預覽縮圖）"""
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


def load_thumbnail(path, max_w, max_h):
    """只解碼縮圖大小（不載入整張大圖），回傳 (QPixmap, 原始尺寸 QSize)"""
    try:
        reader = QImageReader(path)
        orig = reader.size()
        if not orig.isValid():
            return QPixmap(), QSize()
        reader.setScaledSize(orig.scaled(max_w, max_h, Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return QPixmap(), orig
        return QPixmap.fromImage(img), orig
    except Exception:
        return QPixmap(), QSize()


def get_script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.expanduser("~")

def enable_swipe_scroll(widget):
    """啟用手指（或滑鼠拖曳）上下滑動捲動的手勢。

    使用 Qt 內建的 QScroller，將指定 widget（通常是可捲動元件的 viewport）
    註冊為可用「滑動」方式捲動：
    - 手指輕點（或滑鼠單擊）仍視為一般點擊，不影響原本的選取／開啟功能。
    - 手指按住並拖曳超過一定距離時，才會被判定為滑動捲動，並帶有慣性滾動效果，
      符合行動裝置（Android/Pydroid3）上常見的滑動瀏覽體驗。
    """
    if not IS_MOBILE:
        return None  # 桌機用滑鼠滾輪/捲軸，不啟用拖曳手勢以免干擾拖曳操作
    scroller = QScroller.grabGesture(widget, QScroller.LeftMouseButtonGesture)
    try:
        props = scroller.scrollerProperties()
        # 稍微降低觸發滑動所需的拖曳距離，讓手勢更靈敏
        props.setScrollMetric(QScrollerProperties.DragStartDistance, 0.001)
        # 開啟垂直方向的邊界回彈效果（滑到頂/底時的彈性回饋）
        props.setScrollMetric(QScrollerProperties.VerticalOvershootPolicy,
                               QScrollerProperties.OvershootWhenScrollable)
        props.setScrollMetric(QScrollerProperties.HorizontalOvershootPolicy,
                               QScrollerProperties.OvershootAlwaysOff)
        scroller.setScrollerProperties(props)
    except Exception:
        pass
    return scroller


class FileBrowserPage(QWidget):
    file_opened = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_dir = get_default_browse_dir()
        self.home_dir = self.current_dir
        self.dark_mode = True
        self._build_ui()
        self._refresh_list()

    def set_dark(self, dark):
        self.dark_mode = dark
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        title = QLabel("選擇照片")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        nav_row = QHBoxLayout()
        self.up_btn = QPushButton()
        self.up_btn.setFixedSize(sz(48), sz(48))
        self.up_btn.clicked.connect(self.go_up)
        nav_row.addWidget(self.up_btn)

        self.home_btn = QPushButton()
        self.home_btn.setFixedSize(sz(48), sz(48))
        self.home_btn.clicked.connect(self.go_home)
        nav_row.addWidget(self.home_btn)

        self.path_label = QLineEdit(self.current_dir)
        self.path_label.setObjectName("PathEdit")
        self.path_label.setToolTip("點擊修改預設路徑，按 Enter 立即套用並記住此路徑（即使內容未變更也會重新寫入設定檔）")
        # 用旗標分辨 editingFinished 是否是因為按下 Enter 而觸發，
        # 避免按 Enter 時 returnPressed 與 editingFinished 兩個訊號都觸發而重複處理
        self._path_enter_triggered = False
        self.path_label.returnPressed.connect(self._on_path_return_pressed)
        self.path_label.editingFinished.connect(self._on_path_editing_finished)
        nav_row.addWidget(self.path_label, stretch=1)
        layout.addLayout(nav_row)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜尋檔名…")
        self.search_box.setMinimumHeight(sz(44))
        self.search_box.textChanged.connect(self._refresh_list)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(30, 30))
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list_widget.itemDoubleClicked.connect(self._on_item_clicked)
        enable_swipe_scroll(self.list_widget.viewport())
        self.list_widget.itemClicked.connect(self._on_item_single_clicked)
        layout.addWidget(self.list_widget, stretch=1)

        # 單擊照片時顯示的預覽縮圖
        self.preview_label = ClickableLabel("單擊照片可預覽")
        self.preview_label.doubleClicked.connect(self._on_preview_double_clicked)
        self._preview_path = None
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(ui(150, 190))
        self.preview_label.setObjectName("SectionLabel")
        layout.addWidget(self.preview_label)
        self.preview_info = QLabel("")
        self.preview_info.setObjectName("SectionLabel")
        self.preview_info.setAlignment(Qt.AlignCenter)
        self.preview_info.setWordWrap(True)
        layout.addWidget(self.preview_info)

        self.hint_label = QLabel("單擊照片預覽；雙擊資料夾進入，雙擊照片或預覽圖開啟")
        self.hint_label.setObjectName("SectionLabel")
        layout.addWidget(self.hint_label)

        self._update_nav_icons()

    def _update_nav_icons(self):
        self.up_btn.setIcon(make_icon(_draw_back_arrow, force_white=True, dark=self.dark_mode))
        self.up_btn.setIconSize(QSize(22, 22))
        self.home_btn.setIcon(make_icon(_draw_home, force_white=True, dark=self.dark_mode))
        self.home_btn.setIconSize(QSize(22, 22))

    def go_up(self):
        parent = os.path.dirname(self.current_dir.rstrip("/"))
        if parent and os.path.isdir(parent):
            self.current_dir = parent
            self._refresh_list()

    def go_home(self):
        self.current_dir = self.home_dir
        self._refresh_list()

    @staticmethod
    def _normalize_path(raw_path):
        """正規化使用者輸入的路徑字串。

        處理重點（尤其是 Windows 中文路徑）：
        - 去除頭尾空白，以及使用者從檔案總管「複製路徑」時常見的前後引號
        - 展開 "~" 開頭的家目錄寫法
        - 以 os.path.normpath 統一路徑分隔符號，中文字元本身不受影響
          （Python 3 的字串為 Unicode，os 模組在 Windows 上底層使用寬字元 API，
          因此中文路徑可以正確處理，不需額外轉碼）
        """
        if raw_path is None:
            return ""
        p = raw_path.strip()
        if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
            p = p[1:-1].strip()
        if not p:
            return ""
        try:
            p = os.path.expanduser(p)
            p = os.path.normpath(p)
        except Exception:
            pass
        return p

    def _on_path_return_pressed(self):
        """使用者在路徑欄位按下 Enter 鍵。

        不論欄位內容是否變更，只要是有效資料夾，就立即套用並寫入設定檔。
        """
        self._path_enter_triggered = True
        self._apply_path_input(self.path_label.text(), force_save=True, show_message=True)

    def _on_path_editing_finished(self):
        """路徑欄位失去焦點時觸發。

        按下 Enter 時 Qt 會先發出 returnPressed，再發出 editingFinished；
        此處利用旗標偵測到剛剛已經處理過 Enter，直接跳過，避免重複套用/重複跳出訊息。
        若是使用者用滑鼠點擊其他地方離開欄位（未按 Enter），僅嘗試套用路徑，
        但不強制寫入設定檔、也不跳出確認訊息，維持原本「僅按 Enter 才視為明確確認」的行為。
        """
        if self._path_enter_triggered:
            self._path_enter_triggered = False
            return
        self._apply_path_input(self.path_label.text(), force_save=False, show_message=False)

    def _apply_path_input(self, raw_path, force_save, show_message):
        new_path = self._normalize_path(raw_path)
        if not new_path:
            self.path_label.setText(self.current_dir)
            return
        try:
            is_dir = os.path.isdir(new_path)
        except Exception:
            # Windows 上若路徑含有無法解碼／不合法的中文字元，os.path.isdir 可能拋出例外
            is_dir = False
        if is_dir:
            changed = (os.path.normcase(new_path) != os.path.normcase(self.current_dir))
            self.current_dir = new_path
            self.home_dir = new_path  # home 按鈕改為回到新的預設路徑
            self.search_box.clear()
            self.path_label.setText(self.current_dir)
            if force_save:
                # 按下 Enter：不管內容是否真的改變，都立即寫入 JSON 設定檔
                set_default_browse_dir(self.current_dir)
            elif changed:
                set_default_browse_dir(self.current_dir)
            self._refresh_list()
            if show_message:
                QMessageBox.information(
                    self, "已儲存預設路徑",
                    f"已將預設路徑寫入設定檔：\n{CONFIG_PATH}"
                )
        else:
            QMessageBox.warning(self, "路徑錯誤", "找不到此資料夾，請確認路徑是否正確")
            self.path_label.setText(self.current_dir)

    def _on_preview_double_clicked(self):
        if self._preview_path and os.path.isfile(self._preview_path):
            self.file_opened.emit(self._preview_path)

    def _clear_preview(self):
        self._preview_path = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("單擊照片可預覽")
        self.preview_info.setText("")

    def _on_item_single_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if item.data(Qt.UserRole + 1) or not path:
            self._clear_preview()
            return
        pix, orig = load_thumbnail(path, 600, 600)
        if pix.isNull():
            self._preview_path = None
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("無法預覽此檔案")
            self.preview_info.setText("")
            return
        self._preview_path = path
        box = self.preview_label.size()
        self.preview_label.setPixmap(
            pix.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        try:
            kb = os.path.getsize(path) / 1024.0
            size_txt = f"{kb/1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
        except OSError:
            size_txt = ""
        self.preview_info.setText(
            f"{os.path.basename(path)}　{orig.width()}×{orig.height()}　{size_txt}")

    def _on_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        is_dir = item.data(Qt.UserRole + 1)
        if is_dir:
            self.current_dir = path
            self.search_box.clear()
            self._refresh_list()
        else:
            self.file_opened.emit(path)

    def _refresh_list(self):
        self._update_nav_icons()
        self.path_label.setText(self.current_dir)
        self.list_widget.clear()
        if hasattr(self, "preview_label"):
            self._clear_preview()

        keyword = self.search_box.text().strip().lower()

        try:
            entries = os.listdir(self.current_dir)
        except OSError:
            self.hint_label.setText("無法讀取此資料夾（權限不足），請切換其他路徑")
            return

        dirs, files = [], []
        for name in entries:
            if name.startswith("."):
                continue
            if keyword and keyword not in name.lower():
                continue
            full = os.path.join(self.current_dir, name)
            if os.path.isdir(full):
                dirs.append((name, full))
            elif name.lower().endswith(IMAGE_EXTS):
                files.append((name, full))

        dirs.sort(key=lambda t: t[0].lower())
        files.sort(key=lambda t: t[0].lower())

        folder_icon = make_icon(_draw_folder, force_white=False, dark=self.dark_mode)
        image_icon = make_icon(_draw_image_file, force_white=False, dark=self.dark_mode)

        for name, full in dirs:
            item = QListWidgetItem(folder_icon, name)
            item.setData(Qt.UserRole, full)
            item.setData(Qt.UserRole + 1, True)
            self.list_widget.addItem(item)

        for name, full in files:
            item = QListWidgetItem(image_icon, name)
            item.setData(Qt.UserRole, full)
            item.setData(Qt.UserRole + 1, False)
            self.list_widget.addItem(item)

# ----------------------------------------------------------------------
# 儲存確認對話盒
# ----------------------------------------------------------------------
class SaveConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("儲存結果圖片")
        self.setWindowIcon(get_app_icon())
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        msg = QLabel("要儲存這次的標註結果圖片嗎？")
        msg.setWordWrap(True)
        msg.setObjectName("TitleLabel")
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.close_btn = QPushButton("放棄")
        self.close_btn.setMinimumHeight(sz(52))
        self.close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.close_btn)

        self.save_btn = QPushButton("儲存")
        self.save_btn.setMinimumHeight(sz(52))
        self.save_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.save_btn)

        layout.addLayout(btn_row)


class FolderPickerDialog(QDialog):
    def __init__(self, parent=None, start_dir=None):
        super().__init__(parent)
        self.setWindowTitle("選擇儲存資料夾 ")
        self.setWindowIcon(get_app_icon())
        self.setModal(True)
        if parent is not None:
            self.setMaximumWidth(parent.window().width())
            self.setMaximumHeight(parent.window().height())
            self.resize(min(ui(380, 520), parent.window().width() - 20),
                        min(520, parent.window().height() - 60))

        self.current_dir = start_dir if start_dir and os.path.isdir(start_dir) else os.path.expanduser("~")
        self.selected_dir = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        nav_row = QHBoxLayout()
        self.up_btn = QPushButton("上層")
        self.up_btn.setFixedHeight(44)
        self.up_btn.clicked.connect(self.go_up)
        nav_row.addWidget(self.up_btn)
        layout.addLayout(nav_row)

        self.path_label = QLabel(self.current_dir)
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("SectionLabel")
        layout.addWidget(self.path_label)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.enter_folder)
        layout.addWidget(self.list_widget, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(sz(48))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        choose_btn = QPushButton("選擇這個資料夾")
        choose_btn.setMinimumHeight(sz(48))
        choose_btn.clicked.connect(self.choose_current)
        btn_row.addWidget(choose_btn)
        layout.addLayout(btn_row)

        self.refresh_list()

    def refresh_list(self):
        self.path_label.setText(self.current_dir)
        self.list_widget.clear()
        try:
            names = sorted(
                name for name in os.listdir(self.current_dir)
                if not name.startswith(".") and os.path.isdir(os.path.join(self.current_dir, name))
            )
        except OSError:
            names = []
        for name in names:
            self.list_widget.addItem(name)

    def enter_folder(self, item):
        new_dir = os.path.join(self.current_dir, item.text())
        if os.path.isdir(new_dir):
            self.current_dir = new_dir
            self.refresh_list()

    def go_up(self):
        parent_dir = os.path.dirname(self.current_dir.rstrip("/"))
        if parent_dir and os.path.isdir(parent_dir):
            self.current_dir = parent_dir
            self.refresh_list()

    def choose_current(self):
        self.selected_dir = self.current_dir
        self.accept()


class SaveFileDialog(QDialog):
    def __init__(self, parent=None, initial_dir=None, initial_filename=""):
        super().__init__(parent)
        self.setWindowTitle("儲存結果圖片")
        self.setWindowIcon(get_app_icon())
        self.setModal(True)
        if parent is not None:
            self.setMaximumWidth(parent.window().width())
            self.resize(min(ui(380, 520), parent.window().width() - 20), 1)

        remembered_dir = get_default_save_dir()
        if remembered_dir:
            self.current_dir = remembered_dir
        elif initial_dir and os.path.isdir(initial_dir):
            self.current_dir = initial_dir
        else:
            self.current_dir = os.path.expanduser("~")
        self.selected_path = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        name_label = QLabel("檔名：")
        name_label.setObjectName("SectionLabel")
        layout.addWidget(name_label)

        self.filename_edit = QLineEdit(initial_filename)
        self.filename_edit.setMinimumHeight(sz(48))
        layout.addWidget(self.filename_edit)

        folder_label = QLabel("儲存資料夾：")
        folder_label.setObjectName("SectionLabel")
        layout.addWidget(folder_label)

        folder_row = QHBoxLayout()
        self.folder_display = QLabel(self.current_dir)
        self.folder_display.setWordWrap(True)
        folder_row.addWidget(self.folder_display, stretch=1)

        change_folder_btn = QPushButton("更改")
        change_folder_btn.setMinimumHeight(sz(44))
        change_folder_btn.clicked.connect(self.choose_folder)
        folder_row.addWidget(change_folder_btn)
        layout.addLayout(folder_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(sz(52))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("儲存")
        save_btn.setMinimumHeight(sz(52))
        save_btn.clicked.connect(self.on_save_clicked)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def choose_folder(self):
        dlg = FolderPickerDialog(self, self.current_dir)
        if dlg.exec() == QDialog.Accepted and dlg.selected_dir:
            self.current_dir = dlg.selected_dir
            self.folder_display.setText(self.current_dir)
            # 按下「更改」並選定資料夾後，立即套用並記憶至設定檔（獨立鍵值，不與 home 共用）
            set_default_save_dir(self.current_dir)

    def on_save_clicked(self):
        name = self.filename_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "請輸入檔名")
            return
        if not os.path.splitext(name)[1]:
            name += ".jpg"
        self.selected_path = os.path.join(self.current_dir, name)
        self.accept()

# ----------------------------------------------------------------------
# 主分析頁面
# ----------------------------------------------------------------------
class AnalysisPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_path = None
        self.last_output_img = None
        self.worker = None
        self.last_clean_img = None
        self.method = "lsc"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.back_btn = QPushButton()
        self.back_btn.setFixedSize(sz(48), sz(48))
        self.back_btn.clicked.connect(self.back_requested.emit)
        top_row.addWidget(self.back_btn)

        self.file_name_label = QLabel("")
        self.file_name_label.setObjectName("TitleLabel")
        self.file_name_label.setWordWrap(True)
        top_row.addWidget(self.file_name_label, stretch=1)
        layout.addLayout(top_row)

        hint = QLabel("在照片上拖曳圈選後自動放大聚焦（按「清除圈選」可還原全圖並重新選取）。雙指可縮放與移動圖片；滑鼠：滾輪縮放、右鍵拖曳移動、雙擊重設")
        hint.setObjectName("SectionLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.roi_widget = ImageROIWidget()
        self.roi_widget.setMinimumHeight(260)
        self.roi_widget.roi_changed.connect(self.update_thresh_preview)
        if IS_MOBILE:
            layout.addWidget(self.roi_widget, stretch=5)
        else:
            body_row = QHBoxLayout()
            body_row.setSpacing(12)
            body_row.addWidget(self.roi_widget, stretch=3)
            layout.addLayout(body_row, stretch=1)

        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        thresh_group_title = QLabel("二值化濾鏡設定：")
        thresh_group_title.setObjectName("SectionLabel")
        controls_layout.addWidget(thresh_group_title)

        thresh_info_row = QHBoxLayout()
        self.thresh_label = QLabel("門檻值：128")
        self.thresh_label.setObjectName("SectionLabel")
        thresh_info_row.addWidget(self.thresh_label)

        self.preview_cb = QCheckBox("開啟二值化預覽")
        self.preview_cb.setChecked(False)
        self.preview_cb.stateChanged.connect(self.on_preview_cb_changed)
        thresh_info_row.addWidget(self.preview_cb)

        self.invert_cb = QCheckBox("反轉黑白")
        self.invert_cb.setChecked(True)
        self.invert_cb.stateChanged.connect(self.update_thresh_preview)
        thresh_info_row.addWidget(self.invert_cb)
        controls_layout.addLayout(thresh_info_row)

        slider_row = QHBoxLayout()
        self.thresh_slider = QSlider(Qt.Horizontal)
        self.thresh_slider.setRange(0, 255)
        self.thresh_slider.setValue(128)
        self.thresh_slider.setMinimumHeight(sz(36))
        
        self.thresh_slider.sliderMoved.connect(self.on_slider_changed)
        self.thresh_slider.valueChanged.connect(self.on_slider_changed)
        slider_row.addWidget(self.thresh_slider, stretch=3)

        self.auto_otsu_btn = QPushButton("自動最佳化 (Otsu)")
        self.auto_otsu_btn.setObjectName("AutoButton")
        self.auto_otsu_btn.setMinimumHeight(sz(36))
        self.auto_otsu_btn.clicked.connect(self.run_auto_otsu)
        slider_row.addWidget(self.auto_otsu_btn, stretch=1)
        
        controls_layout.addLayout(slider_row)

        roi_btn_row = QHBoxLayout()
        self.clear_roi_btn = QPushButton("清除圈選")
        self.clear_roi_btn.setMinimumHeight(sz(48))
        self.clear_roi_btn.clicked.connect(self.clear_all_annotations)
        roi_btn_row.addWidget(self.clear_roi_btn)

        self.analyze_btn = QPushButton("分析選取區域")
        self.analyze_btn.setObjectName("AnalyzeButton")
        self.analyze_btn.setMinimumHeight(sz(48))
        self.analyze_btn.clicked.connect(self.run_analysis)
        roi_btn_row.addWidget(self.analyze_btn)
        controls_layout.addLayout(roi_btn_row)

        self.overlay_btn = QPushButton("標註：顯示")
        self.overlay_btn.setObjectName("MethodButton")
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setChecked(True)
        self.overlay_btn.setEnabled(False)
        self.overlay_btn.setMinimumHeight(sz(46))
        self.overlay_btn.clicked.connect(self.on_overlay_toggled)
        controls_layout.addWidget(self.overlay_btn)

        method_label = QLabel("計算方式：")
        method_label.setObjectName("SectionLabel")
        controls_layout.addWidget(method_label)

        method_row = QHBoxLayout()
        self.method_area_btn = QPushButton("周長面積法")
        self.method_area_btn.setObjectName("MethodButton")
        self.method_area_btn.setCheckable(True)
        self.method_area_btn.setChecked(False)
        self.method_area_btn.setMinimumHeight(sz(46))
        self.method_area_btn.clicked.connect(lambda: self.set_method("area_perimeter"))
        method_row.addWidget(self.method_area_btn)

        self.method_lsc_btn = QPushButton("最小平方圓法")
        self.method_lsc_btn.setObjectName("MethodButton")
        self.method_lsc_btn.setCheckable(True)
        self.method_lsc_btn.setChecked(True)
        self.method_lsc_btn.setMinimumHeight(sz(46))
        self.method_lsc_btn.clicked.connect(lambda: self.set_method("lsc"))
        method_row.addWidget(self.method_lsc_btn)
        controls_layout.addLayout(method_row)

        self.method_hint = QLabel("")
        self.method_hint.setObjectName("SectionLabel")
        self.method_hint.setWordWrap(True)
        controls_layout.addWidget(self.method_hint)

        area_row = QHBoxLayout()
        area_label = QLabel("忽略面積小於（像素²）：")
        area_label.setObjectName("SectionLabel")
        area_row.addWidget(area_label)
        self.min_area_spin = QSpinBox()
        self.min_area_spin.setRange(0, 100000)
        self.min_area_spin.setSingleStep(50)
        self.min_area_spin.setValue(100)
        area_row.addWidget(self.min_area_spin)
        controls_layout.addLayout(area_row)

        result_label = QLabel("量測結果")
        result_label.setObjectName("SectionLabel")
        controls_layout.addWidget(result_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(160)
        controls_layout.addWidget(self.table)

        self.save_btn = QPushButton("儲存標註結果圖片")
        self.save_btn.setMinimumHeight(sz(52))
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_result)
        controls_layout.addWidget(self.save_btn)

        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QScrollArea.NoFrame)
        self.controls_scroll.setWidget(controls_container)
        enable_swipe_scroll(self.controls_scroll.viewport())
        if IS_MOBILE:
            self.controls_scroll.setMinimumHeight(180)
            self.controls_scroll.setMaximumHeight(360)
            layout.addWidget(self.controls_scroll, stretch=2)
        else:
            # 桌機：左圖右控制面板
            self.controls_scroll.setMinimumWidth(430)
            self.controls_scroll.setMaximumWidth(520)
            body_row.addWidget(self.controls_scroll, stretch=2)

        self._update_table_headers()
        self._update_method_hint()

    def set_dark(self, dark):
        self.roi_widget.set_dark(dark)
        self.back_btn.setIcon(make_icon(_draw_back_arrow, force_white=True, dark=dark))
        self.back_btn.setIconSize(QSize(22, 22))

    def on_slider_changed(self, value):
        self.thresh_label.setText(f"門檻值：{value}")
        if not self.preview_cb.isChecked():
            self.preview_cb.setChecked(True)
        else:
            self.update_thresh_preview()

    def run_auto_otsu(self):
        if not self.image_path:
            return

        full_img = imread_unicode(self.image_path)
        if full_img is None:
            return

        roi = self.roi_widget.get_roi_original()
        if roi:
            x, y, w, h = roi
            crop = full_img[y:y+h, x:x+w].copy()
            if crop.size == 0:
                crop = full_img
        else:
            crop = full_img

        invert = self.invert_cb.isChecked()
        best_thresh = auto_calculate_otsu_threshold(crop, invert=invert)
        self.thresh_slider.setValue(best_thresh)

    def on_preview_cb_changed(self, state):
        if self.preview_cb.isChecked():
            self.update_thresh_preview()
        else:
            if self.last_output_img is not None:
                self._show_result_pixmap()
            else:
                self.roi_widget.clear_processed_overlay()

    def _reset_overlay_ui(self):
        self.last_clean_img = None
        self.overlay_btn.setChecked(True)
        self.overlay_btn.setText("標註：顯示")
        self.overlay_btn.setEnabled(False)

    def _show_result_pixmap(self):
        """依「標註」開關顯示結果圖（有標註 / 無標註的原圖）"""
        if self.last_output_img is None:
            return
        img = self.last_output_img
        if not self.overlay_btn.isChecked() and self.last_clean_img is not None:
            img = self.last_clean_img
        self.roi_widget.show_processed_pixmap(cvimg_to_qpixmap(img))

    def on_overlay_toggled(self):
        self.overlay_btn.setText("標註：顯示" if self.overlay_btn.isChecked() else "標註：隱藏")
        if self.last_output_img is None:
            return
        if self.preview_cb.isChecked():
            self.preview_cb.setChecked(False)   # 關閉二值化預覽，回到結果圖
        self._show_result_pixmap()

    def update_thresh_preview(self):
        if not self.image_path or not self.preview_cb.isChecked():
            return

        full_img = imread_unicode(self.image_path)
        if full_img is None:
            return

        roi = self.roi_widget.get_roi_original()
        if roi:
            x, y, w, h = roi
            crop = full_img[y:y+h, x:x+w].copy()
            if crop.size == 0:
                crop = full_img
            thresh_val = self.thresh_slider.value()
            invert = self.invert_cb.isChecked()
            binary_crop = process_binarization(crop, thresh_val=thresh_val, invert=invert)

            preview_canvas = full_img.copy()
            preview_canvas[y:y+h, x:x+w] = cv2.cvtColor(binary_crop, cv2.COLOR_GRAY2BGR)
            self.roi_widget.show_processed_pixmap(cvimg_to_qpixmap(preview_canvas))
        else:
            thresh_val = self.thresh_slider.value()
            invert = self.invert_cb.isChecked()
            binary_img = process_binarization(full_img, thresh_val=thresh_val, invert=invert)
            self.roi_widget.show_processed_pixmap(cvimg_to_qpixmap(binary_img))

    def set_method(self, method):
        self.method = method
        self.method_area_btn.setChecked(method == "area_perimeter")
        self.method_lsc_btn.setChecked(method == "lsc")
        self._update_method_hint()
        self._update_table_headers()
        self.table.setRowCount(0)
        self.save_btn.setEnabled(False)

    def _update_method_hint(self):
        if self.method == "lsc":
            self.method_hint.setText(
                "最小平方圓法：繪製藍色內接圓 (Rmin) 與紅色外切圓 (Rmax)\n"
                "真圓度 = (擬合半徑 - 真圓度誤差) / 擬合半徑"
            )
        else:
            self.method_hint.setText(
                "周長面積法：真圓度 = 4π × 面積 ÷ 周長²，完美正圓 = 100%"
            )

    def _update_table_headers(self):
        if self.method == "lsc":
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(["編號", "真圓度", "擬合直徑", "內切直徑", "外接直徑", "圓度誤差"])
        else:
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["編號", "真圓度", "面積", "周長"])

    def load_image(self, path):
        self.image_path = path
        self.last_output_img = None
        self._reset_overlay_ui()
        self.file_name_label.setText(os.path.basename(path))
        self.table.setRowCount(0)
        self.save_btn.setEnabled(False)

        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.critical(self, "錯誤", f"無法讀取圖片：{path}")
            return
        self.roi_widget.set_pixmap(pix, pix.size())
        if self.preview_cb.isChecked():
            self.update_thresh_preview()

    def clear_all_annotations(self):
        """徹底清除圈選區，並恢復為原圖載入時的縮放與狀態"""
        self.roi_widget.clear_selection()
        self.last_output_img = None
        self._reset_overlay_ui()
        self.table.setRowCount(0)
        self.save_btn.setEnabled(False)

        if self.image_path:
            pix = QPixmap(self.image_path)
            if not pix.isNull():
                self.roi_widget.set_pixmap(pix, pix.size())
        if self.preview_cb.isChecked():
            self.update_thresh_preview()

    def run_analysis(self):
        if not self.image_path:
            return

        full_img = imread_unicode(self.image_path)
        if full_img is None:
            QMessageBox.critical(self, "錯誤", "無法讀取原始圖片檔案")
            return

        roi = self.roi_widget.get_roi_original()
        if roi:
            x, y, w, h = roi
            crop = full_img[y:y + h, x:x + w].copy()
            if crop.size == 0:
                crop = full_img
        else:
            crop = full_img

        self.analyze_btn.setEnabled(False)
        self.clear_roi_btn.setEnabled(False)
        self.analyze_btn.setText("分析中…")

        self.worker = AnalyzeWorker(
            min_area=self.min_area_spin.value(),
            image_array=crop,
            method=self.method,
            thresh_val=self.thresh_slider.value(),
            invert_thresh=self.invert_cb.isChecked()
        )
        self.worker.finished_ok.connect(self.on_analysis_done)
        self.worker.failed.connect(self.on_analysis_failed)
        self.worker.start()

    def on_analysis_done(self, results, output_crop_img):
        self.analyze_btn.setEnabled(True)
        self.clear_roi_btn.setEnabled(True)
        self.analyze_btn.setText("分析選取區域")

        full_img = imread_unicode(self.image_path)
        roi = self.roi_widget.get_roi_original()

        self.last_clean_img = full_img.copy() if full_img is not None else None
        if roi and full_img is not None:
            x, y, w, h = roi
            full_img[y:y + h, x:x + w] = output_crop_img
            self.last_output_img = full_img
        else:
            self.last_output_img = output_crop_img

        self.preview_cb.setChecked(False)
        self.overlay_btn.setEnabled(True)
        self.overlay_btn.setChecked(True)
        self.overlay_btn.setText("標註：顯示")
        self._show_result_pixmap()

        self._update_table_headers()
        self.table.setRowCount(0)
        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(r["id"])))
            if r.get("method") == "lsc":
                self.table.setItem(row, 1, QTableWidgetItem(f"{r['circularity']*100:.2f}%"))
                self.table.setItem(row, 2, QTableWidgetItem(f"{r['d_fit']:.1f}"))
                self.table.setItem(row, 3, QTableWidgetItem(f"{r['d_in']:.1f}"))
                self.table.setItem(row, 4, QTableWidgetItem(f"{r['d_out']:.1f}"))
                self.table.setItem(row, 5, QTableWidgetItem(f"{r['roundness_error']:.2f}"))
            else:
                self.table.setItem(row, 1, QTableWidgetItem(f"{r['circularity']*100:.2f}%"))
                self.table.setItem(row, 2, QTableWidgetItem(f"{r['area']:.1f}"))
                self.table.setItem(row, 3, QTableWidgetItem(f"{r['perimeter']:.1f}"))

        self.save_btn.setEnabled(len(results) > 0)

        if not results:
            QMessageBox.information(self, "提示", "未偵測到符合條件的圓形輪廓，請調整二值化門檻、最小面積或重新圈選範圍。")

    def on_analysis_failed(self, msg):
        self.analyze_btn.setEnabled(True)
        self.clear_roi_btn.setEnabled(True)
        self.analyze_btn.setText("分析選取區域")
        QMessageBox.critical(self, "分析失敗", msg)

    def save_result(self):
        if self.last_output_img is None:
            return

        confirm = SaveConfirmDialog(self)
        if confirm.exec() != QDialog.Accepted:
            return

        default_dir = os.path.dirname(self.image_path) or os.path.expanduser("~")
        base, ext = os.path.splitext(os.path.basename(self.image_path))
        default_name = f"{base}_result{ext or '.jpg'}"

        save_dlg = SaveFileDialog(self, default_dir, default_name)
        if save_dlg.exec() != QDialog.Accepted or not save_dlg.selected_path:
            return

        path = save_dlg.selected_path
        ok = imwrite_unicode(path, self.last_output_img)
        if ok:
            QMessageBox.information(self, "完成", f"已儲存：{path}")
        else:
            QMessageBox.critical(self, "錯誤", "儲存失敗，請確認路徑權限。")

# ----------------------------------------------------------------------
# QSS 樣式表
# ----------------------------------------------------------------------
SCROLLBAR_QSS_COMMON = """
QScrollBar:vertical {
    background: #E8ECEF;
    width: 28px;
    margin: 0px;
    border-radius: 14px;
}
QScrollBar::handle:vertical {
    background: #2F6FED;
    min-height: 40px;
    border-radius: 10px;
    margin: 3px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {
    background: #1E52C7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background: #E8ECEF;
    height: 28px;
    margin: 0px;
    border-radius: 14px;
}
QScrollBar::handle:horizontal {
    background: #2F6FED;
    min-width: 40px;
    border-radius: 10px;
    margin: 3px;
}
QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {
    background: #1E52C7;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
"""

DARK_SCROLLBAR_QSS_COMMON = """
QScrollBar:vertical {
    background: #262B36;
    width: 28px;
    margin: 0px;
    border-radius: 14px;
}
QScrollBar::handle:vertical {
    background: #3D7BFF;
    min-height: 40px;
    border-radius: 10px;
    margin: 3px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {
    background: #2A5BD1;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background: #262B36;
    height: 28px;
    margin: 0px;
    border-radius: 14px;
}
QScrollBar::handle:horizontal {
    background: #3D7BFF;
    min-width: 40px;
    border-radius: 10px;
    margin: 3px;
}
QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {
    background: #2A5BD1;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
"""

SLIDER_QSS_LIGHT = """
QSlider::groove:horizontal {
    border: 1px solid #C7CCD6;
    height: 8px;
    background: #E4E7EC;
    border-radius: 4px;
}
QSlider::sub-page:horizontal {
    background: #2F6FED;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: #2F6FED;
    border: 2px solid white;
    width: 22px;
    height: 22px;
    margin: -7px 0;
    border-radius: 11px;
}
QCheckBox {
    font-size: 13px;
    font-weight: bold;
}
QPushButton#AutoButton {
    background-color: #20BF6B;
    color: white;
    font-size: 12px;
    padding: 4px 8px;
    border-radius: 6px;
}
QPushButton#AutoButton:pressed {
    background-color: #1A9D58;
}
"""

SLIDER_QSS_DARK = """
QSlider::groove:horizontal {
    border: 1px solid #3A4150;
    height: 8px;
    background: #262B36;
    border-radius: 4px;
}
QSlider::sub-page:horizontal {
    background: #3D7BFF;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: #3D7BFF;
    border: 2px solid #1A1D24;
    width: 22px;
    height: 22px;
    margin: -7px 0;
    border-radius: 11px;
}
QCheckBox {
    font-size: 13px;
    color: #E7EAF0;
    font-weight: bold;
}
QPushButton#AutoButton {
    background-color: #20BF6B;
    color: white;
    font-size: 12px;
    padding: 4px 8px;
    border-radius: 6px;
}
QPushButton#AutoButton:pressed {
    background-color: #1A9D58;
}
"""

LIGHT_QSS = """
QMainWindow, QWidget { background-color: #F5F6F8; color: #1E222C; }
QDialog, QMessageBox {
    background-color: #F5F6F8;
    border: 2px solid #0A0907;
    border-radius: 0px;
}
QPushButton {
    background-color: #2F6FED; color: white; border-radius: 10px;
    padding: 10px; font-size: 15px; font-weight: bold;
}
QPushButton:pressed { background-color: #1E52C7; }
QPushButton:disabled { background-color: #A9B4C4; }
QPushButton#MethodButton { background-color: #9AA4B5; color: #1E222C; }
QPushButton#MethodButton:pressed { background-color: #838DA0; }
QPushButton#MethodButton:checked { background-color: #2F6FED; color: white; border: 2px solid #FF8A3D; }
QPushButton#AnalyzeButton { background-color: #FF8A3D; color: white; }
QPushButton#AnalyzeButton:pressed { background-color: #E56F1F; }
QPushButton#AnalyzeButton:disabled { background-color: #E8C6A6; }
QLineEdit, QSpinBox {
    background-color: white; border: 1px solid #C7CCD6; border-radius: 6px;
    padding: 8px; font-size: 15px; min-height: 28px;
}
QListWidget {
    background-color: white; border: 1px solid #C7CCD6; border-radius: 8px;
    font-size: 15px;
}
QScrollArea { background-color: transparent; border: none; }
QScrollArea > QWidget > QWidget { background-color: transparent; }
QListWidget::item { padding: 10px 6px; }
QListWidget::item:selected { background-color: #DCE6FF; color: #1E222C; }
QTableWidget {
    background-color: white; border: 1px solid #C7CCD6; border-radius: 6px;
    font-size: 13px; gridline-color: #E0E3E8;
}
QHeaderView::section {
    background-color: #E4E7EC; padding: 6px; border: none; font-weight: bold;
}
QLabel#TitleLabel { font-size: 19px; font-weight: bold; }
QLabel#SectionLabel { font-size: 13px; color: #5B6472; }
QLineEdit#PathEdit {
    font-size: 13px; color: #2F6FED; background-color: #EEF3FF;
    border: 1px dashed #9CB6F5; border-radius: 6px; padding: 6px 8px; min-height: 20px;
}
""" + SCROLLBAR_QSS_COMMON + SLIDER_QSS_LIGHT

DARK_QSS = """
QMainWindow, QWidget { background-color: #1A1D24; color: #E7EAF0; }
QDialog, QMessageBox {
    background-color: #1A1D24;
    border: 2px solid #E5E2DB;
    border-radius: 0px;
}
QPushButton {
    background-color: #3D7BFF; color: white; border-radius: 10px;
    padding: 10px; font-size: 15px; font-weight: bold;
}
QPushButton:pressed { background-color: #2A5BD1; }
QPushButton:disabled { background-color: #454C5B; }
QPushButton#MethodButton { background-color: #454C5B; color: #E7EAF0; }
QPushButton#MethodButton:pressed { background-color: #363C48; }
QPushButton#MethodButton:checked { background-color: #3D7BFF; color: white; border: 2px solid #26D9C0; }
QPushButton#AnalyzeButton { background-color: #FF8A3D; color: white; }
QPushButton#AnalyzeButton:pressed { background-color: #E56F1F; }
QPushButton#AnalyzeButton:disabled { background-color: #6B5138; }
QLineEdit, QSpinBox {
    background-color: #262B36; color: #E7EAF0; border: 1px solid #3A4150; border-radius: 6px;
    padding: 8px; font-size: 15px; min-height: 28px;
}
QListWidget {
    background-color: #20242E; border: 1px solid #3A4150; border-radius: 8px;
    font-size: 15px; color: #E7EAF0;
}
QScrollArea { background-color: transparent; border: none; }
QScrollArea > QWidget > QWidget { background-color: transparent; }
QListWidget::item { padding: 10px 6px; }
QListWidget::item:selected { background-color: #2A3550; color: #FFFFFF; }
QTableWidget {
    background-color: #20242E; color: #E7EAF0; border: 1px solid #3A4150; border-radius: 6px;
    font-size: 13px; gridline-color: #3A4150;
}
QHeaderView::section {
    background-color: #262B36; color: #E7EAF0; padding: 6px; border: none; font-weight: bold;
}
QLabel#TitleLabel { font-size: 19px; font-weight: bold; color: #E7EAF0; }
QLabel#SectionLabel { font-size: 13px; color: #9AA4B5; }
QLineEdit#PathEdit {
    font-size: 13px; color: #7FB0FF; background-color: #20263A;
    border: 1px dashed #3D5A99; border-radius: 6px; padding: 6px 8px; min-height: 20px;
}
""" + DARK_SCROLLBAR_QSS_COMMON + SLIDER_QSS_DARK

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("真圓度量測 06C(R4P2)")
        self.setWindowIcon(get_app_icon())

        # 視窗大小/位置變更後，用計時器延遲寫入設定檔（debounce），
        # 避免拖曳調整視窗過程中每個像素變化都觸發一次磁碟寫入
        self._geo_save_timer = QTimer(self)
        self._geo_save_timer.setSingleShot(True)
        self._geo_save_timer.setInterval(500)
        self._geo_save_timer.timeout.connect(self._save_window_geometry)
        self._restoring_geometry = True
        self._apply_saved_geometry()  # 還原上次記住的視窗大小與位置（若無則使用預設）
        self._restoring_geometry = False

        self.dark_mode = get_default_dark_mode()  # 讀取設定檔中上次記住的顯示模式

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.browser_page = FileBrowserPage()
        self.analysis_page = AnalysisPage()

        self.stack.addWidget(self.browser_page)
        self.stack.addWidget(self.analysis_page)

        self.browser_page.file_opened.connect(self._open_image)
        self.analysis_page.back_requested.connect(self._back_to_browser)

        self._build_theme_toggle()
        self._apply_theme()

    def _apply_saved_geometry(self):
        """啟動時還原上次記住的視窗大小與位置；若沒有記錄則使用預設大小"""
        geo = get_default_window_geometry()
        if geo:
            self.setGeometry(geo["x"], geo["y"], geo["w"], geo["h"])
        else:
            self.resize(*ui((420, 800), (1180, 760)))

    def _schedule_geometry_save(self):
        """排程一次延遲寫入；還原設定造成的大小/位置變化不觸發寫入"""
        if getattr(self, "_restoring_geometry", False):
            return
        if hasattr(self, "_geo_save_timer"):
            self._geo_save_timer.start()

    def _save_window_geometry(self):
        try:
            g = self.geometry()
            set_default_window_geometry(g.x(), g.y(), g.width(), g.height())
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_geometry_save()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_geometry_save()

    def closeEvent(self, event):
        # 關閉視窗前，立即（不等待計時器）寫入最終的大小與位置，
        # 確保縮放/移動後就算馬上關閉程式，設定也不會遺失
        if hasattr(self, "_geo_save_timer"):
            self._geo_save_timer.stop()
        self._save_window_geometry()
        super().closeEvent(event)

    def _build_theme_toggle(self):
        self.theme_btn = QPushButton("模式")
        self.theme_btn.setFixedSize(sz(72), sz(40))
        self.theme_btn.clicked.connect(self.toggle_theme)

        self.theme_icon = ThemeIcon(dark_mode=self.dark_mode)

        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        toggle_row.addWidget(self.theme_icon)
        toggle_row.addWidget(self.theme_btn)

        self.browser_page.layout().insertLayout(0, toggle_row)

        toggle_row2 = QHBoxLayout()
        toggle_row2.addStretch(1)
        self.theme_icon2 = ThemeIcon(dark_mode=self.dark_mode)
        self.theme_btn2 = QPushButton("模式")
        self.theme_btn2.setFixedSize(sz(72), sz(40))
        self.theme_btn2.clicked.connect(self.toggle_theme)
        toggle_row2.addWidget(self.theme_icon2)
        toggle_row2.addWidget(self.theme_btn2)
        self.analysis_page.layout().insertLayout(0, toggle_row2)

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.theme_icon.set_dark(self.dark_mode)
        self.theme_icon2.set_dark(self.dark_mode)
        self._apply_theme()
        set_default_dark_mode(self.dark_mode)  # 寫入 JSON 設定檔，下次啟動自動套用

    def _apply_theme(self):
        self.setStyleSheet(adapt_qss(DARK_QSS if self.dark_mode else LIGHT_QSS))
        self.browser_page.set_dark(self.dark_mode)
        self.analysis_page.set_dark(self.dark_mode)

    def _open_image(self, path):
        self.analysis_page.load_image(path)
        self.stack.setCurrentWidget(self.analysis_page)

    def _back_to_browser(self):
        self.stack.setCurrentWidget(self.browser_page)

def main():
    _install_crash_logger()
    app = QApplication(sys.argv)
    app.setWindowIcon(get_app_icon())  # 設定應用程式圖示，套用至所有視窗（含對話框）標題列
    win = MainWindow()
    result = verify_or_exit(app)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

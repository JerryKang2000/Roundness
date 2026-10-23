#!/usr/bin/env python3
"""license_verify.py - 單檔獨立授權驗證程式 (Ed25519)。

不需要其他模組。有安裝 `cryptography` 就用它驗簽章;沒有的話自動改用內建的純 Python
Ed25519 驗證(只用標準函式庫),所以可以直接複製這一個檔案到任何有 Python 3.8+ 的機器執行,
也能用 PyInstaller / Nuitka 打包成單一 exe。

驗證內容: 簽章 -> 產品 -> 主機板 SN -> 簽發時間下限 -> 到期日(以網路時間為準),
並含離線保護(續期碼、多處紀錄、檔案時間戳見證、ClockGuard)。

使用前: 把 `keygen.py pubkey <產品> --raw` 的輸出貼到下方 PUBLIC_KEYS
        (keygen_gui 的「複製 PUBLIC_KEYS 片段」可一次產生全部產品),
        並把 STATE_SALT 改成你自己的隨機字串。
        PUBLIC_KEYS 的值(以及 --pubkey)也可以直接放 <產品>.public.pem 的內容或檔案路徑。
授權檔: license.lic 可以是純授權碼,也可以直接貼 keygen 的輸出(text / csv / json 皆可);
        同一個檔案放多個 SN、多個產品也行,會自動挑出「本產品 + 本機 SN」的那一筆。
        離線設備的續期碼放在 renewal.lic,規則相同。

命令列:
    python license_verify.py                                 # 無引數: 預設顯示本機主機板 SN(客戶回報給你發碼用)
    python license_verify.py --show-sn                       # 同上(明確指定)
    python license_verify.py DDAS                              # 驗證(授權碼取自 license.lic / 環境變數)
    python license_verify.py DDAS --license <授權碼> --json  # 給其他程式呼叫,輸出 JSON
    python license_verify.py DDAS --apply-renewal <續期碼>     # 離線設備套用續期碼
  結束碼: 0 = 有效, 1 = 無效或錯誤。

在 Python 程式中:
    from license_verify import require_license_online
    result = require_license_online("DDAS")     # 失敗會印出原因並 sys.exit(1)
    ...
    result.guard.check()                        # 長時間執行時定期呼叫
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:  # 優先使用經過稽核的 cryptography;沒有就退回內建純 Python 實作
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    _HAVE_CRYPTO = True
except ImportError:  # pragma: no cover
    _HAVE_CRYPTO = False


def _exe_dir_from_argv0() -> Optional[Path]:
    """由 sys.argv[0] 推得「使用者啟動的那個 exe」所在資料夾(相對路徑 / 只打檔名經 PATH 啟動都處理)。"""
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return None
    if not os.path.dirname(argv0):               # 只有檔名(例如從 PATH 啟動)
        found = shutil.which(argv0)
        if not found:
            return None
        argv0 = found
    return Path(os.path.abspath(argv0)).resolve().parent


def _app_dir() -> Path:
    """授權檔所在資料夾 = 「exe 放的位置」(開發時為本檔所在資料夾)。

    - Nuitka(standalone / onefile): 此模組被編譯後,模組全域會有 __compiled__。
      onefile 模式下 __file__ 與 sys.executable 都指向暫存解壓資料夾,不能用;
      改用 __compiled__.containing_dir(原始 exe 所在處),取不到再用 original_argv0 / sys.argv[0]。
    - PyInstaller 等: sys.frozen 為 True,sys.executable 就是 exe。
    - 一般 .py 執行: 本檔所在資料夾。
    """
    comp = globals().get("__compiled__")
    if comp is not None:
        for attr in ("containing_dir",):
            d = getattr(comp, attr, None)
            if d:
                return Path(d).resolve()
        orig = getattr(comp, "original_argv0", None)
        if orig:
            return Path(os.path.abspath(orig)).resolve().parent
        d = _exe_dir_from_argv0()
        if d is not None:
            return d
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# ============================== 設定區 ==============================
'''
PUBLIC_KEYS = {                      # 各產品公鑰(raw base64, 32 bytes)
    "DDAS": "PASTE_DDAS_PUBLIC_KEY_HERE",
    "RW": "PASTE_RW_PUBLIC_KEY_HERE",
}
'''

#
PUBLIC_KEYS = {
    "A": "2gFUmPr24kdL+wRb7TTBbb7RdXwrWzspWC/Ma5PhnlA=",
    "B": "m6tTDcBEy6JLOWr5Ok0Valt+5Ul2IDkcrPf9Y0UlGEg=",
    "C": "3M4rKl/XPjHt1Riln3FZIj4XBZS1lRxQizgOI+Jd7Ok=",
    "D": "fOFJwo6UnhM4yhUyUPq3VZogu2Y5blzB2MAIIsqjvjA=",
    "E": "reCje++xsXmBI2jl8TLOd82Rv8rpNDeSUvxQNAoNwDw=",
    "F": "P2DQkbTXgfp3DwLDivNfmoLqMxT2QMZdDfnuanJoeTU=",
}
#



# 授權碼的 SN 欄位若是這個值,代表「不綁定硬體」:不比對主機板序號,
# 即使本機讀不到序號也照樣通過(簽章、產品、到期日仍會照常檢查)。須與 keygen.py 一致。
NO_HW_SN = "不檢查硬體序號"

LICENSE_FILE = _app_dir() / "license.lic"
RENEWAL_FILE = _app_dir() / "renewal.lic"
LICENSE_ENV = "APP_LICENSE"
RENEWAL_ENV = "APP_RENEWAL"

HTTPS_SOURCES = ["https://www.google.com", "https://www.cloudflare.com", "https://www.microsoft.com"]
NTP_SERVERS = ["time.google.com", "time.cloudflare.com", "pool.ntp.org"]
REQUEST_TIMEOUT = 4.0           # 每個時間來源的逾時秒數
MAX_SOURCE_SPREAD = 120         # 多個來源彼此差超過幾秒視為不可信
OFFLINE_GRACE_DAYS = 7          # 一般授權的離線寬限天數(距上次成功連線驗證)
ROLLBACK_TOLERANCE = 300        # 容許的時鐘倒退秒數
STATE_SALT = b"change-me-per-app"    # 請改成你自己的隨機字串(僅增加竄改門檻)

WITNESS_TOLERANCE = 3600        # 檔案時間比現在晚超過幾秒才算「未來」
WITNESS_MIN_FILES = 3           # 至少幾個「未來檔案」才判定時間被往回調
WITNESS_MAX_AHEAD_DAYS = 730    # 比現在晚超過幾天的檔案視為亂填,忽略
WITNESS_PER_DIR_LIMIT = 2000    # 每個資料夾最多檢查幾個項目
GUARD_TOLERANCE = 120.0         # ClockGuard 容許的時鐘倒退秒數
# ====================================================================

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_NTP_EPOCH_OFFSET = 2208988800
_REG_PATH = r"Software\AppLicense"
_BAD_SN = {
    "", "0", "none", "n/a", "na", "default string", "not applicable", "not specified",
    "to be filled by o.e.m.", "system serial number", "base board serial number", "unknown",
    "9774d56d682e549c",             # 舊版 Android 模擬器/部分機型共用的重複 ANDROID_ID
}


class LicenseError(Exception):
    """授權無效,訊息為原因。"""


class OnlineTimeError(Exception):
    """無法取得可信的網路時間。"""


def _norm_sn(s) -> str:
    """SN 比對用的正規化:全形轉半形(NFKC)、去掉所有空白(含中間與不可見字元)、轉大寫。
    避免因為多打空白、大小寫、全形字元或 BOM 造成「看起來一樣卻比對不過」。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = "".join(ch for ch in s if not ch.isspace() and ch not in "﻿​‌‍")
    return s.upper()


def _sn_matches(lic_sn, local_sn) -> bool:
    if _norm_sn(lic_sn) == _norm_sn(local_sn):
        return True
    # Android: 不同執行環境(獨立執行 / 被 GUI 程式呼叫)可能讀到不同的 .device_id,
    # 只要授權碼 SN 與任何一個候選裝置 ID 相符就算通過
    try:
        if _is_android():
            want = _norm_sn(lic_sn)
            return any(_norm_sn(c) == want for c in _android_device_id_candidates())
    except Exception:
        pass
    return False


def _is_no_hw(lic_sn) -> bool:
    return _norm_sn(lic_sn) == _norm_sn(NO_HW_SN)


@dataclass
class LicenseInfo:
    product: str
    sn: str
    expires: Optional[str]              # YYYY-MM-DD,None 代表無到期日
    days_left: Optional[int]
    issued: Optional[datetime] = None   # 授權簽發時間(UTC)
    renewable: bool = False             # True 代表離線設備需要續期碼


@dataclass
class RenewalInfo:
    product: str
    sn: str
    issued: datetime
    until: date


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ======================= Ed25519 驗證(RFC 8032) =======================
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _pt_add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _pt_mul(s: int, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _pt_add(q, p)
        p = _pt_add(p, p)
        s >>= 1
    return q


def _pt_eq(p, q) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _pt_decode(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _ed25519_verify_py(pub: bytes, sig: bytes, msg: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    a, r = _pt_decode(pub), _pt_decode(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
    return _pt_eq(_pt_mul(s, _G), _pt_add(r, _pt_mul(h, a)))


def ed25519_verify(pub: bytes, sig: bytes, msg: bytes, force_pure: bool = False) -> bool:
    if _HAVE_CRYPTO and not force_pure:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except (InvalidSignature, ValueError):
            return False
    return _ed25519_verify_py(pub, sig, msg)


def crypto_engine() -> str:
    return "cryptography" if _HAVE_CRYPTO else "內建純 Python"


# ============================ 授權碼驗證 ============================
def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")   # Ed25519 公鑰 DER 前綴(12 bytes)


def parse_public_key(value: str) -> bytes:
    """把公鑰轉成 32 bytes raw。接受: raw base64 / PEM 文字 / 公鑰檔路徑(PEM 或 raw base64)
    -- 也就是 keygen.py 的 verify --pubkey 能吃的格式。格式不對丟 ValueError。"""
    text = value.strip()
    if "\n" not in text and len(text) < 1024 and os.path.isfile(text):
        text = Path(text).read_text(encoding="utf-8").strip()
    if text.startswith("-----BEGIN"):
        body = "".join(l for l in text.splitlines() if l and not l.startswith("-----"))
        der = base64.b64decode(body, validate=True)
        if len(der) != 44 or not der.startswith(_ED25519_SPKI_PREFIX):
            raise ValueError("不是 Ed25519 公鑰")
        return der[len(_ED25519_SPKI_PREFIX):]
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 32:
        raise ValueError("公鑰長度不是 32 bytes")
    return raw


def _load_public_key(product: str, override: Optional[str]) -> bytes:
    value = override or PUBLIC_KEYS.get(product)
    if not value:
        raise LicenseError(f"未知產品 {product!r}")
    try:
        return parse_public_key(value)
    except Exception:
        raise LicenseError(f"產品 {product} 的公鑰尚未設定或格式不正確")


def _read_lines(path: Path) -> List[str]:
    try:
        return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{86}(?![A-Za-z0-9_-])")


def read_tokens(path: Path) -> List[str]:
    """讀出檔案中所有「長得像授權碼」的字串(# 開頭的行略過)。
    純授權碼一行一個可以,直接貼 keygen 的 text / csv / json 輸出也可以。"""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    found: List[str] = []
    for line in text.splitlines():
        if not line.strip().startswith("#"):
            found += _TOKEN_RE.findall(line)
    return found


def peek_token(token: str) -> dict:
    """不驗簽章、只讀出 payload(僅用來從檔案中挑選授權碼;真正的驗證仍由 verify_token 負責)。"""
    try:
        body = json.loads(_b64url_decode(token.split(".")[0]).decode("utf-8"))
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def pick_token(candidates: List[str], product: Optional[str], sn: Optional[str],
               sn_unknown: bool = False) -> Optional[str]:
    """從候選中挑出屬於 product / sn 的授權碼(多筆取簽發時間最新);都不符則回傳第一筆,
    讓後續驗證報出真正的原因(例如「與本機主機板序號不符」)。
    「不檢查硬體序號」的授權碼對任何 sn 都算符合;sn_unknown=True(本機讀不到序號)時,
    只有這種授權碼算符合。"""
    good = []
    for t in candidates:
        b = peek_token(t)
        if b.get("k", "lic") != "lic" or not (product is None or b.get("p") == product):
            continue
        if _is_no_hw(b.get("sn")):
            sn_ok = True
        elif sn_unknown:
            sn_ok = False
        else:
            sn_ok = sn is None or _sn_matches(b.get("sn"), sn)
        if sn_ok:
            good.append((b.get("iat") or "", t))
    if good:
        return max(good)[1]
    for t in candidates:                    # 都不符: 優先回傳同產品的,讓錯誤原因指向 SN 而不是產品
        b = peek_token(t)
        if b.get("k", "lic") == "lic" and (product is None or b.get("p") == product):
            return t
    return candidates[0] if candidates else None


def find_token(token: Optional[str] = None, license_file: Optional[Path] = None,
               product: Optional[str] = None, sn: Optional[str] = None,
               sn_unknown: bool = False) -> str:
    if token and token.strip():
        return token.strip()
    env = os.environ.get(LICENSE_ENV, "").strip()
    if env:
        return env
    path = Path(license_file) if license_file else LICENSE_FILE
    picked = pick_token(read_tokens(path), product, sn, sn_unknown)
    if picked:
        return picked
    lines = _read_lines(path)               # 檔案裡沒有任何符合格式的授權碼: 保留舊行為,交給驗證報錯
    if lines:
        return lines[0]
    raise LicenseError(f"找不到授權碼(可放在 {path.name},或設定環境變數 {LICENSE_ENV})")


def _open_token(token: str, pub: bytes) -> dict:
    """驗簽章並回傳 payload(尚未檢查任何欄位內容)。"""
    try:
        p_b64, s_b64 = token.strip().split(".")
        payload, sig = _b64url_decode(p_b64), _b64url_decode(s_b64)
    except Exception:
        raise LicenseError("授權碼格式不正確")
    if not ed25519_verify(pub, sig, payload):   # 一定要先驗簽章,再信任 payload 內容
        raise LicenseError("授權碼簽章無效")
    try:
        body = json.loads(payload.decode("utf-8"))
    except ValueError:
        raise LicenseError("授權碼內容無法解析")
    if not isinstance(body, dict):
        raise LicenseError("授權碼內容無法解析")
    return body


def _parse_ts(s) -> datetime:
    try:
        return datetime.strptime(s, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise LicenseError("授權碼時間欄位格式不正確")


def verify_token(token: str, product: str, sn: Optional[str], public_key_b64: Optional[str] = None,
                 today: Optional[date] = None, check_expiry: bool = True) -> LicenseInfo:
    """純驗證: 不讀硬體。check_expiry=False 則只驗簽章 / 產品 / SN。"""
    body = _open_token(token, _load_public_key(product, public_key_b64))
    if body.get("k", "lic") != "lic":            # 擋掉續期碼被當成授權碼
        raise LicenseError("這不是授權碼(類型不符)")
    if body.get("p") != product:
        raise LicenseError(f"授權碼不屬於產品 {product}")
    if not _is_no_hw(body.get("sn")):            # 「不檢查硬體序號」的授權碼(已通過簽章)略過 SN 比對
        if sn is None:
            raise LicenseError("無法取得有效的主機板序號")
        if not _sn_matches(body.get("sn"), sn):
            raise LicenseError(f"授權碼與本機主機板序號不符(授權碼 SN={body.get('sn')!r},本機 SN={sn!r})")
    issued =_parse_ts(body["iat"]) if body.get("iat") else None
    exp = body.get("exp")
    days_left = None
    if exp:
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            raise LicenseError("授權碼到期日格式不正確")
        days_left = (exp_date - (today or datetime.now(timezone.utc).date())).days
        if check_expiry and days_left < 0:
            raise LicenseError(f"授權已於 {exp} 到期")
    return LicenseInfo(product=product, sn=body["sn"], expires=exp, days_left=days_left,
                       issued=issued, renewable=bool(body.get("rn")))


def verify_renewal(token: str, product: str, sn: Optional[str],
                   public_key_b64: Optional[str] = None) -> RenewalInfo:
    """驗證續期碼(簽章、類型、產品、SN),不判斷是否過期。"""
    body = _open_token(token, _load_public_key(product, public_key_b64))
    if body.get("k") != "renew":
        raise LicenseError("這不是續期碼(類型不符)")
    if body.get("p") != product:
        raise LicenseError(f"續期碼不屬於產品 {product}")
    if not _is_no_hw(body.get("sn")):
        if sn is None:
            raise LicenseError("無法取得有效的主機板序號")
        if not _sn_matches(body.get("sn"), sn):
            raise LicenseError(f"續期碼與本機主機板序號不符(續期碼 SN={body.get('sn')!r},本機 SN={sn!r})")
    try:
        until = datetime.strptime(body.get("until"), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise LicenseError("續期碼到期日格式不正確")
    return RenewalInfo(product=product, sn=body["sn"], issued=_parse_ts(body.get("iat")), until=until)


# ============================ 主機板 SN ============================
def _run(cmd: list) -> str:
    kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}   # 不跳出黑視窗
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15, **kwargs).stdout


def _is_android() -> bool:
    """是否在 Android 上執行(Kivy/python-for-android、Termux、官方 Android 版 Python 皆可)。"""
    return (hasattr(sys, "getandroidapilevel") or sys.platform == "android"
            or "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ
            or ("ANDROID_ROOT" in os.environ and "ANDROID_DATA" in os.environ))


def _android_private_dir() -> Path:
    """App 私有可寫資料夾(Kivy 的 ANDROID_PRIVATE;否則退回家目錄)。"""
    return Path(os.environ.get("ANDROID_PRIVATE") or Path.home())


def _android_device_id() -> str:
    """Android 裝置識別碼(App 無法讀硬體序號,改用下列來源,依序嘗試):
    1. Settings.Secure.ANDROID_ID(pyjnius,不需權限)。同一裝置 + 同一 App 簽署金鑰 + 同一使用者下固定;
       解除安裝重裝不變(簽署金鑰相同時),原廠重設會改變。
    2. getprop ro.serialno / ro.boot.serialno(Termux、adb shell 等環境才讀得到)。
    3. 寫在 App 私有資料夾的隨機 UUID(最後手段;清除資料或解除安裝後會改變)。
    回傳值一律加上 "ANDROID-" 前綴,與其他平台的 SN 區隔。"""
    # 1. ANDROID_ID
    try:
        from jnius import autoclass  # type: ignore
        ctx = None
        for cls_name, attr in (("org.kivy.android.PythonActivity", "mActivity"),
                               ("org.kivy.android.PythonService", "mService")):
            try:
                ctx = getattr(autoclass(cls_name), attr)
            except Exception:
                ctx = None
            if ctx is not None:
                break
        if ctx is not None:
            secure = autoclass("android.provider.Settings$Secure")
            aid = secure.getString(ctx.getContentResolver(), secure.ANDROID_ID)
            if aid and str(aid).strip().lower() not in _BAD_SN:
                return "ANDROID-" + str(aid).strip().upper()
    except Exception:
        pass
    # 2. 系統屬性中的序號
    for prop in ("ro.serialno", "ro.boot.serialno"):
        try:
            v = _run(["getprop", prop]).strip()
        except (OSError, subprocess.SubprocessError):
            v = ""
        if v and v.lower() not in _BAD_SN:
            return "ANDROID-" + v.upper()
    # 3. 隨機 ID 檔(依序檢查多個位置,已存在的優先;都沒有才新建於第一個可寫位置)
    files = _device_id_files()
    for p in files:
        try:
            if p.exists():
                v = p.read_text().strip()
                if v:
                    return "ANDROID-" + v
        except OSError:
            pass
    import uuid
    v = uuid.uuid4().hex.upper()
    for p in files:
        try:
            p.write_text(v)
            return "ANDROID-" + v
        except OSError:
            continue
    return ""


def _device_id_files() -> List[Path]:
    """隨機裝置 ID 檔(.device_id)可能所在的位置,依優先順序、去除重複。"""
    dirs = []
    for getter in (_app_dir, _android_private_dir, Path.home, Path.cwd):
        try:
            d = Path(getter())
        except Exception:
            continue
        if d not in dirs:
            dirs.append(d)
    return [d / ".device_id" for d in dirs]


def _android_device_id_candidates() -> List[str]:
    """本機所有可能的 Android 裝置 ID(ANDROID_ID、系統序號、各位置的 .device_id)。"""
    out: List[str] = []
    try:
        out.append(_android_device_id())
    except Exception:
        pass
    for p in _device_id_files():
        try:
            if p.exists():
                v = p.read_text().strip()
                if v:
                    out.append("ANDROID-" + v)
        except OSError:
            pass
    return [c for c in dict.fromkeys(out) if c]


def get_baseboard_sn() -> str:
    """取得本機主機板序號(Android 上為裝置識別碼);取不到或為廠商預設值時丟 LicenseError。"""
    sn = ""
    try:
        if _is_android():
            sn = _android_device_id()
        elif os.name == "nt":
            sn = _run(["powershell", "-NoProfile", "-Command",
                       "(Get-CimInstance Win32_BaseBoard).SerialNumber"]).strip()
            if not sn:
                lines = [l.strip() for l in _run(["wmic", "baseboard", "get", "serialnumber"]).splitlines()
                         if l.strip()]
                sn = lines[1] if len(lines) > 1 else ""
        elif sys.platform == "darwin":
            for line in _run(["ioreg", "-l"]).splitlines():
                if "IOPlatformSerialNumber" in line:
                    sn = line.split('"')[-2]
                    break
        else:
            p = Path("/sys/class/dmi/id/board_serial")
            sn = p.read_text().strip() if p.exists() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        sn = ""
    if sn.lower() in _BAD_SN:
        raise LicenseError("無法取得有效的主機板序號")
    return sn


# ============================ 網路時間 ============================
def _https_time(url: str, timeout: float) -> datetime:
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hdr = r.headers.get("Date")
    except urllib.error.HTTPError as e:      # 4xx/5xx 仍帶有 Date 標頭
        hdr = e.headers.get("Date") if e.headers else None
    if not hdr:
        raise ValueError("回應沒有 Date 標頭")
    dt = parsedate_to_datetime(hdr)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_ntp_packet(data: bytes) -> datetime:
    if len(data) < 48:
        raise ValueError("NTP 回應長度不足")
    mode, stratum = data[0] & 0x07, data[1]
    if mode != 4 or not 1 <= stratum <= 15:
        raise ValueError("NTP 回應無效")
    secs, frac = struct.unpack("!II", data[40:48])
    if secs == 0:
        raise ValueError("NTP 時間戳為 0")
    return datetime.fromtimestamp(secs - _NTP_EPOCH_OFFSET + frac / 2**32, timezone.utc)


def _ntp_time(host: str, timeout: float) -> datetime:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(b"\x1b" + 47 * b"\0", (host, 123))
        data, _ = s.recvfrom(64)
    return parse_ntp_packet(data)


def get_online_time(timeout: float = REQUEST_TIMEOUT, need: int = 2) -> Tuple[datetime, str]:
    """回傳 (UTC 時間, 來源說明)。優先 HTTPS(取 need 個來源的中位數),失敗才用 NTP。"""
    results = []
    for url in HTTPS_SOURCES:
        try:
            results.append((_https_time(url, timeout), url.split("//")[1]))
        except (OSError, ValueError, TypeError):
            continue
        if len(results) >= need:
            break
    if not results:
        for host in NTP_SERVERS:
            try:
                results.append((_ntp_time(host, timeout), "ntp:" + host))
                break
            except (OSError, ValueError):
                continue
    if not results:
        raise OnlineTimeError("所有網路時間來源皆無法連線")
    if len(results) >= 2:
        times = sorted(r[0] for r in results)
        if (times[-1] - times[0]).total_seconds() > MAX_SOURCE_SPREAD:
            raise OnlineTimeError("網路時間來源彼此不一致,可能遭竄改")
    dt, _ = sorted(results)[len(results) // 2]
    return dt.replace(microsecond=0), ", ".join(r[1] for r in results)


# ======================= 多處紀錄 / 檔案見證 / ClockGuard =======================
@dataclass
class State:
    last_online: datetime   # 最後一次取得可信時間(網路 / 續期)
    last_seen: datetime     # 最後一次見到的時間(單調不減)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _mac(key: bytes, last_online: str, last_seen: str) -> str:
    return hmac.new(key, f"{last_online}|{last_seen}".encode(), hashlib.sha256).hexdigest()


def _encode(key: bytes, st: State) -> str:
    lo, ls = _fmt(st.last_online), _fmt(st.last_seen)
    return json.dumps({"v": 1, "last_online": lo, "last_seen": ls, "mac": _mac(key, lo, ls)})


def _decode(key: bytes, text: str) -> Optional[State]:
    try:
        d = json.loads(text)
        if not hmac.compare_digest(d["mac"], _mac(key, d["last_online"], d["last_seen"])):
            return None
        p = lambda s: datetime.strptime(s, _TS_FMT).replace(tzinfo=timezone.utc)
        return State(p(d["last_online"]), p(d["last_seen"]))
    except (ValueError, KeyError, TypeError):
        return None


def _reg_read(name: str) -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (OSError, ImportError):
        return None


def _reg_write(name: str, text: str) -> None:
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, text)
    except (OSError, ImportError):
        pass


class StateStore:
    """「最後見到的時間」存多份(檔案 x N + Windows 登錄檔),讀取取所有有效紀錄的最大值,存檔時補齊副本。"""

    def __init__(self, paths: List[Path], registry_value: Optional[str] = None):
        self.paths = [Path(p) for p in paths]
        self.registry_value = registry_value

    def load(self, key: bytes) -> Optional[State]:
        found = []
        for p in self.paths:
            try:
                st = _decode(key, p.read_text(encoding="utf-8"))
            except OSError:
                st = None
            if st:
                found.append(st)
        if self.registry_value:
            st = _decode(key, _reg_read(self.registry_value) or "")
            if st:
                found.append(st)
        if not found:
            return None
        return State(max(s.last_online for s in found), max(s.last_seen for s in found))

    def save(self, key: bytes, state: State) -> None:
        text = _encode(key, state)
        for p in self.paths:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, p)
            except OSError:
                pass
        if self.registry_value:
            _reg_write(self.registry_value, text)


def _hashed(product: str, salt: bytes) -> str:
    return hashlib.sha256(salt + product.encode()).hexdigest()[:16]


def registry_value_name(product: str, salt: bytes) -> str:
    return _hashed(product, salt)


def default_state_paths(product: str, salt: bytes) -> List[Path]:
    """三個位置: 主要(可辨識檔名) + 兩個以雜湊命名的副本。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", product)
    home = Path.home()
    if _is_android():
        base = _android_private_dir()       # App 私有資料夾,一定可寫
        local, roam, prog = base / ".local" / "state", base / ".cache", base / ".local" / "share"
    elif os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or home)
        roam = Path(os.environ.get("APPDATA") or home)
        prog = Path(os.environ.get("PROGRAMDATA") or home)
    elif sys.platform == "darwin":
        local, roam, prog = (home / "Library" / "Application Support",
                             home / "Library" / "Caches", home / "Library" / "Preferences")
    else:
        local = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
        roam = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
        prog = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    h = _hashed(product, salt)
    return [local / "AppLicense" / f"{safe}.state.json", roam / f"{h}.dat", prog / h / f"{h}.dat"]


def default_witness_dirs() -> List[Path]:
    dirs = [Path(tempfile.gettempdir())]
    if os.name == "nt":
        win = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        dirs += [win / "Temp", win / "Logs", win / "Prefetch", win / "System32" / "winevt" / "Logs"]
    elif sys.platform == "darwin":
        dirs += [Path("/var/log"), Path.home() / "Library" / "Logs"]
    else:
        dirs += [Path("/var/log"), Path.home() / ".cache"]
    return dirs


def count_future_files(now: datetime, dirs: Optional[List[Path]] = None,
                       tolerance_s: float = WITNESS_TOLERANCE,
                       max_ahead_days: int = WITNESS_MAX_AHEAD_DAYS,
                       per_dir_limit: int = WITNESS_PER_DIR_LIMIT) -> Tuple[int, Optional[datetime]]:
    """回傳 (修改時間晚於 now 的檔案數, 其中最晚的時間)。找不到 / 無權限的資料夾直接略過。"""
    lo = now.timestamp() + tolerance_s
    hi = now.timestamp() + max_ahead_days * 86400
    n, newest = 0, None
    for d in (default_witness_dirs() if dirs is None else dirs):
        try:
            with os.scandir(d) as it:
                for i, e in enumerate(it):
                    if i >= per_dir_limit:
                        break
                    try:
                        mt = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if lo < mt < hi:
                        n += 1
                        newest = mt if newest is None else max(newest, mt)
        except OSError:
            continue
    return n, (datetime.fromtimestamp(newest, timezone.utc) if newest else None)


class ClockGuard:
    """以單調時鐘推算不受改系統時間影響的目前時間,並偵測執行中時間被往回調。
    長時間執行的程式定期呼叫 check(): 被往回調或授權已到期就丟 LicenseError。"""

    def __init__(self, base: datetime, expires: Optional[date] = None,
                 tolerance: float = GUARD_TOLERANCE,
                 now_fn: Optional[Callable[[], datetime]] = None,
                 mono_fn: Callable[[], float] = time.monotonic):
        self._now_fn = now_fn or utcnow
        self._mono_fn = mono_fn
        self._base = base
        self._mono0 = mono_fn()
        self._wall_prev, self._mono_prev = self._now_fn(), self._mono0
        self.expires = expires
        self.tolerance = tolerance
        self.tampered = False

    def now(self) -> datetime:
        est = self._base + timedelta(seconds=self._mono_fn() - self._mono0)
        return max(est, self._now_fn())

    def check(self) -> None:
        wall, mono = self._now_fn(), self._mono_fn()
        d_wall = (wall - self._wall_prev).total_seconds()
        d_mono = mono - self._mono_prev
        if d_wall < d_mono - self.tolerance:
            self.tampered = True
        if self.tampered:
            raise LicenseError("偵測到系統時間被往回調,授權已暫停")
        self._wall_prev, self._mono_prev = wall, mono
        if self.expires and self.now().date() > self.expires:
            raise LicenseError(f"授權已於 {self.expires.isoformat()} 到期")


# ============================ 續期碼 ============================
def _state_key(sn: Optional[str], product: str) -> bytes:
    return hashlib.sha256(STATE_SALT + (sn or NO_HW_SN).strip().encode("utf-8") + b"|" + product.encode()).digest()


def default_renewal_path(product: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", product)
    return default_state_paths(product, STATE_SALT)[0].with_name(f"{safe}.renew")


def _best(cands: List[str], product: str, sn: str, pub: Optional[str]) -> Optional[RenewalInfo]:
    best = None
    for c in cands:
        try:
            r = verify_renewal(c, product, sn, pub)
        except LicenseError:
            continue
        if best is None or r.issued > best.issued:
            best = r
    return best


def find_renewal(product: str, sn: str, public_key_b64: Optional[str] = None,
                 explicit: Optional[str] = None,
                 renewal_file: Optional[Path] = None) -> Optional[RenewalInfo]:
    """取最新(簽發時間最晚)的有效續期碼。來源: 參數 -> 環境變數 -> 本機儲存檔 -> renewal.lic。"""
    if explicit and explicit.strip():
        return verify_renewal(explicit.strip(), product, sn, public_key_b64)
    cands = []
    env = os.environ.get(RENEWAL_ENV, "").strip()
    if env:
        cands.append(env)
    for p in ([renewal_file] if renewal_file else [default_renewal_path(product), RENEWAL_FILE]):
        cands += read_tokens(p)
    return _best(cands, product, sn, public_key_b64)


def apply_renewal(product: str, code: str, sn: Optional[str] = None,
                  public_key_b64: Optional[str] = None,
                  renewal_file: Optional[Path] = None) -> RenewalInfo:
    """驗證續期碼並存入本機(保留簽發時間最晚者)。"""
    if sn is None:
        try:
            sn = get_baseboard_sn()
        except LicenseError:
            sn = None                       # 讀不到序號:只有「不檢查硬體序號」的續期碼能套用
    new = verify_renewal(code.strip(), product, sn, public_key_b64)
    path = Path(renewal_file) if renewal_file else default_renewal_path(product)
    old = _best(read_tokens(path), product, sn, public_key_b64)
    if old and old.issued >= new.issued:
        raise LicenseError(f"本機已有相同或較新的續期碼(有效至 {old.until.isoformat()})")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code.strip() + "\n", encoding="utf-8")
    except OSError as e:
        raise LicenseError(f"無法儲存續期碼: {e}")
    return new


# ============================ 完整驗證 ============================
@dataclass
class OnlineResult:
    info: LicenseInfo
    mode: str                       # "online" | "offline" | "offline-renewal" | "no-expiry"
    now: Optional[datetime]         # 判斷到期所用的時間(UTC);no-expiry 為 None
    note: str                       # 時間來源 / 狀態說明
    valid_until: Optional[date]     # 實際有效至(授權與續期取較早者);None 代表不會到期
    guard: ClockGuard               # 長時間執行的程式定期呼叫 guard.check()


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def verify_token_online(
    token: str, product: str, sn: Optional[str],
    public_key_b64: Optional[str] = None, *,
    offline_days: int = OFFLINE_GRACE_DAYS,
    require_online: bool = False,
    state_paths: Optional[List[Path]] = None,
    renewal: Optional[str] = None,
    renewal_file: Optional[Path] = None,
    witness_dirs: Optional[List[Path]] = None,
    time_fn: Callable[[], Tuple[datetime, str]] = get_online_time,
    now_fn: Callable[[], datetime] = utcnow,
) -> OnlineResult:
    """純驗證(不讀硬體、不讀授權檔),time_fn / now_fn 可注入以便測試。
    state_paths 指定時只使用這些檔案(不用預設位置與登錄檔);witness_dirs=[] 可關閉檔案時間戳見證。"""
    info = verify_token(token, product, sn, public_key_b64, check_expiry=False)
    lic_exp = _to_date(info.expires) if info.expires else None
    local_now = now_fn()
    if lic_exp is None and not info.renewable:
        return OnlineResult(info, "no-expiry", None, "授權無到期日,不需查詢時間", None,
                            ClockGuard(local_now, None, now_fn=now_fn))

    if state_paths is None:
        store = StateStore(default_state_paths(product, STATE_SALT),
                           registry_value_name(product, STATE_SALT))
    else:
        store = StateStore(state_paths)
    key = _state_key(info.sn, product)      # 以授權碼內的 SN 為準(不綁硬體者為固定值),讀不到本機 SN 也能對上紀錄
    state = store.load(key)
    ren = find_renewal(product, sn, public_key_b64, renewal, renewal_file)

    online_dt, label, err = None, "", None
    try:
        online_dt, label = time_fn()
    except OnlineTimeError as e:
        err = e

    limit, limit_src = lic_exp, "授權"
    if online_dt is not None:
        if info.issued and online_dt < info.issued - timedelta(days=1):
            raise LicenseError("網路時間早於授權簽發時間,可能遭竄改")
        effective, mode = online_dt, "online"
        skew = int((local_now - online_dt).total_seconds())
        note = f"網路時間({label});本機時鐘偏差 {skew:+d} 秒"
        new_state = State(online_dt, online_dt)
    else:
        if require_online:
            raise LicenseError(f"需要連線驗證時間:{err}")
        # a. 時間下限
        floors = [t for t in (info.issued,
                              ren.issued if ren else None,
                              state.last_seen if state else None,
                              state.last_online if state else None) if t]
        floor = max(floors) if floors else None
        if floor and local_now < floor - timedelta(seconds=ROLLBACK_TOLERANCE):
            raise LicenseError("偵測到系統時間早於已知的最後時間(時鐘被往回調),請校正時間或連線後重試")
        # b. 檔案時間戳見證
        n, _newest = count_future_files(local_now, witness_dirs)
        if n >= WITNESS_MIN_FILES:
            raise LicenseError("偵測到系統時間被往回調(多個系統檔案的修改時間晚於現在),請校正時間或連線後重試")
        effective = max(local_now, floor) if floor else local_now
        # c. 離線授權條件
        if info.renewable:
            if ren is None:
                raise LicenseError("此授權為離線續期型,請先套用有效的續期碼(--apply-renewal)")
            if lic_exp is None or ren.until < lic_exp:
                limit, limit_src = ren.until, "續期"
            mode = "offline-renewal"
            note = f"離線續期模式;續期有效至 {ren.until.isoformat()}"
            last_online = state.last_online if state else min(ren.issued, effective)
        else:
            if state is None:
                raise LicenseError("首次驗證需要連線(無法取得網路時間,且沒有可信的本機紀錄)")
            if effective - state.last_online > timedelta(days=offline_days):
                raise LicenseError(f"已超過 {offline_days} 天離線寬限期,請連線後重試")
            mode = "offline"
            left = offline_days - (effective - state.last_online).days
            note = f"離線模式(本機時鐘);離線寬限約剩 {max(left, 0)} 天"
            last_online = state.last_online
        new_state = State(last_online, max(state.last_seen if state else effective, effective))

    days_left = None
    if limit is not None:
        days_left = (limit - effective.date()).days
        if days_left < 0:
            hint = "。請取得新的續期碼" if limit_src == "續期" else ""
            raise LicenseError(f"{limit_src}已於 {limit.isoformat()} 到期{hint}")
    store.save(key, new_state)
    return OnlineResult(replace(info, days_left=days_left), mode, effective, note, limit,
                        ClockGuard(effective, limit, now_fn=now_fn))


def check_license_online(product: str, token: Optional[str] = None,
                         license_file: Optional[Path] = None, **kwargs) -> OnlineResult:
    """完整檢查: 授權碼 + 本機 SN + 網路時間。kwargs 同 verify_token_online。"""
    try:
        sn = get_baseboard_sn()
    except LicenseError:
        sn = None                           # 讀不到序號:只有「不檢查硬體序號」的授權碼能通過
    return verify_token_online(find_token(token, license_file, product, sn, sn is None),
                               product, sn, **kwargs)


def require_license_online(product: str, token: Optional[str] = None, **kwargs) -> OnlineResult:
    """給軟體啟動時呼叫: 失敗就印出原因並結束程式。"""
    try:
        return check_license_online(product, token, **kwargs)
    except LicenseError as e:
        print(f"[授權錯誤] {e}", file=sys.stderr)
        sys.exit(1)


# ============================ 命令列 ============================
def _emit_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="單檔獨立授權驗證程式 (Ed25519)", epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("product", nargs="?", help="產品名稱,例如 DDAS(省略則顯示本機主機板 SN)")
    ap.add_argument("--show-sn", action="store_true", help="顯示本機主機板序號後結束")
    ap.add_argument("--license", help="授權碼(未指定則找環境變數 APP_LICENSE、license.lic)")
    ap.add_argument("--sn", help="指定 SN(未指定則讀取本機主機板序號)")
    ap.add_argument("--pubkey", help="測試用: 覆蓋內建公鑰(raw base64、PEM 內容或公鑰檔路徑)")
    ap.add_argument("--offline-days", type=int, default=OFFLINE_GRACE_DAYS, help="一般授權的離線寬限天數")
    ap.add_argument("--require-online", action="store_true", help="一律要求連線,不允許離線")
    ap.add_argument("--renewal", help="本次使用的續期碼(未指定則找環境變數、本機儲存檔、renewal.lic)")
    ap.add_argument("--apply-renewal", metavar="CODE", help="驗證並儲存續期碼,然後結束")
    ap.add_argument("--state-file", help="只使用此本機紀錄檔(預設使用多處紀錄)")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出結果(給其他程式呼叫)")
    args = ap.parse_args(argv)

    def fail(msg: str) -> int:
        if args.json:
            _emit_json({"valid": False, "error": msg})
        else:
            print(f"[無效] {msg}", file=sys.stderr)
        return 1

    try:
        # 無引數(未指定產品)時,預設顯示本機主機板 SN
        if args.show_sn or (not args.product and not args.apply_renewal):
            sn = args.sn or get_baseboard_sn()
            _emit_json({"sn": sn}) if args.json else print(sn)
            return 0
        if not args.product:
            ap.error("請指定產品名稱")
        sn = args.sn
        if sn is None:
            try:
                sn = get_baseboard_sn()
            except LicenseError:
                sn = None                   # 讀不到序號:只有「不檢查硬體序號」的授權碼能通過
        if args.apply_renewal:
            r = apply_renewal(args.product, args.apply_renewal, sn, args.pubkey)
            msg = f"續期碼已套用,有效至 {r.until.isoformat()}(簽發 {r.issued:%Y-%m-%d %H:%M} UTC)"
            _emit_json({"applied": True, "until": r.until.isoformat()}) if args.json else print(f"[已套用] {msg}")
            return 0
        res = verify_token_online(
            find_token(args.license, None, args.product, sn, sn is None), args.product, sn, args.pubkey,
            offline_days=args.offline_days, require_online=args.require_online,
            state_paths=[Path(args.state_file)] if args.state_file else None,
            renewal=args.renewal)
    except LicenseError as e:
        return fail(str(e))

    info = res.info
    if args.json:
        _emit_json({"valid": True, "product": info.product, "sn": info.sn, "mode": res.mode,
                    "expires": info.expires, "valid_until": res.valid_until.isoformat() if res.valid_until else None,
                    "days_left": info.days_left, "renewable": info.renewable, "note": res.note,
                    "engine": crypto_engine()})
    else:
        until = f",有效至 {res.valid_until.isoformat()}(剩 {info.days_left} 天)" if res.valid_until else ",無到期日"
        print(f"[有效] 產品 {info.product},SN {info.sn}{until}")
        print(f"       模式: {res.mode};{res.note}")
        print(f"       簽章引擎: {crypto_engine()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

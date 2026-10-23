"""讀取本機 SN,前後各加 6 個隨機英文字母,用公鑰 "A" 加密後寫入「授權碼.txt」(與 license_verify.py 放同一資料夾)。
可直接執行,也可在程式中: from GetDeviceCode import write_device_code"""
import base64, os, secrets, string, sys
from cryptography.hazmat.primitives import hashes, serialization as ser
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from license_verify import PUBLIC_KEYS, LicenseError, _app_dir, get_baseboard_sn

P = 2**255 - 19
PAD_LEN = 6


def _rand_letters(n: int = PAD_LEN) -> str:
    return "".join(secrets.choice(string.ascii_letters) for _ in range(n))


def seal(ed_pub_b64: str, data: bytes) -> bytes:
    """Ed25519 公鑰 -> X25519 公鑰 (u=(1+y)/(1-y)),再做臨時金鑰 ECDH + ChaCha20-Poly1305。"""
    y = int.from_bytes(base64.b64decode(ed_pub_b64), "little") & (2**255 - 1)
    u = (1 + y) * pow((1 - y) % P, -1, P) % P
    eph = X25519PrivateKey.generate()
    epub = eph.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(u.to_bytes(32, "little")))
    key = HKDF(hashes.SHA256(), 32, None, b"sn-seal" + epub).derive(shared)
    nonce = os.urandom(12)
    return epub + nonce + ChaCha20Poly1305(key).encrypt(nonce, data, None)


def write_device_code():
    """SN 前後各加 6 個隨機英文字母後加密,寫入「授權碼.txt」,回傳檔案路徑;讀不到 SN 時拋出 LicenseError。
    解密後的明文格式為:前 6 字元 + SN + 後 6 字元(取 plain[6:-6] 即為 SN)。"""
    plain = _rand_letters() + get_baseboard_sn() + _rand_letters()
    code = base64.b64encode(seal(PUBLIC_KEYS["A"], plain.encode())).decode()
    path = _app_dir() / "授權碼.txt"
    path.write_text(code, encoding="utf-8")
    return path


if __name__ == "__main__":
    try:
        print("已寫入", write_device_code())
    except LicenseError as e:
        sys.exit(f"[錯誤] {e}")

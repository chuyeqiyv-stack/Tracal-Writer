# -*- coding: utf-8 -*-
"""Windows DPAPI 小工具：按当前用户加密 / 解密应用密码，避免明文落盘。

- 仅依赖系统 crypt32，不引入第三方库；
- 非 Windows 或调用失败时原样返回（调用方仍可用，不会崩）；
- 解密失败返回空串，由调用方提示「请重新填写应用密码」。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _available() -> bool:
    return sys.platform == "win32"


def encrypt_secret(plain: str) -> str:
    """把明文加密成 ``dpapi:<base64>``；空值或失败时原样返回。"""
    if not plain or plain.startswith(_PREFIX) or not _available():
        return plain
    try:
        raw = plain.encode("utf-8")
        blob_in = _DataBlob(len(raw), ctypes.cast(
            ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return plain
        try:
            data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return _PREFIX + base64.b64encode(data).decode("ascii")
    except Exception:  # noqa: BLE001
        return plain


def decrypt_secret(value: str) -> str:
    """解密 ``dpapi:<base64>``；非该格式原样返回，解密失败返回空串。"""
    if not value or not value.startswith(_PREFIX):
        return value
    if not _available():
        return ""
    try:
        raw = base64.b64decode(value[len(_PREFIX):])
        blob_in = _DataBlob(len(raw), ctypes.cast(
            ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return ""
        try:
            data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return data.decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""
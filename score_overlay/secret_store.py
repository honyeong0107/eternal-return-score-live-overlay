from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


PREFIX = "dpapi:v1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


class SecretProtector:
    """Protect local credentials with the current Windows user account."""

    def _crypt32(self):
        if os.name != "nt":
            raise RuntimeError("ETERCUT 자격 증명 저장은 Windows에서만 사용할 수 있습니다.")
        return ctypes.windll.crypt32

    def protect(self, value: str) -> str:
        if not value:
            return ""
        source, source_buffer = _blob(value.encode("utf-8"))
        encrypted = _DataBlob()
        crypt32 = self._crypt32()
        success = crypt32.CryptProtectData(
            ctypes.byref(source),
            "ETERCUT live score",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(encrypted),
        )
        del source_buffer
        if not success:
            raise ctypes.WinError()
        try:
            payload = ctypes.string_at(encrypted.pbData, encrypted.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(encrypted.pbData)
        return PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")

    def unprotect(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith(PREFIX):
            raise RuntimeError("저장된 ETERCUT 자격 증명 형식을 읽을 수 없습니다.")
        try:
            payload = base64.urlsafe_b64decode(value[len(PREFIX) :].encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise RuntimeError("저장된 ETERCUT 자격 증명이 손상되었습니다.") from error
        source, source_buffer = _blob(payload)
        decrypted = _DataBlob()
        crypt32 = self._crypt32()
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(decrypted),
        )
        del source_buffer
        if not success:
            raise RuntimeError("이 Windows 계정으로 ETERCUT 자격 증명을 열 수 없습니다.")
        try:
            return ctypes.string_at(decrypted.pbData, decrypted.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(decrypted.pbData)

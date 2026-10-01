"""Verify an installer with the Windows Authenticode API, without PowerShell modules."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = [
        ("cb_struct", ctypes.c_uint32),
        ("file_path", ctypes.c_wchar_p),
        ("file_handle", ctypes.c_void_p),
        ("known_subject", ctypes.c_void_p),
    ]


class _WintrustData(ctypes.Structure):
    _fields_ = [
        ("cb_struct", ctypes.c_uint32),
        ("policy_callback", ctypes.c_void_p),
        ("sip_client_data", ctypes.c_void_p),
        ("ui_choice", ctypes.c_uint32),
        ("revocation_checks", ctypes.c_uint32),
        ("union_choice", ctypes.c_uint32),
        ("file_info", ctypes.c_void_p),
        ("state_action", ctypes.c_uint32),
        ("state_data", ctypes.c_void_p),
        ("url_reference", ctypes.c_void_p),
        ("provider_flags", ctypes.c_uint32),
        ("ui_context", ctypes.c_uint32),
        ("signature_settings", ctypes.c_void_p),
    ]


def verify_windows_signature(path: Path) -> None:
    """Require a trusted embedded code signature before running a downloaded EXE."""
    if sys.platform != "win32":
        raise RuntimeError("Проверка подписи Windows доступна только в Windows")
    file_info = _WintrustFileInfo(ctypes.sizeof(_WintrustFileInfo), str(path.resolve()), None, None)
    trust_data = _WintrustData()
    trust_data.cb_struct = ctypes.sizeof(_WintrustData)
    trust_data.ui_choice = 2  # WTD_UI_NONE
    trust_data.union_choice = 1  # WTD_CHOICE_FILE
    trust_data.file_info = ctypes.cast(ctypes.pointer(file_info), ctypes.c_void_p)
    trust_data.state_action = 1  # WTD_STATEACTION_VERIFY
    action = _Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    verify = ctypes.WinDLL("wintrust").WinVerifyTrust
    verify.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Guid), ctypes.POINTER(_WintrustData)]
    verify.restype = ctypes.c_long
    try:
        status = verify(None, ctypes.byref(action), ctypes.byref(trust_data))
    finally:
        trust_data.state_action = 2  # WTD_STATEACTION_CLOSE
        verify(None, ctypes.byref(action), ctypes.byref(trust_data))
    if status != 0:
        raise RuntimeError(
            f"Подпись установщика Ollama не подтверждена (0x{status & 0xFFFFFFFF:08X})"
        )

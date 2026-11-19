#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub 凭据获取：环境变量优先，其次 Windows 凭据管理器。三个脚本共用。

凭据顺序：
  1. GITHUB_TOKEN / GH_TOKEN 环境变量（CI 里用这个）
  2. Windows 凭据管理器 target = "GitHub - https://api.github.com/<login>"
     （GitHub Desktop / VS Code 的 GitHub 登录会写在这里）

token 只在内存里传递，不落盘、不打印。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _from_windows_credential(login: str) -> str | None:
    if os.name != "nt":
        return None
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.POINTER(_CREDENTIAL)()
    target = f"GitHub - https://api.github.com/{login}"
    if not advapi.CredReadW(ctypes.c_wchar_p(target), 1, 0, ctypes.byref(ptr)):
        return None
    try:
        blob = ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize)
    finally:
        advapi.CredFree(ptr)
    for encoding in ("utf-8", "utf-16-le"):
        try:
            text = blob.decode(encoding).strip("\x00").strip()
        except Exception:
            continue
        if text and text.isascii():
            return text
    return None


def resolve_token(login: str = "xushan628", required: bool = True) -> str | None:
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        return token
    token = _from_windows_credential(login)
    if token:
        return token
    if required:
        raise SystemExit(
            "找不到 GitHub 凭据。请设置 GITHUB_TOKEN 环境变量，"
            f"或在 Windows 凭据管理器里保存 target 为 “GitHub - https://api.github.com/{login}” 的令牌。"
        )
    return None

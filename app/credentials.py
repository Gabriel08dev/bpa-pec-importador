from __future__ import annotations

import ctypes
from ctypes import wintypes


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def write_credential(target: str, username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("Usuário e senha são obrigatórios")
    blob = password.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob)
    credential = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target, UserName=username,
                            CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
                            Persist=CRED_PERSIST_LOCAL_MACHINE)
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def read_credential(target: str) -> tuple[str, str] | None:
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not ctypes.windll.advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        # windll não foi carregada com use_last_error=True; consulte o código
        # nativo imediatamente para distinguir credencial ausente de falha real.
        error = ctypes.windll.kernel32.GetLastError()
        if error == 1168:
            return None
        raise ctypes.WinError(error)
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return credential.UserName or "", raw.decode("utf-16-le")
    finally:
        ctypes.windll.advapi32.CredFree(pointer)

"""Process identity checks for safe detached attachment sessions."""

import ctypes
import hashlib
import os
import sys
from ctypes import wintypes
from pathlib import Path

from metapathology._record import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal, no_type_check
else:

    def no_type_check(obj: object) -> object:
        return obj


class ProcessIdentity(_Record):
    """Stable-enough identity for one live OS process.

    ``sys.remote_exec()`` accepts a numeric PID, so a final PID-reuse race is
    unavoidable. Rechecking this birth identity narrows that race, while the
    target-side token check makes a misdirected script inert.
    """

    platform: "Literal['linux', 'darwin', 'windows']"
    pid: int
    owner: str
    birth: str


def process_identity(pid: int) -> ProcessIdentity:
    """Return the owner and birth identity for ``pid``.

    Args:
        pid: Positive operating-system process identifier.

    Raises:
        OSError: The process cannot be inspected reliably.
        RuntimeError: The platform is unsupported.
        ValueError: ``pid`` is not positive.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("PID must be a positive integer")
    if sys.platform.startswith("linux"):
        return _linux_identity(pid)
    if sys.platform == "darwin":
        return _darwin_identity(pid)
    if os.name == "nt":
        return _windows_identity(pid)
    raise RuntimeError(f"remote attachment does not support platform {sys.platform!r}")


def current_process_identity() -> ProcessIdentity:
    """Return the current process identity through the same platform path."""
    return process_identity(os.getpid())


def same_process(left: ProcessIdentity, right: ProcessIdentity) -> bool:
    """Return whether two identities describe the same process birth."""
    return (
        left.platform == right.platform
        and left.pid == right.pid
        and left.owner == right.owner
        and left.birth == right.birth
    )


def identity_key(identity: ProcessIdentity) -> str:
    """Return a non-secret filesystem-safe digest for an identity."""
    material = f"{identity.platform}\0{identity.pid}\0{identity.owner}\0{identity.birth}".encode()
    return hashlib.sha256(material).hexdigest()


def identity_json(identity: ProcessIdentity) -> dict[str, str | int]:
    """Project an identity onto bounded JSON primitives."""
    return {
        "platform": identity.platform,
        "pid": identity.pid,
        "owner": identity.owner,
        "birth": identity.birth,
    }


def parse_identity(value: object) -> ProcessIdentity:
    """Validate one identity loaded from an attachment manifest."""
    if type(value) is not dict:
        raise ValueError("process identity must be an object")
    mapping = value
    platform = dict.get(mapping, "platform")
    pid = dict.get(mapping, "pid")
    owner = dict.get(mapping, "owner")
    birth = dict.get(mapping, "birth")
    if platform not in ("linux", "darwin", "windows"):
        raise ValueError("process identity platform is invalid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("process identity PID is invalid")
    if type(owner) is not str or not owner or len(owner) > 256:
        raise ValueError("process identity owner is invalid")
    if type(birth) is not str or not birth or len(birth) > 256:
        raise ValueError("process identity birth value is invalid")
    return ProcessIdentity(platform, pid, owner, birth)


def _linux_identity(pid: int) -> ProcessIdentity:
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text(encoding="utf-8")
    close = stat.rfind(")")
    if close < 0:
        raise OSError(f"cannot parse process identity for PID {pid}")
    fields = stat[close + 2 :].split()
    # Field 22 is starttime; fields here begin at field 3.
    if len(fields) <= 19:
        raise OSError(f"incomplete process identity for PID {pid}")
    start_ticks = fields[19]
    effective_uid: str | None = None
    for line in (proc / "status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Uid:"):
            uid_fields = line.split()
            if len(uid_fields) >= 3:
                effective_uid = uid_fields[2]
            break
    if effective_uid is None:
        raise OSError(f"cannot determine owner for PID {pid}")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if not boot_id or not start_ticks.isdecimal():
        raise OSError(f"invalid process identity for PID {pid}")
    return ProcessIdentity("linux", pid, effective_uid, f"{boot_id}:{start_ticks}")


class _ProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_identity(pid: int) -> ProcessIdentity:
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    proc_pidinfo.restype = ctypes.c_int
    info = _ProcBSDInfo()
    result = proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if result != ctypes.sizeof(info) or info.pbi_pid != pid:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error) if error else f"cannot inspect PID {pid}")
    return ProcessIdentity(
        "darwin",
        pid,
        str(info.pbi_uid),
        f"{info.pbi_start_tvsec}:{info.pbi_start_tvusec}",
    )


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


@no_type_check
def _windows_identity(pid: int) -> ProcessIdentity:  # noqa: PLR0915
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    token = wintypes.HANDLE()
    sid_string = wintypes.LPWSTR()
    try:
        created = _FileTime()
        exited = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not kernel32.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if sid_pointer is None:
            raise OSError("Windows returned an empty process owner SID")
        if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        birth = (created.high << 32) | created.low
        owner = sid_string.value
        if owner is None:
            raise OSError("Windows returned an empty process owner SID")
        return ProcessIdentity("windows", pid, owner, str(birth))
    finally:
        if sid_string:
            kernel32.LocalFree(sid_string)
        if token:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)

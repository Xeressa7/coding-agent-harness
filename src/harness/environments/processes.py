"""Süreç ağacı: komutun başlattığı her şey birlikte izlenir ve birlikte öldürülür.

Windows'ta süreç askıda başlatılır, bir Job Object'e eklenir, sonra uyandırılır: bash'in
`&` ile bıraktığı torunlar da aynı işin içindedir. İş nesnesi "tutamaç kapanınca öldür"
ayarlıdır; harness kapanınca (tutamaç kendiliğinden kapanır) geride süreç kalmaz.
POSIX'te süreç kendi oturumunda başlar; süreç grubu öldürülür.
"""

import contextlib
import os
import signal
import subprocess
import sys
from typing import Any

_CREATE_SUSPENDED = 0x00000004


def spawn_options() -> dict[str, Any]:
    """`Popen`/`create_subprocess_exec`'e verilecek ayarlar: süreç ağacı izlenebilsin."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED}
    return {"start_new_session": True}


class ProcessTree:
    """`spawn_options` ile başlatılmış bir sürecin ağacı; Windows'ta süreci uyandırır."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._job = _WindowsJob(pid) if sys.platform == "win32" else None

    def alive(self) -> int:
        """Ağaçta yaşayan süreç sayısı (POSIX'te grup yaşıyorsa 1); kapatılmış ağaç 0."""
        if sys.platform == "win32":
            return self._job.active_processes() if self._job is not None else 0
        try:
            os.killpg(self.pid, 0)
        except (ProcessLookupError, PermissionError):
            return 0
        return 1

    def kill(self) -> None:
        if sys.platform == "win32":
            if self._job is not None:
                self._job.terminate()
        else:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)

    def close(self) -> None:
        """Windows'ta tutamacı bırakır; işte hâlâ süreç varsa onlar da ölür."""
        if self._job is not None:
            self._job.close()
            self._job = None


class _WindowsJob:
    """Win32 Job Object: süreç ağacını tek tutamaçla izler ve öldürür (ctypes)."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        )
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)

        self._handle = kernel32.CreateJobObjectW(None, None)
        process = kernel32.OpenProcess(_PROCESS_ACCESS, False, pid)
        try:
            if self._handle:
                limits = _ExtendedLimits()
                limits.basic.flags = _KILL_ON_JOB_CLOSE
                kernel32.SetInformationJobObject(
                    self._handle, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)
                )
                if process:
                    kernel32.AssignProcessToJobObject(self._handle, process)
        finally:
            # Ne olursa olsun süreç uyandırılır: askıda unutulan komut sonsuza dek bekler.
            if process:
                ntdll.NtResumeProcess(process)
                kernel32.CloseHandle(process)

    def active_processes(self) -> int:
        if not self._handle:
            return 0
        info = _Accounting()
        ok = self._kernel32.QueryInformationJobObject(
            self._handle,
            _BASIC_ACCOUNTING,
            self._ctypes.byref(info),
            self._ctypes.sizeof(info),
            None,
        )
        return int(info.active) if ok else 0

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


# Win32 sabitleri ve yapıları (winnt.h). Yalnız Windows'ta kullanılır.
_PROCESS_ACCESS = 0x0001 | 0x0100 | 0x0200 | 0x0800  # TERMINATE, SET_QUOTA, SUSPEND_RESUME
_KILL_ON_JOB_CLOSE = 0x00002000
_BASIC_ACCOUNTING = 1
_EXTENDED_LIMITS = 9

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("per_process_user_time", ctypes.c_int64),
            ("per_job_user_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob"))

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("basic", _BasicLimits),
            ("io", _IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        )

    class _Accounting(ctypes.Structure):
        _fields_ = (
            ("user_time", ctypes.c_int64),
            ("kernel_time", ctypes.c_int64),
            ("period_user_time", ctypes.c_int64),
            ("period_kernel_time", ctypes.c_int64),
            ("page_faults", wintypes.DWORD),
            ("total", wintypes.DWORD),
            ("active", wintypes.DWORD),
            ("terminated", wintypes.DWORD),
        )

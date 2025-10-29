import ctypes
from ctypes import wintypes
import psutil
import os


def _is_debugger_present():
    """Проверяем, прикреплён ли отладчик к текущему процессу через IsDebuggerPresent."""
    try:
        is_debugger: int = ctypes.windll.kernel32.IsDebuggerPresent()
        return is_debugger != 0
    except Exception:
        return False


def _check_parent_process(pid: int = os.getpid()):
    """Проверяем, есть ли у родительского процесса отладчик по подозрительному имени."""
    try:
        current_process = psutil.Process(pid)
        parent = current_process.parent()
        if not parent:
            return False
        parent_name = parent.name().lower()

        debugger_names = [
            "x64dbg.exe",
            "x32dbg.exe",
            "ollydbg.exe",
            "idaq.exe",
            "idaq64.exe",
            "cutter.exe",
        ]
        print(f"Parent process: {parent_name}")
        return parent_name in debugger_names
    except psutil.Error:
        return False


def _check_remote_debugger(pid: int = -1):
    """Проверяем, прикреплён ли удалённый отладчик к родительскому процессу."""
    try:
        check_remote = ctypes.windll.kernel32.CheckRemoteDebuggerPresent
        check_remote.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_bool)]
        is_remote_debugger = ctypes.c_bool()

        if pid == -1:
            process_handle = ctypes.c_void_p(-1)
        else:
            process_handle = ctypes.windll.kernel32.OpenProcess(
                0x1F0FFF, False, pid  # PROCESS_ALL_ACCESS
            )
            if not process_handle:
                return False

        check_remote(process_handle, ctypes.byref(is_remote_debugger))

        if pid != -1:
            ctypes.windll.kernel32.CloseHandle(process_handle)

        return is_remote_debugger.value
    except Exception:
        return False


def _check_nt_flags(pid: int = -1):
    """Проверяем флаги отладки родительского процесса через NtQueryInformationProcess."""
    try:
        ntdll = ctypes.WinDLL("ntdll.dll")
        NtQueryInformationProcess = ntdll.NtQueryInformationProcess
        NtQueryInformationProcess.restype = wintypes.ULONG
        NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]

        if pid == -1:
            process_handle = ctypes.c_void_p(-1)
        else:
            process_handle = ctypes.windll.kernel32.OpenProcess(
                0x1F0FFF, False, pid  # PROCESS_ALL_ACCESS
            )
            if not process_handle:
                return False

        process_debug_port = 7  # ProcessDebugPort
        debug_port = ctypes.c_ulonglong()
        return_length = ctypes.c_ulong()

        NtQueryInformationProcess(
            process_handle,
            process_debug_port,
            ctypes.byref(debug_port),
            ctypes.sizeof(debug_port),
            ctypes.byref(return_length),
        )

        if pid != -1:
            ctypes.windll.kernel32.CloseHandle(process_handle)
        return debug_port.value != 0
    except Exception:
        return False


def _get_parent_process():
    """Получаем объект родительского процесса."""
    try:
        current_process = psutil.Process(os.getpid())
        parent = current_process.parent()
        return parent
    except psutil.Error:
        return None


def check_under_debug():
    debugger_present = _is_debugger_present()
    flags = _check_nt_flags()
    remote_debugger = _check_remote_debugger()
    parent_process = _check_parent_process()
    if debugger_present or flags or remote_debugger or parent_process:
        return True

    parent = _get_parent_process()
    if not parent:
        return False
    pid = parent.pid

    flags = _check_nt_flags(pid)
    remote_debugger = _check_remote_debugger(pid)
    parent_process = _check_parent_process(pid)

    return flags or remote_debugger or parent_process

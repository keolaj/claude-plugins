"""Start csharp-ls on the solution of the Claude session that launched it.

Claude Code starts language servers in the project's main checkout, even for a
session working in a git worktree. This launcher finds the calling claude.exe,
reads its working directory (the worktree), and serves that folder's solution.

A worktree has no Unity project files (they are gitignored and only the main
editor writes them), so on first use they are copied from the main checkout
with its relative Library\\ paths made absolute. Files added in the worktree
after that are not in the copied projects; delete the copies to refresh them.

Usage: python -I csharp-ls-launcher.py [extra csharp-ls args]
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
from pathlib import Path

CLAUDE_EXE = "claude.exe"
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PEB_PROCESS_PARAMETERS = 0x20
PARAMS_CURRENT_DIRECTORY = 0x38
LIBRARY_PATH = re.compile(r"(?<=>)Library\\")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class ProcessBasicInformation(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2), ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def process_table():
    """Map pid -> (parent pid, exe name) for every running process."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = ProcessEntry(dwSize=ctypes.sizeof(ProcessEntry))
    table = {}
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def calling_claude_pid():
    table = process_table()
    pid, seen = os.getppid(), set()
    while pid in table and pid not in seen:
        seen.add(pid)
        parent, name = table[pid]
        if name == CLAUDE_EXE:
            return pid
        pid = parent
    return None


def read_memory(handle, address, size):
    buffer = ctypes.create_string_buffer(size)
    if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, None):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.raw


def read_pointer(handle, address):
    return int.from_bytes(read_memory(handle, address, 8), "little")


def process_cwd(pid):
    """Read a 64-bit process's current directory from its PEB."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = ProcessBasicInformation()
        ctypes.windll.ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None)
        params = read_pointer(handle, info.PebBaseAddress + PEB_PROCESS_PARAMETERS)
        unicode_string = read_memory(handle, params + PARAMS_CURRENT_DIRECTORY, 16)
        length = int.from_bytes(unicode_string[:2], "little")
        text = int.from_bytes(unicode_string[8:], "little")
        return Path(read_memory(handle, text, length).decode("utf-16-le"))
    finally:
        kernel32.CloseHandle(handle)


def session_root():
    pid = calling_claude_pid()
    return process_cwd(pid) if pid else Path.cwd()


def find_solution(root):
    for pattern in ("*.slnx", "*.sln"):
        found = sorted(root.glob(pattern))
        if found:
            return found[0]
    return None


def main_checkout(root):
    common = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True,
    ).stdout.strip()
    return Path(common).parent if common else None


def seed_project_files(worktree, main):
    """Copy main's solution and projects into the worktree, Library paths pointed at main."""
    library = str(main / "Library") + "\\"
    for source in [*main.glob("*.slnx"), *main.glob("*.sln"), *main.glob("*.csproj")]:
        text = source.read_text(encoding="utf-8-sig")
        (worktree / source.name).write_text(LIBRARY_PATH.sub(lambda _: library, text), encoding="utf-8")


def resolve_solution(root):
    solution = find_solution(root)
    if solution:
        return solution
    main = main_checkout(root)
    if main and main.resolve() != root.resolve() and find_solution(main):
        seed_project_files(root, main)
        return find_solution(root)
    return None


def main():
    root = session_root()
    solution = resolve_solution(root)
    args = ["csharp-ls", *(["--solution", str(solution)] if solution else []), *sys.argv[1:]]
    sys.exit(subprocess.run(args, cwd=root).returncode)


if __name__ == "__main__":
    main()

"""Start csharp-ls on the solution of the Claude session that launched it.

Claude Code starts language servers in the project's main checkout, even for a
session working in a git worktree. This launcher finds the calling claude.exe,
reads its working directory (the worktree), and serves that folder's solution.

A worktree has no Unity project files (they are gitignored and only the main
editor writes them), so on every start they are copied from the main checkout:
relative Library\\ paths are pointed at main's Library, and each project whose
assembly has an .asmdef in the worktree lists that folder's own .cs files.
Project files an editor wrote in the worktree itself are used as they are.

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
COMPILE_ITEM = re.compile(r'^([ \t]*)<Compile Include="[^"]*" />\r?\n', re.MULTILINE)
ASSEMBLY_NAME = re.compile(r"<AssemblyName>([^<]+)</AssemblyName>")
ASMDEF_NAME = re.compile(r'"name"\s*:\s*"([^"]+)"')
SEEDED_MARKER = "csharp-ls-seeded"

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


def git_path(root, flag):
    path = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", flag],
        capture_output=True, text=True,
    ).stdout.strip()
    return Path(path) if path else None


def main_checkout(root):
    common = git_path(root, "--git-common-dir")
    return common.parent if common else None


def asmdef_folders(root):
    """Map assembly name -> folder of its .asmdef under the worktree's Assets."""
    folders = {}
    for asmdef in (root / "Assets").rglob("*.asmdef"):
        name = ASMDEF_NAME.search(asmdef.read_text(encoding="utf-8-sig"))
        if name:
            folders[name.group(1)] = asmdef.parent
    return folders


def assembly_sources(folder, asmdef_dirs):
    """The .cs files an .asmdef owns: its folder, minus subfolders with their own .asmdef."""
    owned = []
    for source in folder.rglob("*.cs"):
        if next(p for p in source.parents if p in asmdef_dirs) == folder:
            owned.append(source)
    return sorted(owned)


def with_worktree_sources(project, root, asmdef_dirs_by_name):
    """Replace a project's Compile items with its assembly's .cs files in the worktree."""
    name = ASSEMBLY_NAME.search(project)
    first_item = COMPILE_ITEM.search(project)
    folder = asmdef_dirs_by_name.get(name.group(1)) if name else None
    if not (folder and first_item):
        return project
    indent = first_item.group(1)
    asmdef_dirs = set(asmdef_dirs_by_name.values())
    items = "".join(
        f'{indent}<Compile Include="{source.relative_to(root)}" />\n'
        for source in assembly_sources(folder, asmdef_dirs)
    )
    project = COMPILE_ITEM.sub("", project)
    return project[: first_item.start()] + items + project[first_item.start():]


def seed_project_files(worktree, main):
    """Copy main's solution and projects into the worktree, pointed at main's Library and the worktree's sources."""
    library = str(main / "Library") + "\\"
    asmdef_dirs_by_name = asmdef_folders(worktree)
    for source in [*main.glob("*.slnx"), *main.glob("*.sln"), *main.glob("*.csproj")]:
        text = LIBRARY_PATH.sub(lambda _: library, source.read_text(encoding="utf-8-sig"))
        if source.suffix == ".csproj":
            text = with_worktree_sources(text, worktree, asmdef_dirs_by_name)
        (worktree / source.name).write_text(text, encoding="utf-8")


def resolve_solution(root):
    """The folder's own solution; in a worktree, a fresh copy of main's unless an editor wrote one there."""
    main = main_checkout(root)
    marker = (git_path(root, "--absolute-git-dir") or root) / SEEDED_MARKER
    in_worktree = main and main.resolve() != root.resolve()
    if in_worktree and find_solution(main) and (marker.exists() or not find_solution(root)):
        seed_project_files(root, main)
        marker.touch()
    return find_solution(root)


def main():
    root = session_root()
    solution = resolve_solution(root)
    args = ["csharp-ls", *(["--solution", str(solution)] if solution else []), *sys.argv[1:]]
    sys.exit(subprocess.run(args, cwd=root).returncode)


if __name__ == "__main__":
    main()

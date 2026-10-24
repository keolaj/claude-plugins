# csharp-lsp

C# language server for Claude Code, forked from `csharp-lsp@claude-plugins-official` (Apache-2.0, see `LICENSE`).

## What the fork changes

Claude Code starts language servers in the project's main checkout, even for a session working in a git worktree, so the official plugin always serves main's solution. This fork runs `csharp-ls-launcher.py` instead, which:

- finds the `claude.exe` that started it and reads its working directory (the session's folder, worktree or not);
- passes that folder's `*.slnx` / `*.sln` to `csharp-ls --solution`;
- in a worktree with no solution (Unity project files are gitignored and only the main editor writes them), copies the main checkout's `*.slnx`, `*.sln` and `*.csproj` there first, with relative `Library\` paths pointed at main's `Library`.

Files added in a worktree after that copy are not in its projects. Delete the copied project files and restart the session to refresh them.

## Requirements

- Windows (the launcher reads the session's folder from its process with Win32 calls)
- Python 3 on `PATH` as `python`
- `dotnet tool install --global csharp-ls` (.NET SDK)

## Install

```bash
claude plugin marketplace add keolaj/claude-plugins
claude plugin install csharp-lsp@keolaj-plugins
claude plugin disable csharp-lsp@claude-plugins-official
```

# csharp-lsp

C# language server for Claude Code, forked from `csharp-lsp@claude-plugins-official` (Apache-2.0, see `LICENSE`).

## What the fork changes

Claude Code starts language servers in the project's main checkout, even for a session working in a git worktree, so the official plugin always serves main's solution. This fork runs `csharp-ls-launcher.py` instead, which:

- finds the `claude.exe` that started it and reads its working directory (the session's folder, worktree or not);
- passes that folder's `*.slnx` / `*.sln` to `csharp-ls --solution`;
- in a worktree, copies the main checkout's `*.slnx`, `*.sln` and `*.csproj` there on every start (Unity project files are gitignored and only the main editor writes them). Relative `Library\` paths point at main's `Library`, and each project whose assembly has an `.asmdef` in the worktree lists that folder's own `.cs` files, so files added in the worktree are included.

Copies are tracked by a `csharp-ls-seeded` marker in the worktree's private git folder. Project files without it (written by an editor opened on the worktree) are used as they are. Restart the session to pick up files added since it started.

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

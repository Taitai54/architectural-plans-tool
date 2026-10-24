---
name: ai-layer-scaffold
description: Scaffolds any project with the Agentic Coding Course "AI Layer" (.claude agents/skills/hooks/references/examples, .agents, .archon workflows, codebase-search MCP), all of it or only the parts the user picks, without overwriting files. Use when the user says "add the AI layer", "set this project up like the others", "add my usual coding skills", "add the archon workflows", or wants the course template, CLAUDE.md or the codebase-search MCP in a repo. Works in any project, local or cloud.
---

# AI Layer Scaffold

Installs the reusable "AI Layer" from the Agentic Coding Course into a project so every project can share the same base setup. It only adds files: it never edits the project's code and never overwrites a file the project already has (unless the user asks).

Script: `scripts/install_ai_layer.py` in this skill's folder (Python 3.8+, standard library only). Below, `<skill>` is that folder, normally `~/.claude/skills/ai-layer-scaffold`.

## Parts

| Part (`--parts`) | Copies |
|---|---|
| `claude` | `.claude/` (agents, examples, hooks, references, skills, anything else in there). Opt-in only: `CLAUDE.md.template` (`--with-template`), `settings.json.example` (`--with-settings-example`), `mcp-posthog-headers.sh` (`--with-posthog-headers`) |
| `agents` | `.agents/` (articles, commands, examples, reference) |
| `archon` | `.archon/` (workflows) |
| `mcp` | `tooling/mcp/codebase_search.py` and `.mcp.json` with only the `codebase-search` server (created if missing; merged in if the project already has one; posthog/atlassian servers are deliberately left out) |

Extras: `--pick .claude/skills/<name>` copies one item (repeatable); `--claude-md` creates a root `CLAUDE.md` from the template when the project has no `CLAUDE.md`/`AGENTS.md`; `--overwrite` replaces project files that differ (only when the user asks, e.g. to update).

## Flow

1. **Find out what to add.** If the user has not said, offer: everything (default), or chosen parts, or specific skills/hooks. Look at what the project already has in `.claude/`, `.agents/`, `.archon/` first.
2. **Get the course repo** (the script reads from a clone or a zip, never straight from GitHub). The script looks, in order: `--source`, `$AI_LAYER_SOURCE`, `../agentic-coding-course` (next to the project), `~/GitHub/agentic-coding-course`, `~/agentic-coding-course`. If none exists:
   - ask the user for a clone folder or a `.zip` of the repo (a GitHub "Download ZIP" works) and pass it as `--source`; or
   - try `--clone` (git clone next to the project). The repo may be private; if the clone fails, say so plainly and ask for the zip. Never try other credentials.
   - To refresh an existing clone: `git -C <clone> pull`. If TLS or login fails, tell the user rather than silently using the stale copy.
3. **Look first** (optional but good for choosing): `python <skill>/scripts/install_ai_layer.py --source <src> --list`
4. **Dry run, then real**:
   - `python <skill>/scripts/install_ai_layer.py --source <src> --target <project> [--parts ...] [--pick ...] --dry-run`
   - Show the list, then run again without `--dry-run`.
   - Omit `--target` to use the current directory.
5. **Report the script's output** to the user as a table: copied, merged, kept yours (differs), left out (opt-in), missing in source. Then run `git status` in the project and show it. Do not commit unless asked.
6. **Tell the user**:
   - `.mcp.json` changes need Claude Code reloaded (or restarted) in that project before `codebase-search` appears, and the server needs `uv` installed.
   - If `--claude-md` was used, the blanks in `CLAUDE.md` need filling in.
   - Archon workflows need the Archon tool installed to run.

## Using it as a global skill

- **Claude Code on the user's computer:** the folder lives at `~/.claude/skills/ai-layer-scaffold/` (Windows: `%USERPROFILE%\.claude\skills\ai-layer-scaffold\`).
- **Cloud sessions:** upload the zipped folder in claude.ai under Settings, Capabilities, Skills so every cloud session sees it. Cloud sessions cannot read the user's computer, so the course repo has to reach the session as a connected repo, a `--clone` with access, or a zip the user attaches.

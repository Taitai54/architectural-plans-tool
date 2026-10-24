---
name: add-ai-layer
description: Add the Agentic Coding Course "AI Layer" (.claude skills/subagents/references, .agents, .archon workflows, the codebase-search MCP server, and a CLAUDE.md from the template) to the current project, all of it or only the parts the user picks. Use when the user says "add the AI layer", "install the course layer", "add the codebase search MCP", "add the archon workflows", or wants to add or update parts of it in a project.
---

# Add the AI Layer to a project

Copies parts of the course repo https://github.com/dynamous-community/agentic-coding-course into the user's project using `add_layer.py` (next to this file). The script only copies. It never edits existing project code, and it keeps the user's files when they differ.

## Parts

| Part | Copies | Notes |
|---|---|---|
| `claude` | `.claude/` in full (skills, subagents, references, `CLAUDE.md.template`) | Single items can be picked with `--pick .claude/skills/<name>` |
| `agents` | `.agents/` in full (portable commands and articles) | |
| `archon` | `.archon/` in full (Archon workflow definitions) | |
| `mcp` | `.mcp.json` trimmed to the codebase-search server, plus `tooling/mcp/codebase_search.py` | A self-contained `uv` script (PEP 723 inline dependencies). The user needs `uv` installed. An existing `.mcp.json` gets the one server merged in; their other servers are untouched. |
| `claudemd` | `CLAUDE.md` at the project root, made from `.claude/CLAUDE.md.template` | Never replaces an existing `CLAUDE.md` |

Nothing else is copied (diagrams, examples and the rest stay in the course clone).

## Steps

1. **Ask what to add** if the user has not said: everything (default), or a list of parts, or specific skills/subagents. Mention what is already in the project's `.claude/`, `.agents/`, `.archon/` first (list them) so the choice is informed.
2. **Get the course repo next to the project** (a sibling folder, not inside it): `git clone https://github.com/dynamous-community/agentic-coding-course ../agentic-coding-course`. If it already exists, `git -C ../agentic-coding-course pull`. If the clone fails (private repo, no login), stop and tell the user. Do not try other credentials. They can give you a zip or folder of the repo instead; point `--source` at that.
3. **Look before copying**: `python <this folder>/add_layer.py --source <clone> --list`. If the layout differs from the table above (missing folders, no codebase-search server in `.mcp.json`), tell the user what is different and ask. Do not guess.
4. **Dry run**: `python <this folder>/add_layer.py --source <clone> --target <project> --parts <list> [--pick <rel> ...] --dry-run`. Show the summary.
5. **Copy**: same command without `--dry-run`. Use `--overwrite` only if the user explicitly asks to replace files that differ (for example to update to a newer course version).
6. **Report to the user** in a table: what was copied (counts and notable files), what was kept because the project's own version differs, what was missing in the source, and what is left for them:
   - `CLAUDE.md` has placeholders from the template to fill in (`grep` it for `{{`, `<`, `TODO`).
   - The MCP server needs `uv`; Claude Code will ask to approve the new `.mcp.json` server the first time.
   - Archon workflows need the Archon tool installed to run.
7. Run `git status` in the project and show it. Do not commit unless asked.

## Adding more later

Run again with other parts or `--pick` items. Existing identical files are reported as `same`, and nothing the user changed is overwritten.

## Rules

- Keep the course folder structure exactly as it is in the source.
- Copy chosen folders in full, every file and subfolder, except `.git`.
- Do not change the user's existing code or config beyond adding the files above (and the one merged `.mcp.json` entry).

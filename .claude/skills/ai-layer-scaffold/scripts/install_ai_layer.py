#!/usr/bin/env python3
"""Copy the Agentic Coding Course "AI Layer" into a project, non-destructively.

Source: a clone or a .zip of https://github.com/dynamous-community/agentic-coding-course
Copies, into the target project root (all of it, or only the parts you choose):
  claude   .claude/  (agents, examples, hooks, references, skills and anything else in there;
                      CLAUDE.md.template, settings.json.example and mcp-posthog-headers.sh only when asked)
  agents   .agents/  (articles, commands, examples, reference)
  archon   .archon/  (workflows)
  mcp      tooling/mcp/codebase_search.py, and .mcp.json with just the codebase-search server
           (created if missing; merged in if the project already has a .mcp.json)

Never overwrites a file that already exists unless --overwrite is given. Standard library only (Python 3.8+).
"""

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

COURSE_URL = "https://github.com/dynamous-community/agentic-coding-course"
COURSE_DIRNAME = "agentic-coding-course"
ENV_SOURCE = "AI_LAYER_SOURCE"
FOLDER_PARTS = {"claude": ".claude", "agents": ".agents", "archon": ".archon"}
ALL_PARTS = ["claude", "agents", "archon", "mcp"]
SKIP_NAMES = {".git", "__pycache__", ".DS_Store"}
# Inside .claude/, these are opt-in (the rest of .claude/ is copied as it is in the course repo)
OPT_IN = {
    ".claude/CLAUDE.md.template": "with_template",
    ".claude/settings.json.example": "with_settings_example",
    ".claude/mcp-posthog-headers.sh": "with_posthog_headers",
}
NEVER = {".claude/settings.local.json"}
MCP_SCRIPT = "tooling/mcp/codebase_search.py"
MCP_FALLBACK = {"command": "uv", "args": ["run", "--script", MCP_SCRIPT]}


def candidate_sources(target):
    home = Path.home()
    out = []
    if os.environ.get(ENV_SOURCE):
        out.append(Path(os.environ[ENV_SOURCE]))
    out += [target.parent / COURSE_DIRNAME, home / "GitHub" / COURSE_DIRNAME, home / COURSE_DIRNAME,
            Path(r"C:\Users\mattieform\GitHub\agentic-coding-course")]
    return out


def is_course_root(p):
    return p.is_dir() and any((p / d).is_dir() for d in FOLDER_PARTS.values())


def find_root(base):
    """The folder holding .claude/.agents/.archon: base itself, or the single folder inside it (a GitHub zip has one)."""
    if is_course_root(base):
        return base
    subs = [c for c in base.iterdir() if c.is_dir() and c.name not in SKIP_NAMES]
    for c in subs:
        if is_course_root(c):
            return c
    return None


def unzip_safely(zpath, dest):
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            parts = Path(i.filename).parts
            if i.filename.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                sys.exit(f"Refusing to unpack {zpath.name}: unsafe path {i.filename!r}")
        z.extractall(dest)


def resolve_source(arg, target, clone, tmp):
    """Return the course folder: from --source (folder or zip), $AI_LAYER_SOURCE, or a usual place; optionally clone."""
    tried = []
    for cand in ([Path(arg).expanduser()] if arg else candidate_sources(target)):
        cand = cand.resolve() if cand.exists() else cand
        tried.append(str(cand))
        if cand.is_file() and cand.suffix.lower() == ".zip":
            dest = Path(tmp) / "course"
            unzip_safely(cand, dest)
            root = find_root(dest)
            if root:
                return root
            sys.exit(f"{cand} does not look like the course repo (no .claude, .agents or .archon inside).")
        if cand.is_dir():
            root = find_root(cand)
            if root:
                return root
            if arg:
                sys.exit(f"{cand} does not look like the course repo (no .claude, .agents or .archon inside).")
    if clone:
        dest = target.parent / COURSE_DIRNAME
        print(f"Cloning {COURSE_URL} into {dest} ...")
        r = subprocess.run(["git", "clone", "--depth", "1", COURSE_URL, str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and is_course_root(dest):
            return dest
        sys.exit("Could not clone the course repo (it may be private, or git has no login here):\n" + (r.stderr.strip()[-400:] or "unknown error")
                 + "\nGive the script a clone or a zip of the repo with --source <folder or .zip>.")
    sys.exit("Could not find the course repo. Looked in:\n  " + "\n  ".join(tried)
             + f"\nPass --source <clone folder or .zip>, set {ENV_SOURCE}, or add --clone to clone it next to the project.")


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, path):
        self.rows.append((status, str(path).replace("\\", "/")))


def copy_file(src, dst, rep, rel, overwrite, dry):
    if dst.exists():
        if filecmp.cmp(src, dst, shallow=False):
            rep.add("same", rel)
            return
        if not overwrite:
            rep.add("kept yours (differs)", rel)
            return
        rep.add("overwritten", rel)
    else:
        rep.add("copied", rel)
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def copy_tree(source, target, rel, rep, args, include_optin=False):
    src = source / rel
    if not src.exists():
        rep.add("missing in source", rel)
        return
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for p in files:
        r = p.relative_to(source)
        posix = r.as_posix()
        if any(part in SKIP_NAMES for part in r.parts) or posix in NEVER:
            continue
        flag = OPT_IN.get(posix)
        if flag and not (include_optin or getattr(args, flag)):
            rep.add("left out (opt-in)", posix)
            continue
        copy_file(p, target / r, rep, r, args.overwrite, args.dry_run)


def do_mcp(source, target, rep, args):
    server = None
    sp = source / ".mcp.json"
    if sp.exists():
        try:
            servers = json.loads(sp.read_text(encoding="utf-8")).get("mcpServers") or {}
            hits = {n: s for n, s in servers.items() if "codebase_search" in json.dumps(s) or "codebase-search" in n.lower()}
            if len(hits) == 1:
                server = next(iter(hits.values()))
        except json.JSONDecodeError:
            pass
    note = "from the course's .mcp.json"
    if server is None:
        server, note = MCP_FALLBACK, "built-in default (no single codebase-search server found in the course .mcp.json)"
    name = "codebase-search"
    dst = target / ".mcp.json"
    if not dst.exists():
        rep.add("copied", f".mcp.json (only '{name}', {note})")
        if not args.dry_run:
            dst.write_text(json.dumps({"mcpServers": {name: server}}, indent=2) + "\n", encoding="utf-8")
    else:
        try:
            data = json.loads(dst.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            rep.add("SKIPPED", f".mcp.json is not valid JSON ({e}); add '{name}' by hand")
            data = None
        if data is not None:
            servers = data.setdefault("mcpServers", {})
            if name in servers and servers[name] == server:
                rep.add("same", f".mcp.json ('{name}')")
            elif name in servers and not args.overwrite:
                rep.add("kept yours (differs)", f".mcp.json ('{name}')")
            else:
                servers[name] = server
                rep.add("merged", f".mcp.json (added '{name}', your other servers untouched)")
                if not args.dry_run:
                    dst.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    copy_tree(source, target, Path(MCP_SCRIPT), rep, args)


def do_claude_md(source, target, rep, args):
    tpl = source / ".claude" / "CLAUDE.md.template"
    if not tpl.exists():
        rep.add("missing in source", ".claude/CLAUDE.md.template")
    elif (target / "CLAUDE.md").exists() or (target / "AGENTS.md").exists():
        rep.add("kept yours (exists)", "CLAUDE.md / AGENTS.md")
    else:
        rep.add("copied", "CLAUDE.md (from the template; fill in the blanks)")
        if not args.dry_run:
            shutil.copy2(tpl, target / "CLAUDE.md")


def list_source(source):
    out = {}
    for folder in FOLDER_PARTS.values():
        base = source / folder
        if base.is_dir():
            out[folder] = sorted(c.name + ("/" if c.is_dir() else "") for c in base.iterdir() if c.name not in SKIP_NAMES)
            for sub in sorted(c for c in base.iterdir() if c.is_dir() and c.name not in SKIP_NAMES):
                out[f"{folder}/{sub.name}"] = sorted(c.name + ("/" if c.is_dir() else "") for c in sub.iterdir() if c.name not in SKIP_NAMES)
    try:
        out[".mcp.json servers"] = sorted((json.loads((source / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {}).keys())
    except (OSError, json.JSONDecodeError):
        out[".mcp.json servers"] = None
    out[MCP_SCRIPT] = (source / MCP_SCRIPT).exists()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help=f"course clone folder or .zip (else ${ENV_SOURCE}, or the usual places)")
    ap.add_argument("--target", default=".", help="project root (default: current directory)")
    ap.add_argument("--parts", default="all", help="comma list of: claude, agents, archon, mcp (default: all)")
    ap.add_argument("--pick", action="append", default=[], metavar="REL", help="copy one item, e.g. .claude/skills/foo (repeatable)")
    ap.add_argument("--with-template", action="store_true", help="also copy .claude/CLAUDE.md.template")
    ap.add_argument("--claude-md", action="store_true", help="create CLAUDE.md in the project from the template (only if there is no CLAUDE.md/AGENTS.md)")
    ap.add_argument("--with-settings-example", action="store_true", help="also copy .claude/settings.json.example")
    ap.add_argument("--with-posthog-headers", action="store_true", help="also copy .claude/mcp-posthog-headers.sh")
    ap.add_argument("--overwrite", action="store_true", help="replace project files that differ (default keeps yours)")
    ap.add_argument("--dry-run", action="store_true", help="list what would happen, change nothing")
    ap.add_argument("--list", action="store_true", help="show what the course repo offers, then stop")
    ap.add_argument("--clone", action="store_true", help=f"if no source is found, git clone {COURSE_DIRNAME} next to the project")
    a = ap.parse_args()

    target = Path(a.target).expanduser().resolve()
    if not target.is_dir():
        sys.exit(f"Project folder not found: {target}")
    parts = [] if a.parts == "none" else ALL_PARTS if a.parts == "all" else [p.strip() for p in a.parts.split(",") if p.strip()]
    bad = [p for p in parts if p not in ALL_PARTS]
    if bad:
        sys.exit(f"Unknown part(s): {', '.join(bad)}. Choose from: {', '.join(ALL_PARTS)}")
    for pick in a.pick:
        pp = Path(pick)
        if pp.is_absolute() or ".." in pp.parts or not pp.parts or pp.parts[0] not in FOLDER_PARTS.values():
            sys.exit(f"--pick must be a path inside .claude, .agents or .archon: {pick}")

    with tempfile.TemporaryDirectory() as tmp:
        source = resolve_source(a.source, target, a.clone, tmp)
        if source == target or source in target.parents or target in source.parents:
            sys.exit("The project and the course clone must be separate folders.")
        if a.list:
            print(json.dumps(list_source(source), indent=2))
            return
        rep = Report()
        for key in parts:
            if key in FOLDER_PARTS:
                copy_tree(source, target, Path(FOLDER_PARTS[key]), rep, a)
        for pick in a.pick:
            copy_tree(source, target, Path(pick), rep, a, include_optin=(source / pick).is_file())
        if "mcp" in parts:
            do_mcp(source, target, rep, a)
        if a.claude_md:
            do_claude_md(source, target, rep, a)

        print(("[dry run] nothing was changed\n" if a.dry_run else "") + f"Course: {source}\nProject: {target}\n")
        groups = {}
        for status, path in rep.rows:
            groups.setdefault(status, []).append(path)
        for status in ("copied", "merged", "overwritten", "same", "kept yours (differs)", "kept yours (exists)", "left out (opt-in)", "missing in source", "SKIPPED"):
            paths = groups.get(status)
            if paths:
                print(f"{status}: {len(paths)}")
                for p in paths[: (200 if a.dry_run else 15)]:
                    print(f"   {p}")
                if len(paths) > (200 if a.dry_run else 15):
                    print(f"   ... and {len(paths) - (200 if a.dry_run else 15)} more")


if __name__ == "__main__":
    main()

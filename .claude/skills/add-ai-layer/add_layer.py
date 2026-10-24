#!/usr/bin/env python3
"""Copy parts of the Agentic Coding Course "AI Layer" from a clone of the course repo into a project.

Only copies; never edits the project's existing files. Existing files are kept unless --overwrite is given.
Standard library only (Python 3.8+), so it runs the same on Windows, macOS and Linux.
"""
import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

FOLDER_PARTS = {"claude": ".claude", "agents": ".agents", "archon": ".archon"}
ALL_PARTS = ["claude", "agents", "archon", "mcp", "claudemd"]
SKIP_DIRS = {".git", "__pycache__", ".DS_Store"}
TEMPLATE = Path(".claude") / "CLAUDE.md.template"
MCP_SCRIPT = Path("tooling") / "mcp" / "codebase_search.py"


class Report:
    def __init__(self):
        self.rows = []      # (status, path)

    def add(self, status, path):
        self.rows.append((status, str(path).replace("\\", "/")))

    def count(self, status):
        return sum(1 for s, _ in self.rows if s == status)


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


def copy_tree(src_root, dst_root, rel, rep, overwrite, dry):
    """Copy src_root/rel (a file or a whole folder) to dst_root/rel, keeping the structure."""
    src = src_root / rel
    if not src.exists():
        rep.add("missing in source", rel)
        return
    if src.is_file():
        copy_file(src, dst_root / rel, rep, rel, overwrite, dry)
        return
    for p in sorted(src.rglob("*")):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.relative_to(src_root).parts):
            continue
        r = p.relative_to(src_root)
        copy_file(p, dst_root / r, rep, r, overwrite, dry)


def find_codebase_servers(cfg):
    servers = cfg.get("mcpServers") or {}
    return {n: s for n, s in servers.items() if "codebase_search" in json.dumps(s) or "codebase-search" in n.lower() or "codebase_search" in n.lower()}


def do_mcp(source, target, rep, overwrite, dry):
    src_cfg_path = source / ".mcp.json"
    if not src_cfg_path.exists():
        rep.add("missing in source", ".mcp.json")
        return
    cfg = json.loads(src_cfg_path.read_text(encoding="utf-8"))
    keep = find_codebase_servers(cfg)
    if len(keep) != 1:
        names = ", ".join((cfg.get("mcpServers") or {}).keys()) or "none"
        raise SystemExit(f"Could not pick the codebase-search server in .mcp.json (found {len(keep)} matches; servers: {names}). "
                         "Tell the user and let them choose; do not guess.")
    (name, server), = keep.items()
    dst_cfg_path = target / ".mcp.json"
    if dst_cfg_path.exists():
        dst = json.loads(dst_cfg_path.read_text(encoding="utf-8"))
        servers = dst.setdefault("mcpServers", {})
        if name in servers and servers[name] == server:
            rep.add("same", f".mcp.json (server '{name}')")
        elif name in servers and not overwrite:
            rep.add("kept yours (differs)", f".mcp.json (server '{name}')")
        else:
            servers[name] = server
            rep.add("merged", f".mcp.json (added server '{name}', your other servers untouched)")
            if not dry:
                dst_cfg_path.write_text(json.dumps(dst, indent=2) + "\n", encoding="utf-8")
    else:
        rep.add("copied", f".mcp.json (only server '{name}')")
        if not dry:
            dst_cfg_path.write_text(json.dumps({"mcpServers": {name: server}}, indent=2) + "\n", encoding="utf-8")
    copy_tree(source, target, MCP_SCRIPT, rep, overwrite, dry)


def do_claudemd(source, target, rep, dry):
    tpl = source / TEMPLATE
    if not tpl.exists():
        rep.add("missing in source", TEMPLATE)
        return
    dst = target / "CLAUDE.md"
    if dst.exists():
        rep.add("kept yours (exists)", "CLAUDE.md")
        return
    rep.add("copied", "CLAUDE.md (from the template)")
    if not dry:
        shutil.copy2(tpl, dst)


def list_parts(source):
    out = {}
    for key, folder in FOLDER_PARTS.items():
        base = source / folder
        out[folder] = sorted(p.name + ("/" if p.is_dir() else "") for p in base.iterdir() if p.name not in SKIP_DIRS) if base.is_dir() else None
        if key == "claude" and base.is_dir():      # one level deeper so single skills / subagents can be picked
            for sub in sorted(p for p in base.iterdir() if p.is_dir()):
                out[f"{folder}/{sub.name}"] = sorted(c.name + ("/" if c.is_dir() else "") for c in sub.iterdir() if c.name not in SKIP_DIRS)
    out[".mcp.json"] = sorted((json.loads((source / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {}).keys()) if (source / ".mcp.json").exists() else None
    out[str(MCP_SCRIPT).replace("\\", "/")] = (source / MCP_SCRIPT).exists()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="path to the clone of the course repo")
    ap.add_argument("--target", help="path to the project to add the layer to")
    ap.add_argument("--parts", default="all", help="comma list of: claude, agents, archon, mcp, claudemd (default: all)")
    ap.add_argument("--pick", action="append", default=[], metavar="REL", help="copy one item, e.g. .claude/skills/foo (repeatable); use instead of or as well as --parts")
    ap.add_argument("--overwrite", action="store_true", help="replace project files that differ (default keeps yours)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--list", action="store_true", help="show what the course repo offers, then stop")
    a = ap.parse_args()

    source = Path(a.source).expanduser().resolve()
    if not source.is_dir():
        sys.exit(f"Source folder not found: {source}")
    if a.list:
        print(json.dumps(list_parts(source), indent=2))
        return
    if not a.target:
        sys.exit("--target is required")
    target = Path(a.target).expanduser().resolve()
    if not target.is_dir():
        sys.exit(f"Project folder not found: {target}")
    if target == source or source in target.parents or target in source.parents:
        sys.exit("The project and the course clone must be separate folders (clone it next to the project).")

    parts = ALL_PARTS if a.parts == "all" else [p.strip() for p in a.parts.split(",") if p.strip()]
    if a.parts == "none":
        parts = []
    bad = [p for p in parts if p not in ALL_PARTS]
    if bad:
        sys.exit(f"Unknown part(s): {', '.join(bad)}. Choose from: {', '.join(ALL_PARTS)}")
    for pick in a.pick:
        top = Path(pick).parts[0] if Path(pick).parts else ""
        if top not in FOLDER_PARTS.values() or ".." in Path(pick).parts or Path(pick).is_absolute():
            sys.exit(f"--pick must be inside .claude, .agents or .archon: {pick}")

    rep = Report()
    for key in parts:
        if key in FOLDER_PARTS:
            copy_tree(source, target, Path(FOLDER_PARTS[key]), rep, a.overwrite, a.dry_run)
    for pick in a.pick:
        copy_tree(source, target, Path(pick), rep, a.overwrite, a.dry_run)
    if "mcp" in parts:
        do_mcp(source, target, rep, a.overwrite, a.dry_run)
    if "claudemd" in parts:
        do_claudemd(source, target, rep, a.dry_run)

    print(("DRY RUN, nothing was changed\n" if a.dry_run else "") + f"Source: {source}\nProject: {target}\n")
    summary = {}
    for status, path in rep.rows:
        summary.setdefault(status, []).append(path)
    for status, paths in summary.items():
        print(f"{status}: {len(paths)}")
        for p in paths[:12]:
            print(f"   {p}")
        if len(paths) > 12:
            print(f"   … and {len(paths) - 12} more")


if __name__ == "__main__":
    main()

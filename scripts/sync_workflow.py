#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import difflib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync-workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    pull_p = sub.add_parser("pull")
    pull_p.add_argument("--slug", required=True)
    pull_p.add_argument("--repo-root", default=None)
    pull_p.set_defaults(func=cmd_pull)

    propose_p = sub.add_parser("propose")
    propose_p.add_argument("--slug", required=True)
    propose_p.add_argument("--repo-root", default=None)
    propose_p.add_argument("--rationale", default=None)
    propose_p.set_defaults(func=cmd_propose)

    status_p = sub.add_parser("status")
    status_p.add_argument("--slug", required=True)
    status_p.add_argument("--repo-root", default=None)
    status_p.set_defaults(func=cmd_status)

    return parser


def load_config() -> dict:
    base_url = os.environ.get("TASKFORGE_BASE_URL")
    api_key = os.environ.get("TASKFORGE_API_KEY")
    missing = []
    if not base_url:
        missing.append("TASKFORGE_BASE_URL")
    if not api_key:
        missing.append("TASKFORGE_API_KEY")
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return {"base_url": base_url.rstrip("/"), "api_key": api_key}


def _get_workflow(client: httpx.Client, cfg: dict, slug: str) -> dict[str, Any]:
    """Return the WorkflowOut dict for *slug*."""
    try:
        resp = client.get(f"{cfg['base_url']}/workflows/by-slug/{slug}")
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            print(f"ERROR: workflow '{slug}' not found", file=sys.stderr)
        else:
            print(
                f"ERROR: GET /workflows/by-slug/{slug} returned {exc.response.status_code}",
                file=sys.stderr,
            )
        sys.exit(1)
    return resp.json()


def _get_published_version(client: httpx.Client, cfg: dict, workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the published WorkflowVersionOut for *workflow* (by its id)."""
    wf_id = workflow["id"]
    slug = workflow["slug"]

    # Try the convenience endpoint first.
    try:
        resp = client.get(f"{cfg['base_url']}/workflows/by-slug/{slug}/published")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        pass

    # Fall back to list + filter.
    try:
        resp = client.get(f"{cfg['base_url']}/workflows/{wf_id}/versions")
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"ERROR: GET /workflows/{wf_id}/versions returned {exc.response.status_code}",
            file=sys.stderr,
        )
        sys.exit(1)

    versions = resp.json()
    published = [v for v in versions if v.get("is_published")]
    if not published:
        print(f"ERROR: workflow '{slug}' has no published version", file=sys.stderr)
        sys.exit(1)
    return published[-1]


def _compose_materialized(workflow: dict[str, Any], version: dict[str, Any]) -> str:
    """Build YAML-frontmatter + body_template string from DB data."""
    lines = ["---"]
    lines.append(f"name: {workflow.get('name', workflow['slug'])}")
    lines.append(f"id: {workflow['slug']}")
    if workflow.get("description"):
        lines.append(f"description: {workflow['description']}")
    best_for = version.get("best_for") or []
    if best_for:
        lines.append("best_for:")
        for item in best_for:
            lines.append(f"  - {item}")
    chains_with = version.get("chains_with") or []
    if chains_with:
        lines.append("chains_with:")
        for item in chains_with:
            lines.append(f"  - {item}")
    phases = version.get("phases") or []
    if phases:
        lines.append("phases:")
        for item in phases:
            lines.append(f"  - {item}")
    lines.append(f"version_int: {version['version_int']}")
    lines.append(f"version_id: {version['id']}")
    lines.append("---")
    body = version.get("body_template", "")
    return "\n".join(lines) + "\n" + body


def _repo_root(args: argparse.Namespace) -> Path:
    return Path(args.repo_root) if args.repo_root else Path.cwd()


def _overlay_path(root: Path, slug: str) -> Path:
    return root / ".orchestration" / "workflows" / f"{slug}.overlay.md"


def _materialized_path(root: Path, slug: str) -> Path:
    return root / ".orchestration" / "workflows" / f"{slug}.md"


def _merge(base_content: str, overlay: str | None) -> str:
    if overlay:
        return base_content + "\n\n---\n\n" + overlay
    return base_content


def cmd_pull(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)

    workflow = _get_workflow(client, cfg, slug)
    version = _get_published_version(client, cfg, workflow)
    base_content = _compose_materialized(workflow, version)

    overlay_file = _overlay_path(root, slug)
    overlay: str | None = None
    if overlay_file.exists():
        overlay = overlay_file.read_text(encoding="utf-8")

    merged = _merge(base_content, overlay)
    dest = _materialized_path(root, slug)
    dest.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{slug}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(merged)
        os.replace(tmp, dest)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

    overlay_flag = "yes" if overlay is not None else "no"
    print(f"Wrote .orchestration/workflows/{slug}.md  [overlay: {overlay_flag}]")


def cmd_propose(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)

    overlay_file = _overlay_path(root, slug)
    if not overlay_file.exists():
        print(f"ERROR: overlay file not found: {overlay_file}", file=sys.stderr)
        sys.exit(1)
    overlay_text = overlay_file.read_text(encoding="utf-8")

    workflow = _get_workflow(client, cfg, slug)
    version = _get_published_version(client, cfg, workflow)

    # Merge overlay into body_template and propose as a new draft version.
    merged_body = version.get("body_template", "") + "\n\n---\n\n" + overlay_text

    body = {
        "body_template": merged_body,
        "best_for": version.get("best_for", []),
        "chains_with": version.get("chains_with", []),
        "phases": version.get("phases", []),
        "notes": args.rationale,
    }
    try:
        resp = client.post(
            f"{cfg['base_url']}/workflows/{workflow['id']}/versions",
            json=body,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"ERROR: POST /workflows/{workflow['id']}/versions returned {exc.response.status_code}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    print(f"draft_version_id: {data['id']}")
    print(f"version_int: {data['version_int']}")
    print("Review in the Taskforge GUI, then publish to promote the draft.")


def cmd_status(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)

    workflow = _get_workflow(client, cfg, slug)
    version = _get_published_version(client, cfg, workflow)
    base_content = _compose_materialized(workflow, version)

    overlay_file = _overlay_path(root, slug)
    overlay: str | None = overlay_file.read_text(encoding="utf-8") if overlay_file.exists() else None
    expected = _merge(base_content, overlay)

    materialized_file = _materialized_path(root, slug)
    if not materialized_file.exists():
        print(f"STATUS: missing  {slug}  (run `pull` to create)")
        sys.exit(1)

    materialized = materialized_file.read_text(encoding="utf-8")
    if expected == materialized:
        print(f"STATUS: in-sync  {slug}")
        return

    diff_lines = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            materialized.splitlines(keepends=True),
            fromfile="expected",
            tofile="materialized",
            n=3,
        )
    )
    print(f"STATUS: drifted  {slug}")
    truncated = diff_lines[:20]
    remaining = len(diff_lines) - 20
    print("".join(truncated), end="")
    if remaining > 0:
        print(f"... {remaining} more lines")
    sys.exit(1)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    with httpx.Client(
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
        timeout=10.0,
    ) as client:
        args.func(args, cfg, client)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import difflib
import os
import sys
import tempfile
from pathlib import Path

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync-persona")
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


def _get_agent(client: httpx.Client, cfg: dict, slug: str) -> str:
    try:
        resp = client.get(f"{cfg['base_url']}/agents/{slug}")
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            print(f"ERROR: agent '{slug}' not found", file=sys.stderr)
        else:
            print(
                f"ERROR: GET /agents/{slug} returned {exc.response.status_code}",
                file=sys.stderr,
            )
        sys.exit(1)
    return resp.json()["base_persona"]


def _repo_root(args: argparse.Namespace) -> Path:
    return Path(args.repo_root) if args.repo_root else Path.cwd()


def _overlay_path(root: Path, slug: str) -> Path:
    return root / ".orchestration" / "agents" / f"{slug}.overlay.md"


def _materialized_path(root: Path, slug: str) -> Path:
    return root / ".orchestration" / "agents" / f"{slug}.md"


def _merge(base_persona: str, overlay: str | None) -> str:
    if overlay:
        return base_persona + "\n\n---\n\n" + overlay
    return base_persona


def cmd_pull(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)
    base_persona = _get_agent(client, cfg, slug)

    overlay_file = _overlay_path(root, slug)
    overlay: str | None = None
    if overlay_file.exists():
        overlay = overlay_file.read_text(encoding="utf-8")

    merged = _merge(base_persona, overlay)
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
    print(f"Wrote .orchestration/agents/{slug}.md  [overlay: {overlay_flag}]")


def cmd_propose(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)

    overlay_file = _overlay_path(root, slug)
    if not overlay_file.exists():
        print(f"ERROR: overlay file not found: {overlay_file}", file=sys.stderr)
        sys.exit(1)
    proposed_persona = overlay_file.read_text(encoding="utf-8")

    base_snapshot = _get_agent(client, cfg, slug)

    body = {
        "agent_slug": slug,
        "base_snapshot": base_snapshot,
        "proposed_persona": proposed_persona,
        "rationale": args.rationale,
        "source_project_task_id": None,
    }
    try:
        resp = client.post(f"{cfg['base_url']}/agent-proposals", json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"ERROR: POST /agent-proposals returned {exc.response.status_code}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    print(f"proposal_id: {data['id']}")
    print(f"review_task_id: {data.get('review_task_id')}")


def cmd_status(args: argparse.Namespace, cfg: dict, client: httpx.Client) -> None:
    slug = args.slug
    root = _repo_root(args)

    base_persona = _get_agent(client, cfg, slug)

    overlay_file = _overlay_path(root, slug)
    overlay: str | None = overlay_file.read_text(encoding="utf-8") if overlay_file.exists() else None
    expected = _merge(base_persona, overlay)

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

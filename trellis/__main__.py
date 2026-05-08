"""
Trellis CLI entrypoint.

Usage:
    python3 -m trellis init   <project_dir>
    python3 -m trellis serve  <project_dir>  [--host 127.0.0.1] [--port 8765]

`init` copies types.example.yaml into a fresh directory so a project is bootable.
`serve` starts uvicorn on a single project. The browser is *not* opened
automatically — the user controls that. The server binds to loopback by
default; pass --host to override.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLE_TYPES = PACKAGE_DIR / "types.example.yaml"


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.project_dir).resolve()
    if target.exists() and any(target.iterdir()):
        print(f"trellis: refuse to init non-empty directory: {target}", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE_TYPES, target / "types.yaml")
    (target / "data").mkdir(exist_ok=True)
    print(f"trellis: initialized project at {target}")
    print(f"  - {target / 'types.yaml'}  (edit to taste)")
    print(f"  - {target / 'data'}        (flatgraph storage, lazy)")
    print(f"\n  Start with:  python3 -m trellis serve {target}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn  # local import so `init` works without uvicorn installed
    from .app import create_app

    project_dir = Path(args.project_dir).resolve()
    if not (project_dir / "types.yaml").exists():
        print(f"trellis: no types.yaml in {project_dir}", file=sys.stderr)
        print(f"  run:   python3 -m trellis init {project_dir}", file=sys.stderr)
        return 2

    app = create_app(project_dir)
    print(f"trellis: serving {project_dir} on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    from .core import TrellisError
    from .seed_demo import seed

    project_dir = Path(args.project_dir).resolve()
    if not (project_dir / "types.yaml").exists():
        print(f"trellis: no types.yaml in {project_dir}", file=sys.stderr)
        print(f"  run:   python3 -m trellis init {project_dir}", file=sys.stderr)
        return 2
    try:
        stats = seed(project_dir, force=args.force)
    except TrellisError as e:
        print(f"trellis: {e}", file=sys.stderr)
        return 1

    print(f"trellis: seeded demo dataset into {project_dir}")
    print()
    print("  Counts by type:")
    for t, c in sorted(stats["counts"].items()):
        print(f"    {t:<14} {c:>3}")
    print()
    print("  Readiness check (these are what the dashboard should show):")
    print(f"    Prozessvalidierung    aggregate = {stats['pv_readiness'].upper()}   (red — cascade from PQ)")
    print(f"    PQ Tablettenlinie A   aggregate = {stats['pq_readiness'].upper()}   (red — 2 unsatisfied OQ blockers)")
    print(f"    IQ Mischer M-01       aggregate = {stats['leaf_readiness'].upper()} (green — leaf, no contracts)")
    print()
    print(f"  Stub demo:    {stats['stub_ref']}  (Phase ohne betrifft)")
    print()
    print(f"  Start the UI:  python3 -m trellis serve {project_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trellis")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize a fresh project directory")
    p_init.add_argument("project_dir")
    p_init.set_defaults(func=_cmd_init)

    p_serve = sub.add_parser("serve", help="Run the Trellis web UI for a project")
    p_serve.add_argument("project_dir")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=_cmd_serve)

    p_seed = sub.add_parser(
        "seed",
        help="Populate the project with a realistic Q&V demo dataset",
    )
    p_seed.add_argument("project_dir")
    p_seed.add_argument(
        "--force", action="store_true",
        help="wipe existing data/ before seeding",
    )
    p_seed.set_defaults(func=_cmd_seed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Docksmith CLI

Commands:
  docksmith build -t <name:tag> [--no-cache] [<context>]
  docksmith images
  docksmith rmi <name:tag>
  docksmith run [-e KEY=VALUE]... <name:tag> [cmd...]
  docksmith import <image.tar> <name:tag>
"""

import argparse
import sys


def cmd_build(args):
    from .builder import Builder
    if ":" not in args.tag:
        _die(f"tag must be name:tag format, got '{args.tag}'")
    name, tag = args.tag.split(":", 1)
    Builder(
        context_dir=args.context,
        name=name,
        tag=tag,
        no_cache=args.no_cache,
    ).build()


def cmd_images(args):
    from .store import ImageStore
    images = ImageStore().list_images()
    if not images:
        print("No images found.")
        return
    fmt = "{:<22} {:<12} {:<15} {}"
    print(fmt.format("NAME", "TAG", "ID", "CREATED"))
    for img in images:
        d = img.get("digest", "")
        short = d.replace("sha256:", "")[:12] if d else "unknown"
        print(fmt.format(img["name"], img["tag"], short, img.get("created", "unknown")))


def cmd_rmi(args):
    from .store import ImageStore
    if ":" not in args.name_tag:
        _die(f"must be name:tag format, got '{args.name_tag}'")
    name, tag = args.name_tag.split(":", 1)
    ImageStore().remove_image(name, tag)
    print(f"Removed {args.name_tag}")


def cmd_run(args):
    from .runtime import ContainerRuntime
    if ":" not in args.name_tag:
        _die(f"must be name:tag format, got '{args.name_tag}'")
    name, tag = args.name_tag.split(":", 1)

    env_overrides = {}
    for e in (args.env or []):
        if "=" not in e:
            _die(f"-e flag must be KEY=VALUE, got '{e}'")
        k, v = e.split("=", 1)
        env_overrides[k] = v

    ContainerRuntime().run(
        name=name,
        tag=tag,
        cmd_override=args.cmd or [],
        env_overrides=env_overrides,
    )


def cmd_import(args):
    from .importer import import_image
    if ":" not in args.name_tag:
        _die(f"must be name:tag format, got '{args.name_tag}'")
    name, tag = args.name_tag.split(":", 1)
    import_image(args.tar, name, tag)


def _die(msg: str):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="docksmith",
        description="Simplified Docker-like build and runtime system",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── build ──────────────────────────────────────────────────────────────────
    p_build = sub.add_parser("build", help="Build an image from a Docksmithfile")
    p_build.add_argument("-t", dest="tag", required=True, metavar="name:tag",
                         help="Name and tag for the resulting image")
    p_build.add_argument("--no-cache", action="store_true",
                         help="Do not use or write the build cache")
    p_build.add_argument("context", nargs="?", default=".",
                         help="Build context directory (default: .)")

    # ── images ─────────────────────────────────────────────────────────────────
    sub.add_parser("images", help="List images in the local store")

    # ── rmi ────────────────────────────────────────────────────────────────────
    p_rmi = sub.add_parser("rmi", help="Remove an image")
    p_rmi.add_argument("name_tag", metavar="name:tag")

    # ── run ────────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run a container")
    p_run.add_argument("-e", dest="env", action="append", metavar="KEY=VALUE",
                       help="Set or override an environment variable (repeatable)")
    p_run.add_argument("name_tag", metavar="name:tag")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER,
                       help="Override the image CMD")

    # ── import ─────────────────────────────────────────────────────────────────
    p_import = sub.add_parser("import", help="Import a base image from a Docker-saved tar")
    p_import.add_argument("tar",      metavar="image.tar")
    p_import.add_argument("name_tag", metavar="name:tag")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "build":  cmd_build,
        "images": cmd_images,
        "rmi":    cmd_rmi,
        "run":    cmd_run,
        "import": cmd_import,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
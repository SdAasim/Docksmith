"""
Container runtime — `docksmith run <name:tag> [cmd...]`

Assembles the image filesystem, applies env overrides,
runs the process in isolation, waits, prints exit code, cleans up.
"""

import sys
import tempfile
from typing import Dict, List

from .isolation import run_isolated
from .layers import extract_layers
from .store import ImageStore


class ContainerRuntime:
    def __init__(self):
        self.store = ImageStore()

    def run(
        self,
        name: str,
        tag: str,
        cmd_override: List[str],
        env_overrides: Dict[str, str],
    ):
        manifest = self.store.load_manifest(name, tag)
        config   = manifest.get("config", {})

        # ── resolve command ────────────────────────────────────────────────────
        image_cmd = config.get("Cmd", [])
        if cmd_override:
            cmd = cmd_override
        elif image_cmd:
            cmd = image_cmd
        else:
            print(
                f"Error: no CMD defined in image '{name}:{tag}' and no command given at runtime.",
                file=sys.stderr,
            )
            sys.exit(1)

        # ── resolve environment ────────────────────────────────────────────────
        env: Dict[str, str] = {}
        for kv in config.get("Env", []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                env[k] = v
        # -e overrides take precedence
        env.update(env_overrides)

        # ── working directory ──────────────────────────────────────────────────
        workdir = config.get("WorkingDir", "/") or "/"

        # ── assemble filesystem ────────────────────────────────────────────────
        layer_digests = [l["digest"] for l in manifest.get("layers", [])]

        with tempfile.TemporaryDirectory(prefix="docksmith_ctr_") as rootfs:
            extract_layers(self.store, layer_digests, rootfs)

            # Run the isolated process
            exit_code = run_isolated(
                rootfs  = rootfs,
                cmd     = cmd,
                env     = env,
                workdir = workdir,
            )

        print(f"\nContainer exited with code {exit_code}")
        sys.exit(exit_code)

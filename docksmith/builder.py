"""
Build engine — executes a Docksmithfile and produces an image manifest.

Steps:
  FROM    → load base image layers
  COPY    → copy files from context into a new layer (cached)
  RUN     → execute a command in isolation, capture fs delta as new layer (cached)
  WORKDIR → update workdir in build context (no layer)
  ENV     → accumulate env vars (no layer)
  CMD     → store default command in image config (no layer)
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cache import collect_copy_file_digests, compute_cache_key
from .isolation import run_isolated
from .layers import (
    digest_bytes,
    extract_layer,
    extract_layers,
    make_delta_tar,
    make_tree_tar,
)
from .parser import Instruction, LAYER_PRODUCING, parse_docksmithfile
from .store import ImageStore


class Builder:
    def __init__(
        self,
        context_dir: str,
        name: str,
        tag: str,
        no_cache: bool = False,
    ):
        self.context_dir = str(Path(context_dir).resolve())
        self.name        = name
        self.tag         = tag
        self.no_cache    = no_cache
        self.store       = ImageStore()

        # Build state
        self.layers: List[dict]     = []      # accumulated layer entries
        self.env:    Dict[str, str] = {}      # accumulated ENV
        self.workdir: str           = ""      # current WORKDIR
        self.cmd_list: List[str]    = []      # CMD (last one wins)
        self.prev_digest: str       = ""      # digest of last layer-producing step
        self.cache_cascade: bool    = False   # True once any step misses
        self.base_created: Optional[str] = None  # created timestamp from cache hits

    # ──────────────────────────────────────────────────────────────────────────

    def build(self):
        docksmithfile = os.path.join(self.context_dir, "Docksmithfile")
        instructions  = parse_docksmithfile(docksmithfile)

        total_steps = len(instructions)
        start_wall  = time.monotonic()

        for step_idx, instr in enumerate(instructions, 1):
            print(f"Step {step_idx}/{total_steps} : {instr.op} {instr.raw_args}", end="", flush=True)

            if instr.op == "FROM":
                print()   # FROM never gets cache status or timing
                self._exec_from(instr)

            elif instr.op == "COPY":
                self._exec_copy(instr)

            elif instr.op == "RUN":
                self._exec_run(instr)

            elif instr.op == "WORKDIR":
                print()
                self._exec_workdir(instr)

            elif instr.op == "ENV":
                print()
                self._exec_env(instr)

            elif instr.op == "CMD":
                print()
                self._exec_cmd(instr)

        elapsed = time.monotonic() - start_wall
        manifest = self._write_manifest()
        short_id  = manifest["digest"].replace("sha256:", "")[:12]
        print(f"\nSuccessfully built sha256:{short_id} {self.name}:{self.tag} ({elapsed:.2f}s)")

    # ── instruction handlers ──────────────────────────────────────────────────

    def _exec_from(self, instr: Instruction):
        manifest = self.store.load_manifest(instr.from_name, instr.from_tag)
        self.layers     = list(manifest.get("layers", []))
        self.env        = {}
        self.workdir    = manifest.get("config", {}).get("WorkingDir", "")
        self.cmd_list   = manifest.get("config", {}).get("Cmd", [])
        self.prev_digest = manifest["digest"]   # base image manifest digest
        # Inherit ENV from base image config
        for kv in manifest.get("config", {}).get("Env", []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.env[k] = v

    def _exec_copy(self, instr: Instruction):
        t0 = time.monotonic()

        # 1. Collect source files
        file_pairs = self._collect_copy_sources(instr)

        # 2. Compute cache key
        copy_digests = collect_copy_file_digests(self.context_dir, instr.copy_srcs)
        cache_key = compute_cache_key(
            prev_digest      = self.prev_digest,
            instruction_text = f"COPY {instr.raw_args}",
            workdir          = self.workdir,
            env_state        = self.env,
            copy_file_digests= copy_digests,
        )

        hit, layer_digest = self._cache_lookup(cache_key)
        if hit:
            elapsed = time.monotonic() - t0
            print(f" [CACHE HIT] {elapsed:.2f}s")
            self._record_layer(layer_digest, f"COPY {instr.raw_args}")
            return

        # 3. Build the tar delta
        # Map source files to their destination paths inside the image
        dest_pairs = self._map_copy_dest(file_pairs, instr.copy_dest)
        tar_data   = make_delta_tar(dest_pairs)
        layer_digest = digest_bytes(tar_data)

        self.store.write_layer(layer_digest, tar_data)
        if not self.no_cache:
            self.store.cache_set(cache_key, layer_digest)
        self.cache_cascade = True   # any miss cascades all subsequent steps

        elapsed = time.monotonic() - t0
        print(f" [CACHE MISS] {elapsed:.2f}s")
        self._record_layer(layer_digest, f"COPY {instr.raw_args}")

    def _exec_run(self, instr: Instruction):
        t0 = time.monotonic()

        cache_key = compute_cache_key(
            prev_digest      = self.prev_digest,
            instruction_text = f"RUN {instr.raw_args}",
            workdir          = self.workdir,
            env_state        = self.env,
        )

        hit, layer_digest = self._cache_lookup(cache_key)
        if hit:
            elapsed = time.monotonic() - t0
            print(f" [CACHE HIT] {elapsed:.2f}s")
            self._record_layer(layer_digest, f"RUN {instr.raw_args}")
            return

        # Build the filesystem for execution
        with tempfile.TemporaryDirectory(prefix="docksmith_run_") as rootfs:
            # Extract all layers so far
            extract_layers(self.store, [l["digest"] for l in self.layers], rootfs)

            # Ensure WORKDIR exists inside rootfs
            if self.workdir:
                wd_inside = rootfs + self.workdir
                os.makedirs(wd_inside, exist_ok=True)

            # Snapshot before
            before = _snapshot(rootfs)

            # Execute in isolation
            exit_code = run_isolated(
                rootfs  = rootfs,
                cmd     = [instr.run_cmd],
                env     = self.env,
                workdir = self.workdir or "/",
            )
            if exit_code != 0:
                print(
                    f"\nError: RUN command exited with code {exit_code}: {instr.run_cmd}",
                    file=sys.stderr,
                )
                sys.exit(exit_code)

            # Compute delta (new/changed files)
            after = _snapshot(rootfs)
            changed = _diff_snapshots(before, after, rootfs)

            if changed:
                tar_data     = make_delta_tar(changed)
                layer_digest = digest_bytes(tar_data)
            else:
                # Empty layer (command had no fs effect)
                tar_data     = make_delta_tar([])
                layer_digest = digest_bytes(tar_data)

        self.store.write_layer(layer_digest, tar_data)
        if not self.no_cache:
            self.store.cache_set(cache_key, layer_digest)
        self.cache_cascade = True   # any miss cascades all subsequent steps

        elapsed = time.monotonic() - t0
        print(f" [CACHE MISS] {elapsed:.2f}s")
        self._record_layer(layer_digest, f"RUN {instr.raw_args}")

    def _exec_workdir(self, instr: Instruction):
        self.workdir = instr.workdir

    def _exec_env(self, instr: Instruction):
        self.env[instr.env_key] = instr.env_val

    def _exec_cmd(self, instr: Instruction):
        self.cmd_list = instr.cmd_list

    # ── helpers ───────────────────────────────────────────────────────────────

    def _cache_lookup(self, cache_key: str) -> Tuple[bool, str]:
        """Return (hit, layer_digest). A hit requires both a cache entry AND the layer file."""
        if self.no_cache or self.cache_cascade:
            self.cache_cascade = True
            return False, ""
        stored = self.store.cache_get(cache_key)
        if stored and self.store.layer_exists(stored):
            return True, stored
        # Miss (stale entry or missing layer file → cascade)
        self.cache_cascade = True
        return False, ""

    def _record_layer(self, digest: str, created_by: str):
        size = self.store.layer_path(digest).stat().st_size if self.store.layer_exists(digest) else 0
        self.layers.append({"digest": digest, "size": size, "createdBy": created_by})
        self.prev_digest = digest

    def _collect_copy_sources(self, instr: Instruction) -> List[Tuple[str, str]]:
        """Expand glob patterns and return list of (rel_path_from_context, abs_path)."""
        import glob as _glob
        context = Path(self.context_dir)
        results: Dict[str, str] = {}

        for pattern in instr.copy_srcs:
            abs_pattern = str(context / pattern)
            hits = _glob.glob(abs_pattern, recursive=True)
            if not hits:
                print(f"Warning: COPY pattern '{pattern}' matched nothing.", file=sys.stderr)
                continue
            for hit in hits:
                hp = Path(hit)
                if hp.is_file():
                    rel = str(hp.relative_to(context))
                    results[rel] = str(hp)
                elif hp.is_dir():
                    for root, dirs, files in os.walk(hp):
                        dirs.sort()
                        for fn in sorted(files):
                            fp = Path(root) / fn
                            rel = str(fp.relative_to(context))
                            results[rel] = str(fp)

        return sorted(results.items(), key=lambda x: x[0])

    def _map_copy_dest(
        self,
        file_pairs: List[Tuple[str, str]],
        dest: str,
    ) -> List[Tuple[str, str]]:
        """
        Map (rel_src, abs_src) pairs to (arcname_in_layer, abs_src).

        Rules (mirrors Docker COPY semantics):
          - dest ends with /  →  always treat as directory; preserve relative src paths
          - single file, dest does NOT end with /  →  dest is the exact filename
          - multiple sources  →  dest must be a directory; preserve relative src paths
        """
        dest_clean = dest.lstrip("/")   # tar arcnames must be relative
        multi = len(file_pairs) > 1
        mapped = []

        for rel_src, abs_src in file_pairs:
            if dest.endswith("/") or multi:
                # Preserve the relative path under dest
                arcname = os.path.join(dest_clean, rel_src) if dest_clean else rel_src
            else:
                # Single file → rename to dest exactly
                arcname = dest_clean or Path(rel_src).name

            # Normalise double-slashes / leading dots
            arcname = os.path.normpath(arcname).lstrip("/")
            mapped.append((arcname, abs_src))

        return mapped

    def _write_manifest(self) -> dict:
        """Compute manifest digest and save to disk."""
        env_list = [f"{k}={v}" for k, v in sorted(self.env.items())]
        config: dict = {}
        if env_list:
            config["Env"] = env_list
        if self.cmd_list:
            config["Cmd"] = self.cmd_list
        if self.workdir:
            config["WorkingDir"] = self.workdir

        # Determine created timestamp:
        # If ALL layer steps were cache hits, reuse original created value.
        created = self._original_created() or datetime.now(timezone.utc).isoformat()

        manifest = {
            "name":    self.name,
            "tag":     self.tag,
            "digest":  "",           # placeholder for hash computation
            "created": created,
            "config":  config,
            "layers":  self.layers,
        }
        # Compute digest: serialise with digest="", hash, write back
        canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
        h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        manifest["digest"] = f"sha256:{h}"

        self.store.save_manifest(manifest)
        return manifest

    def _original_created(self) -> Optional[str]:
        """Return the existing created timestamp if the image already exists (for pure cache-hit rebuilds)."""
        if self.store.image_exists(self.name, self.tag):
            try:
                old = self.store.load_manifest(self.name, self.tag)
                return old.get("created")
            except Exception:
                pass
        return None


# ── filesystem snapshot helpers ───────────────────────────────────────────────

def _snapshot(rootfs: str) -> Dict[str, Tuple[float, int]]:
    """Walk rootfs and record {rel_path: (mtime, size)} for all regular files."""
    snap = {}
    root = Path(rootfs)
    for dirpath, dirnames, filenames in os.walk(rootfs):
        dirnames.sort()
        dp = Path(dirpath)
        for fn in filenames:
            fp = dp / fn
            rel = str(fp.relative_to(root))
            try:
                st = fp.stat()
                snap[rel] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return snap


def _diff_snapshots(
    before: Dict[str, Tuple[float, int]],
    after:  Dict[str, Tuple[float, int]],
    rootfs: str,
) -> List[Tuple[str, str]]:
    """Return (arcname, abs_path) pairs for files that are new or changed."""
    root = Path(rootfs)
    changed = []
    for rel, (mtime, size) in sorted(after.items()):
        if rel not in before or before[rel] != (mtime, size):
            abs_path = str(root / rel)
            changed.append((rel, abs_path))
    return changed
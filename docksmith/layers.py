"""
Layer utilities
  make_delta_tar(files)              → deterministic tar bytes
  digest_bytes(data)                 → "sha256:<hex>"
  digest_file(path)                  → "sha256:<hex>"
  extract_layer(tar_bytes, dest_dir) → None
  extract_layers(store, digests, dest_dir) → None
"""

import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path
from typing import List, Tuple

SKIP_PREFIXES = ("proc/", "sys/", "dev/", "run/", "tmp/")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _zero_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def make_delta_tar(files: List[Tuple[str, str]]) -> bytes:
    """
    Build a deterministic tar from (arcname, src_path) pairs.
    Sorted by arcname. Parent directories synthesised. Timestamps zeroed.
    """
    buf = io.BytesIO()
    seen_dirs: set = set()
    sorted_files = sorted(files, key=lambda x: x[0])

    with tarfile.open(fileobj=buf, mode="w:") as tf:

        def _ensure_dir(arc_dir: str):
            if arc_dir in seen_dirs or not arc_dir or arc_dir in (".", "/"):
                return
            parent = str(Path(arc_dir).parent)
            if parent != arc_dir:
                _ensure_dir(parent)
            info = tarfile.TarInfo(name=arc_dir)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            _zero_tarinfo(info)
            tf.addfile(info)
            seen_dirs.add(arc_dir)

        for arcname, src_path in sorted_files:
            src = Path(src_path)
            parent_arc = str(Path(arcname).parent)
            _ensure_dir(parent_arc)

            try:
                info = tf.gettarinfo(str(src), arcname=arcname)
            except Exception as e:
                print(f"Warning: skipping {src}: {e}", file=sys.stderr)
                continue
            _zero_tarinfo(info)

            if info.isreg():
                with open(src, "rb") as fh:
                    tf.addfile(info, fh)
            elif info.isdir():
                if arcname not in seen_dirs:
                    seen_dirs.add(arcname)
                    tf.addfile(info)
            else:
                tf.addfile(info)

    return buf.getvalue()


def extract_layer(tar_data: bytes, dest_dir: str):
    """Extract a tar layer into dest_dir. Later layers overwrite earlier ones."""
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO(tar_data)
    with tarfile.open(fileobj=buf, mode="r:*") as tf:
        safe_members = []
        for m in tf.getmembers():
            # Sanitise path
            m.name = m.name.lstrip("./").lstrip("/")
            if not m.name:
                continue
            if ".." in Path(m.name).parts:
                continue
            # Skip virtual filesystem paths
            if any(m.name.startswith(p) for p in SKIP_PREFIXES):
                continue
            safe_members.append(m)
        tf.extractall(path=dest_dir, members=safe_members)


def extract_layers(store, layer_digests: List[str], dest_dir: str):
    """Extract all image layers in order into dest_dir."""
    for digest in layer_digests:
        if not store.layer_exists(digest):
            print(f"Error: layer {digest[:19]}... not found on disk.", file=sys.stderr)
            sys.exit(1)
        extract_layer(store.read_layer(digest), dest_dir)

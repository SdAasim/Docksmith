"""
Build cache key computation.

A cache key is the SHA-256 of a deterministic serialisation of:
  - previous layer digest  (or base image manifest digest for first layer-producing step)
  - instruction text (full, as written)
  - current WORKDIR value
  - sorted ENV key=value pairs
  - COPY only: sorted (path, sha256) pairs of source files
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .layers import digest_file


def compute_cache_key(
    prev_digest: str,
    instruction_text: str,
    workdir: str,
    env_state: Dict[str, str],
    copy_file_digests: Optional[List[tuple]] = None,   # [(rel_path, sha256), ...]
) -> str:
    parts: List[str] = [
        prev_digest,
        instruction_text,
        workdir,
        _serialize_env(env_state),
    ]
    if copy_file_digests:
        # Sort by path, then concatenate path=digest
        for rel_path, fdigest in sorted(copy_file_digests, key=lambda x: x[0]):
            parts.append(f"{rel_path}={fdigest}")

    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialize_env(env: Dict[str, str]) -> str:
    if not env:
        return ""
    return ";".join(f"{k}={env[k]}" for k in sorted(env))


def collect_copy_file_digests(
    context_dir: str,
    src_patterns: List[str],
) -> List[tuple]:
    """
    Expand glob patterns relative to context_dir.
    Return sorted list of (rel_path, sha256_digest).
    """
    import glob as _glob
    context = Path(context_dir)
    matched: Dict[str, str] = {}   # rel_path → digest

    for pattern in src_patterns:
        # Support both * and ** globs
        abs_pattern = str(context / pattern)
        hits = _glob.glob(abs_pattern, recursive=True)
        if not hits:
            # Try relative
            hits = _glob.glob(pattern, recursive=True)
        for hit in hits:
            hp = Path(hit)
            if hp.is_file():
                rel = str(hp.relative_to(context))
                matched[rel] = digest_file(hp)
            elif hp.is_dir():
                for root, dirs, files in os.walk(hp):
                    dirs.sort()
                    for fn in sorted(files):
                        fp = Path(root) / fn
                        rel = str(fp.relative_to(context))
                        matched[rel] = digest_file(fp)

    return sorted(matched.items(), key=lambda x: x[0])
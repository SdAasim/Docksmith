"""
Base image importer — `docksmith import <tar> <name:tag>`

Supports:
  - Docker-saved tars (docker save)
  - Raw rootfs tars (alpine-minirootfs-*.tar.gz)  ← no Docker needed
"""

import hashlib
import io
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .layers import digest_bytes
from .store import ImageStore


def import_image(tar_path: str, name: str, tag: str):
    store = ImageStore()
    print(f"Importing {tar_path} as {name}:{tag} ...")

    # Detect format: Docker-saved tar has manifest.json at root
    is_docker_format = _has_docker_manifest(tar_path)

    if is_docker_format:
        imported_layers, config_out = _import_docker_tar(tar_path, store)
    else:
        # Raw rootfs tar (e.g. alpine-minirootfs-3.18.0-x86_64.tar.gz)
        imported_layers, config_out = _import_rootfs_tar(tar_path, store)

    manifest = {
        "name":    name,
        "tag":     tag,
        "digest":  "",
        "created": datetime.now(timezone.utc).isoformat(),
        "config":  config_out,
        "layers":  imported_layers,
    }

    canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest["digest"] = f"sha256:{h}"

    store.save_manifest(manifest)
    short = manifest["digest"].replace("sha256:", "")[:12]
    print(f"Imported {name}:{tag} → sha256:{short}")


def _has_docker_manifest(tar_path: str) -> bool:
    """Check if tar contains manifest.json (Docker format)."""
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            names = tf.getnames()
            return "manifest.json" in names
    except Exception:
        return False


def _import_docker_tar(tar_path: str, store: ImageStore):
    """Import a docker save tar."""
    with tarfile.open(tar_path, "r:*") as outer:
        mf_member = outer.getmember("manifest.json")
        manifest_data = json.loads(outer.extractfile(mf_member).read())
        image_entry   = manifest_data[0]
        layer_paths   = image_entry.get("Layers", [])

        # Read config
        config_data, container_cfg = {}, {}
        config_file = image_entry.get("Config", "")
        if config_file:
            try:
                cfg_member  = outer.getmember(config_file)
                config_data = json.loads(outer.extractfile(cfg_member).read())
                container_cfg = config_data.get("config", config_data.get("Config", {}))
            except Exception:
                pass

        env_list = container_cfg.get("Env", []) or []
        cmd_list = container_cfg.get("Cmd", []) or []
        workdir  = container_cfg.get("WorkingDir", "") or ""

        imported_layers = []
        for i, layer_path in enumerate(layer_paths):
            print(f"  Layer {i+1}/{len(layer_paths)}: {layer_path}")
            lm = None
            for candidate in [layer_path, layer_path.replace("/layer.tar", ".tar")]:
                try:
                    lm = outer.getmember(candidate)
                    break
                except KeyError:
                    continue
            if lm is None:
                print(f"  Warning: layer '{layer_path}' not found, skipping.", file=sys.stderr)
                continue

            tar_data   = outer.extractfile(lm).read()
            normalised = _normalise_layer_tar(tar_data)
            layer_digest = digest_bytes(normalised)

            if not store.layer_exists(layer_digest):
                store.write_layer(layer_digest, normalised)
                print(f"    Written {layer_digest[:19]}... ({len(normalised)} bytes)")
            else:
                print(f"    Already exists {layer_digest[:19]}...")

            imported_layers.append({
                "digest":    layer_digest,
                "size":      len(normalised),
                "createdBy": f"imported layer {i+1}",
            })

    config_out = {}
    if env_list: config_out["Env"] = env_list
    if cmd_list: config_out["Cmd"] = cmd_list
    if workdir:  config_out["WorkingDir"] = workdir

    return imported_layers, config_out


def _import_rootfs_tar(tar_path: str, store: ImageStore):
    """
    Import a raw rootfs tar directly (no Docker needed).
    Works with alpine-minirootfs-*.tar.gz and similar.
    """
    print("  Detected raw rootfs tar (no Docker format) — importing directly...")

    with open(tar_path, "rb") as f:
        raw = f.read()

    normalised   = _normalise_layer_tar(raw)
    layer_digest = digest_bytes(normalised)

    if not store.layer_exists(layer_digest):
        store.write_layer(layer_digest, normalised)
        print(f"  Written {layer_digest[:19]}... ({len(normalised)} bytes)")
    else:
        print(f"  Already exists {layer_digest[:19]}...")

    imported_layers = [{
        "digest":    layer_digest,
        "size":      len(normalised),
        "createdBy": "imported rootfs layer",
    }]

    config_out = {
        "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
        "Cmd": ["/bin/sh"],
        "WorkingDir": "",
    }

    return imported_layers, config_out


def _normalise_layer_tar(raw: bytes) -> bytes:
    """
    Repack a tar into Docksmith's deterministic format:
      - entries sorted by name
      - timestamps zeroed, uid/gid zeroed
      - whiteout files skipped
      - proc/sys/dev entries skipped
    """
    SKIP_PREFIXES = ("proc/", "sys/", "dev/", "run/", "./proc/", "./sys/", "./dev/", "./run/")
    buf_in = io.BytesIO(raw)
    members = []

    try:
        with tarfile.open(fileobj=buf_in, mode="r:*") as tf_in:
            for m in tf_in.getmembers():
                # Normalise name
                m.name = m.name.lstrip("./").lstrip("/")
                if not m.name:
                    continue
                # Skip virtual filesystems
                if any(m.name.startswith(p) for p in SKIP_PREFIXES):
                    continue
                # Skip whiteout files (Docker layer deletion markers)
                base = os.path.basename(m.name)
                if base.startswith(".wh."):
                    continue
                data = None
                if m.isreg():
                    try:
                        data = tf_in.extractfile(m).read()
                    except Exception:
                        data = b""
                members.append((m, data))
    except tarfile.TarError:
        return raw

    members.sort(key=lambda x: x[0].name)

    buf_out = io.BytesIO()
    with tarfile.open(fileobj=buf_out, mode="w:") as tf_out:
        for m, data in members:
            m.mtime = m.uid = m.gid = 0
            m.uname = m.gname = ""
            if data is not None:
                tf_out.addfile(m, io.BytesIO(data))
            else:
                tf_out.addfile(m)

    return buf_out.getvalue()

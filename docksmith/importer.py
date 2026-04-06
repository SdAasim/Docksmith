"""
Base image importer — `docksmith import <image.tar> <name:tag>`

Reads a Docker-exported image tar (produced by `docker save`) and
imports it into the local Docksmith store.

Usage (one-time setup):
  docker pull alpine:3.18
  docker save alpine:3.18 -o alpine.tar
  python -m docksmith import alpine.tar alpine:3.18
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

    with tarfile.open(tar_path, "r:*") as outer:
        # Read manifest.json to find layer order
        try:
            mf_member = outer.getmember("manifest.json")
        except KeyError:
            print("Error: not a valid Docker image tar (missing manifest.json)", file=sys.stderr)
            sys.exit(1)

        manifest_data = json.loads(outer.extractfile(mf_member).read())
        if not manifest_data:
            print("Error: manifest.json is empty", file=sys.stderr)
            sys.exit(1)

        image_entry = manifest_data[0]
        layer_paths: List[str] = image_entry.get("Layers", [])

        # Read image config for ENV/CMD/WorkingDir
        config_file = image_entry.get("Config", "")
        config_data = {}
        if config_file:
            try:
                cfg_member = outer.getmember(config_file)
                config_data = json.loads(outer.extractfile(cfg_member).read())
            except (KeyError, json.JSONDecodeError):
                pass

        container_cfg = config_data.get("config", config_data.get("Config", {}))
        env_list  = container_cfg.get("Env", []) or []
        cmd_list  = container_cfg.get("Cmd", []) or []
        workdir   = container_cfg.get("WorkingDir", "") or ""

        # Import each layer
        imported_layers = []
        for i, layer_path in enumerate(layer_paths):
            print(f"  Layer {i+1}/{len(layer_paths)}: {layer_path}")
            try:
                lm = outer.getmember(layer_path)
            except KeyError:
                # Some exporters use different path formats
                layer_path_alt = layer_path.replace("/layer.tar", ".tar")
                try:
                    lm = outer.getmember(layer_path_alt)
                except KeyError:
                    print(f"  Warning: layer '{layer_path}' not found, skipping.", file=sys.stderr)
                    continue

            tar_data = outer.extractfile(lm).read()

            # Re-export as a normalised tar (sort + zero timestamps)
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

    # Build manifest
    config_out: dict = {}
    if env_list:
        config_out["Env"] = env_list
    if cmd_list:
        config_out["Cmd"] = cmd_list
    if workdir:
        config_out["WorkingDir"] = workdir

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


def _normalise_layer_tar(raw: bytes) -> bytes:
    """
    Re-pack a Docker layer tar into Docksmith's deterministic format:
      - entries sorted by name
      - timestamps zeroed
      - uid/gid zeroed
    """
    buf_in  = io.BytesIO(raw)
    buf_out = io.BytesIO()

    members = []
    try:
        with tarfile.open(fileobj=buf_in, mode="r:*") as tf_in:
            for m in tf_in.getmembers():
                # Read file data if regular
                data = None
                if m.isreg():
                    try:
                        data = tf_in.extractfile(m).read()
                    except Exception:
                        data = b""
                members.append((m, data))
    except tarfile.TarError as e:
        # Return raw if we cannot parse
        return raw

    # Sort by name
    members.sort(key=lambda x: x[0].name)

    with tarfile.open(fileobj=buf_out, mode="w:") as tf_out:
        for m, data in members:
            m.mtime  = 0
            m.uid    = 0
            m.gid    = 0
            m.uname  = ""
            m.gname  = ""
            # Skip whiteout files (Docker deletion markers) — simplification
            if m.name.startswith(".wh.") or "/.wh." in m.name:
                continue
            if data is not None:
                tf_out.addfile(m, io.BytesIO(data))
            else:
                tf_out.addfile(m)

    return buf_out.getvalue()
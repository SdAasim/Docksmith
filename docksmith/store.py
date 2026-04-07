"""
ImageStore — manages ~/.docksmith/
  images/  — JSON manifests
  layers/  — content-addressed tar files (named by sha256 hex)
  cache/   — cache key → layer digest index
"""

import json
import os
import sys
from pathlib import Path

# Use DOCKSMITH_HOME env var if set — fixes sudo vs normal user home mismatch
_default = Path(os.environ.get("SUDO_USER") and f"/home/{os.environ['SUDO_USER']}" or Path.home()) / ".docksmith"
DOCKSMITH_HOME = Path(os.environ.get("DOCKSMITH_HOME", str(_default)))
IMAGES_DIR  = DOCKSMITH_HOME / "images"
LAYERS_DIR  = DOCKSMITH_HOME / "layers"
CACHE_DIR   = DOCKSMITH_HOME / "cache"


def ensure_dirs():
    for d in (IMAGES_DIR, LAYERS_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


class ImageStore:
    def __init__(self):
        ensure_dirs()

    # ── manifests ──────────────────────────────────────────────────────────────

    def _manifest_path(self, name: str, tag: str) -> Path:
        safe = f"{name}_{tag}".replace("/", "_").replace(":", "_")
        return IMAGES_DIR / f"{safe}.json"

    def save_manifest(self, manifest: dict):
        self._manifest_path(manifest["name"], manifest["tag"]).write_text(
            json.dumps(manifest, indent=2)
        )

    def load_manifest(self, name: str, tag: str) -> dict:
        p = self._manifest_path(name, tag)
        if not p.exists():
            print(f"Error: image '{name}:{tag}' not found in local store.", file=sys.stderr)
            sys.exit(1)
        return json.loads(p.read_text())

    def image_exists(self, name: str, tag: str) -> bool:
        return self._manifest_path(name, tag).exists()

    def list_images(self) -> list:
        images = []
        for p in sorted(IMAGES_DIR.glob("*.json")):
            try:
                images.append(json.loads(p.read_text()))
            except Exception:
                pass
        return images

    def remove_image(self, name: str, tag: str):
        p = self._manifest_path(name, tag)
        if not p.exists():
            print(f"Error: image '{name}:{tag}' not found.", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(p.read_text())
        p.unlink()
        for layer in manifest.get("layers", []):
            lp = self.layer_path(layer.get("digest", ""))
            if lp.exists():
                lp.unlink()
                print(f"  Deleted layer {layer['digest'][:19]}...")

    # ── layers ─────────────────────────────────────────────────────────────────

    def layer_path(self, digest: str) -> Path:
        return LAYERS_DIR / digest.replace("sha256:", "")

    def layer_exists(self, digest: str) -> bool:
        return self.layer_path(digest).exists()

    def write_layer(self, digest: str, data: bytes):
        self.layer_path(digest).write_bytes(data)

    def read_layer(self, digest: str) -> bytes:
        return self.layer_path(digest).read_bytes()

    # ── cache ──────────────────────────────────────────────────────────────────

    def cache_get(self, key: str):
        p = CACHE_DIR / key
        return p.read_text().strip() if p.exists() else None

    def cache_set(self, key: str, digest: str):
        (CACHE_DIR / key).write_text(digest)

#!/bin/bash
# ============================================================
# setup_base_image.sh
#
# Run ONCE before any builds to import the Alpine base image
# into the Docksmith local store.
#
# Requirements:
#   - Docker installed (only needed for this one-time step)
#   - Python 3.8+ with docksmith installed (pip install -e .)
#
# After this script, Docker is no longer needed.
# All docksmith operations work fully offline.
# ============================================================

set -e

IMAGE_NAME="alpine"
IMAGE_TAG="3.18"
TAR_FILE="/tmp/alpine_3.18.tar"

echo "=== Docksmith Base Image Setup ==="
echo ""

# Pull the alpine image from Docker Hub
echo "[1/3] Pulling ${IMAGE_NAME}:${IMAGE_TAG} via Docker..."
docker pull "${IMAGE_NAME}:${IMAGE_TAG}"

# Save it as a tar archive
echo "[2/3] Saving to ${TAR_FILE}..."
docker save "${IMAGE_NAME}:${IMAGE_TAG}" -o "${TAR_FILE}"

# Import into Docksmith's local store
echo "[3/3] Importing into Docksmith store (~/.docksmith/)..."
python -m docksmith import "${TAR_FILE}" "${IMAGE_NAME}:${IMAGE_TAG}"

# Clean up the tar
rm -f "${TAR_FILE}"

echo ""
echo "=== Setup complete! ==="
echo "You can now use: FROM alpine:3.18"
echo "Verify with:     python -m docksmith images"
echo ""
echo "Docker is no longer needed. All builds work offline."

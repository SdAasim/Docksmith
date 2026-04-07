#!/bin/bash
# ============================================================
# demo.sh — Full Docksmith demo covering all 8 spec scenarios
#
# Run from the repo root after: pip install -e .
# and after running: bash setup_base_image.sh
# ============================================================

set -e
DOCKSMITH="python -m docksmith"
CONTEXT="./sample_app"
TAG="myapp:latest"

banner() { echo; echo "══════════════════════════════════════════"; echo "  $1"; echo "══════════════════════════════════════════"; echo; }

# ── Demo 1: Cold build ────────────────────────────────────────────────────────
banner "Demo 1: Cold build (all CACHE MISS)"
$DOCKSMITH rmi $TAG 2>/dev/null || true
$DOCKSMITH build -t $TAG --no-cache $CONTEXT

# ── Demo 2: Warm build ────────────────────────────────────────────────────────
banner "Demo 2: Warm build (all CACHE HIT)"
$DOCKSMITH build -t $TAG $CONTEXT

# ── Demo 3: Partial cache invalidation ───────────────────────────────────────
banner "Demo 3: Edit source file → partial cache miss"
echo "# modified at $(date)" >> $CONTEXT/run.sh
$DOCKSMITH build -t $TAG $CONTEXT
# Restore
git checkout -- $CONTEXT/run.sh 2>/dev/null || sed -i '$ d' $CONTEXT/run.sh

# ── Demo 4: List images ───────────────────────────────────────────────────────
banner "Demo 4: docksmith images"
$DOCKSMITH images

# ── Demo 5: Run container ─────────────────────────────────────────────────────
banner "Demo 5: docksmith run (default CMD)"
sudo $DOCKSMITH run $TAG

# ── Demo 6: ENV override ──────────────────────────────────────────────────────
banner "Demo 6: docksmith run -e GREETING=Howdy"
sudo $DOCKSMITH run -e GREETING=Howdy $TAG

# ── Demo 7: Isolation check ───────────────────────────────────────────────────
banner "Demo 7: File isolation check"
echo "Running container (it will write /tmp/isolation_test.txt inside)..."
sudo $DOCKSMITH run $TAG
echo ""
echo "Checking host for /tmp/isolation_test.txt ..."
if [ -f /tmp/isolation_test.txt ]; then
    echo "FAIL: file found on host!"
    exit 1
else
    echo "PASS: file does NOT exist on host filesystem."
fi

# ── Demo 8: Remove image ──────────────────────────────────────────────────────
banner "Demo 8: docksmith rmi"
$DOCKSMITH rmi $TAG
$DOCKSMITH images

echo
echo "All demos complete."

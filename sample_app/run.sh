#!/bin/sh
# Sample Docksmith app entry point
# Demonstrates ENV override via -e GREETING=<value>

echo "========================================="
echo "  Docksmith Sample App v${APP_VERSION}"
echo "========================================="
echo ""
echo "${GREETING}, from inside the container!"
echo ""
echo "Environment:"
echo "  APP_VERSION = ${APP_VERSION}"
echo "  GREETING    = ${GREETING}"
echo "  WORKDIR     = $(pwd)"
echo "  HOSTNAME    = $(hostname)"
echo ""

# Write a file inside the container to prove isolation
echo "writing test file inside container..."
echo "container-only-data" > /tmp/isolation_test.txt
echo "  Wrote /tmp/isolation_test.txt (should NOT appear on host)"
echo ""
echo "Done. Container exiting cleanly."
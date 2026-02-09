#!/usr/bin/env bash
# generate_proto.sh — Regenerate gRPC stubs from proto definitions.
#
# Generates Python stubs into this service (core-fraud-detection) and,
# if present as a sibling directory, into core-banking as well.
#
# Usage:
#   bash scripts/generate_proto.sh          (from core-fraud-detection root)
#   make proto                              (from devstack)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
PROTO_DIR="$PROJECT_ROOT/proto"
LOCAL_OUT="$PROJECT_ROOT/app/grpc/generated"
BANKING_OUT="$PROJECT_ROOT/../core-banking/app/grpc/generated"

echo "Proto source: $PROTO_DIR"
echo "Local output: $LOCAL_OUT"

# Generate stubs for core-fraud-detection (server)
python -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$LOCAL_OUT" \
    --grpc_python_out="$LOCAL_OUT" \
    sentinel/fraud/v1/fraud_evaluation.proto

echo "Generated stubs for core-fraud-detection"

# Generate stubs for core-banking (client) if sibling directory exists
if [ -d "$BANKING_OUT" ]; then
    python -m grpc_tools.protoc \
        -I "$PROTO_DIR" \
        --python_out="$BANKING_OUT" \
        --grpc_python_out="$BANKING_OUT" \
        sentinel/fraud/v1/fraud_evaluation.proto
    echo "Generated stubs for core-banking"
else
    echo "WARN: core-banking not found at $BANKING_OUT — skipping client stubs"
fi

echo "Done."

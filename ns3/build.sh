#!/usr/bin/env bash
# Compile an ns-3 C++ source file inside the hpqv-eval container.
#
# Working invocation (verified in container, Ubuntu 24.04 ns3 3.41, aarch64):
#   g++ -std=c++17 "$1" -o "$2" -I/usr/include -lns3-core
#
# pkg-config is NOT installed in the image (no pkg-config binary), even
# though the ns3-*.pc files exist under /usr/lib/aarch64-linux-gnu/pkgconfig.
# The libns3-*.so files are named without a version suffix (libns3-core.so,
# not libns3.41-core.so), so plain `-lns3-<module>` links directly.
#
# Usage: build.sh <source.cc> <output_binary> [extra ns3 modules...]
set -euo pipefail

SRC="$1"
OUT="$2"
shift 2
MODULES=("core" "$@")

LDFLAGS=()
for m in "${MODULES[@]}"; do
  LDFLAGS+=("-lns3-$m")
done

g++ -std=c++17 "$SRC" -o "$OUT" -I/usr/include "${LDFLAGS[@]}"

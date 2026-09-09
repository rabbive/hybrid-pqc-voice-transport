# Linux environment for the hpqv evaluation stack (tc netem + capture + NS-3-ready).
# macOS cannot run tc netem; run everything eval-related in this container.
#
# Build:  docker build -t hpqv-eval ./eval
# Run  :  docker run --rm -it --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval
#         (NET_ADMIN is required for `tc netem`; -v mounts the repo at /work)
#
# Verified: `tc qdisc add dev lo root netem loss 10% delay 50ms` works with
# --cap-add=NET_ADMIN on Docker Desktop / Apple Silicon (aarch64).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# No system Python: Ubuntu 24.04 ships 3.12 and has no python3.11 package.
# uv installs and manages the pinned Python 3.11 itself (see .python-version).
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates \
      libssl-dev \
      iproute2 tshark \
      libopus0 libopus-dev \
      ns3 libns3-dev \
    && rm -rf /var/lib/apt/lists/*

# Build the liboqs C shared library into the image. Without it, liboqs-python
# tries to git-clone-and-compile liboqs at first import, which fails in a fresh
# container (SystemExit). Pre-installing to /usr/local/lib + ldconfig fixes it.
# Version 0.16.0 matches the liboqs-python 0.16 binding (no ABI-mismatch warning).
# OQS_MINIMAL_BUILD: compile ONLY the two algorithms hpqv uses. The full build
# compiles every PQC scheme (snova, uov, ...) — minutes of CPU and enough memory
# pressure to crash Docker Desktop. This keeps it small and fast.
RUN git clone --depth 1 --branch 0.16.0 https://github.com/open-quantum-safe/liboqs /tmp/liboqs \
    && cmake -S /tmp/liboqs -B /tmp/liboqs/build \
         -DBUILD_SHARED_LIBS=ON -DOQS_BUILD_ONLY_LIB=ON -DCMAKE_INSTALL_PREFIX=/usr/local \
         -DOQS_MINIMAL_BUILD="KEM_ml_kem_768;SIG_ml_dsa_65" \
    && cmake --build /tmp/liboqs/build --parallel 2 \
    && cmake --install /tmp/liboqs/build \
    && ldconfig \
    && rm -rf /tmp/liboqs

# uv for the same dependency workflow as the prototype
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Let uv manage the interpreter (downloads a standalone build).
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.11

WORKDIR /work

# The repo is bind-mounted at run time. `uv sync` builds the venv against 3.11.
# (liboqs-python compiles liboqs from source; build-essential + cmake cover it.)
CMD ["bash"]

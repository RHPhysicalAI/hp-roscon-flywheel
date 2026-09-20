# This project was developed with assistance from AI tools.
# Build context: src  (podman build -f src/dev-workspace/Dockerfile src)
# The context is src/ rather than this directory so the evaluation dashboard's own requirement files are
# what gets pre-installed: one list, no copy to drift. Dockerfile.dockerignore lets only those files through.
# arm64 only - the hub's architecture; the checksums below are for the arm64 assets.
# The base pulls without a registry login and already carries git, a POSIX shell, nohup, tar and openssl,
# which is all the Dev Spaces editor asks of a workspace image (it brings its own Node.js and libraries).
FROM registry.access.redhat.com/ubi9/python-312

# The release that completed the rehearsal task against the hub's model. Bump version and checksum together.
ARG OPENCODE_VERSION=1.18.23
ARG OPENCODE_SHA256=86d3afaf4e8784f9adab189be2a315c12b27ec40a04b70defbe70595c3cc7c65
# The exact ripgrep opencode would otherwise download on first use.
ARG RIPGREP_VERSION=15.1.0
ARG RIPGREP_SHA256=2b661c6ef508e902f388e9098d9c4c5aca72c87b55922d94abdba830b4dc885e

USER 0
RUN set -eu; \
    [ "$(uname -m)" = aarch64 ] || { echo "arm64 only: the pinned checksums are for the arm64 assets" >&2; exit 1; }; \
    cd /tmp; \
    curl -fsSL -o opencode.tar.gz \
        "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-arm64.tar.gz"; \
    curl -fsSL -o rg.tar.gz \
        "https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/ripgrep-${RIPGREP_VERSION}-aarch64-unknown-linux-gnu.tar.gz"; \
    printf '%s  %s\n' "$OPENCODE_SHA256" opencode.tar.gz "$RIPGREP_SHA256" rg.tar.gz | sha256sum -c -; \
    tar -xzf opencode.tar.gz --no-same-owner -C /usr/local/bin opencode; \
    tar -xzf rg.tar.gz --no-same-owner -C /usr/local/bin --strip-components=1 \
        "ripgrep-${RIPGREP_VERSION}-aarch64-unknown-linux-gnu/rg"; \
    rm -f opencode.tar.gz rg.tar.gz; \
    chmod 0755 /usr/local/bin/opencode /usr/local/bin/rg; \
    # The workspace runs as an arbitrary UID in group 0: HOME and the sources mount point are group-writable.
    mkdir -p /home/user /projects; \
    chgrp 0 /home/user /projects; \
    chmod g=u /home/user /projects; \
    # A sources volume may be owned by another UID than the one the workspace runs as.
    git config --system --add safe.directory '/projects/*'

COPY dev-workspace/opencode.jsonc /opt/opencode/opencode.jsonc
COPY eval-dashboard/requirements.txt eval-dashboard/requirements-dev.txt /opt/opencode/requirements/

USER 1001
RUN pip install --no-cache-dir -r /opt/opencode/requirements/requirements-dev.txt

# Nothing is fetched at workspace start or while the agent runs: no self-update, no model catalogue,
# no language-server or plugin installs. opencode keeps its state in the XDG directories; /tmp is
# writable whatever the UID and whether or not the root filesystem is read-only.
ENV HOME=/home/user \
    SHELL=/bin/bash \
    ASSISTANT_BASE_URL=http://assistant.flywheel.svc:8000/v1 \
    OPENCODE_CONFIG=/opt/opencode/opencode.jsonc \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
    XDG_CONFIG_HOME=/tmp/xdg/config \
    XDG_DATA_HOME=/tmp/xdg/data \
    XDG_STATE_HOME=/tmp/xdg/state \
    XDG_CACHE_HOME=/tmp/xdg/cache

WORKDIR /projects

# The editor is started inside this container after it is up, so the container only has to stay alive.
# bash rather than tail as PID 1: it reaps the helper processes the agent leaves behind, and it exits at
# once on SIGTERM, so stopping a workspace does not wait out the grace period.
CMD ["/bin/bash", "-c", "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"]

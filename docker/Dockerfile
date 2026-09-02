# SO-ARM101 Flywheel Producer
#
# Based on upstream ros-physical-ai/demos Dockerfile.
# Adds: rmw_zenoh_cpp, episode-emitter node, volume mounts for flywheel integration.
#
# Build:
#   docker buildx build -f docker/Dockerfile -t soarm-flywheel:latest . --load
#
# Run (headless sim):
#   docker run --rm -v /var/lib/episodes:/data/episodes soarm-flywheel:latest

ARG DISTRO=kilted
FROM ros:${DISTRO}

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

# ---- Common build tooling ----
RUN apt-get update \
  && apt-get upgrade -y \
  && apt-get install -y --no-install-recommends \
  ros-dev-tools \
  python3-colcon-common-extensions \
  python3-rosdep \
  python3-vcstool \
  git \
  build-essential \
  ros-${ROS_DISTRO}-rmw-zenoh-cpp \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---- Workspace layout ----
RUN mkdir -p /ws_pai/src
WORKDIR /ws_pai

RUN git clone --depth 1 https://github.com/ros-physical-ai/demos src/demos

# ---- External dependencies ----
RUN vcs import src < src/demos/pai.repos --recursive

RUN apt-get update \
  && rosdep update \
  && rosdep install --from-paths src --ignore-src --rosdistro "$ROS_DISTRO" -yir \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---- Build the workspace ----
RUN source /opt/ros/"$ROS_DISTRO"/setup.bash \
  && colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release

# ---- ML dependencies (LeRobot + PyTorch for ACT inference) ----
# CPU-only PyTorch for now — GPU inference added when VFIO passthrough is set up.
# scipy<2 required: LeRobot 0.5.x imports scipy.fftpack which SciPy 2.0 removed.
# Debian-installed Python packages lack RECORD files, blocking pip upgrades.
# Remove their .dist-info dirs so pip can freely install newer versions.
RUN find /usr/lib/python3/dist-packages -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true \
  && pip install --no-cache-dir --break-system-packages \
  torch torchvision torchaudio \
  "lerobot[async]==0.5.1" \
  "numpy>=2.0.0,<2.3" \
  "scipy>=1.11.0,<2" \
  opencv-python-headless \
  flask \
  && pip cache purge

# ---- Flywheel adapters ----
COPY src/episode-emitter/episode_emitter.py /ws_pai/episode_emitter.py
COPY src/camera-bridge/camera_bridge.py /ws_pai/camera_bridge.py

# ---- Cleanup ----
RUN find /ws_pai -type d -name ".git" -exec rm -rf {} + 2>/dev/null || true

# ---- Runtime environment ----
ENV RMW_IMPLEMENTATION=rmw_zenoh_cpp

# ---- Entrypoint ----
# Launches: zenoh router + Gazebo sim (headless) + episode emitter
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

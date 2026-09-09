#!/bin/bash
# This project was developed with assistance from AI tools.
#
# Desktop host flow (D019, D043). Versioned copy of ~/assemble_all.sh on the desktop; the host runs
# its home-dir copy, this file is the source of record. Pair with after_assemble.sh (port-as-you-go).
#
# Port ALL curated teacher successes (from MinIO's curated selection) into one LeRobot dataset
# and push it to the hub (D019: the ported dataset is the canonical trainable artifact). Loop parked.
DS=flywheel-teacher-all-2026-09-08
LOG=$HOME/assemble-all.log
# MinIO credentials come from ~/.minio-env (mode 600): MINIO_ACCESS_KEY / MINIO_SECRET_KEY.
[ -r $HOME/.minio-env ] || { echo "$(date +%m-%d\ %H:%M) [assemble-all] ~/.minio-env missing — abort" >> $LOG; exit 1; }
set -a; source $HOME/.minio-env; set +a
echo "$(date +%m-%d\ %H:%M) [assemble-all] START -> $DS" >> $LOG
docker rm -f assemble-all >/dev/null 2>&1
docker run --rm --name assemble-all --network host --entrypoint bash \
  -e MINIO_ACCESS_KEY -e MINIO_SECRET_KEY \
  -v $HOME/flywheel-data:/flywheel act-inference:latest -lc "
  source /opt/ros/\$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash
  C=\$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml
  python3 /ws_pai/assemble_dataset.py --from-minio --model-version upstream-act-teacher \
    --bags-root /flywheel/bags --contract \"\$C\" --root /flywheel/datasets --repo-id $DS \
    --vcodec h264 --push-dataset" 2>&1 | grep -E "pulled|curated episode|episodes processed|done ->|pushed|manifest|rror" >> $LOG
[ -f $HOME/flywheel-data/datasets/$DS/meta/info.json ] && echo "$(date +%m-%d\ %H:%M) [assemble-all] DONE" >> $LOG || echo "$(date +%m-%d\ %H:%M) [assemble-all] FAILED" >> $LOG

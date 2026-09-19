#!/bin/bash
# Put the serving model's checkpoint where a training run looks for its incumbent: the pipeline fine-tunes FROM
# it and evaluates the candidate AGAINST it, and asks for it as
#   s3://episodes-data/checkpoints/<model version>/pretrained_model.tar.gz
# This hub's MinIO starts empty. The checkpoint itself is already on this machine, inside the signed modelcar
# image the Fleet pins (models/act/*): this re-packs it under pretrained_model/, the layout the runner unpacks,
# and uploads it. Nothing is downloaded and nothing is retrained.
#
#   ./52-seed-incumbent.sh            skips the upload if the object is already there
#   ./52-seed-incumbent.sh force      replace it
#
# Runs the pinned runtime image (it carries boto3) with the modelcar mounted as an image, and takes the two
# MinIO keys from the runner's own env file - so ./50-runner-install.sh comes first. The keys are never printed.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

rt=quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837
car=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
mv=act-v2-ft160                                  # MODEL_VERSION in the Fleet, INCUMBENT in the manifest consumer
envf=/etc/flywheel-runner/env
endpoint=http://10.20.0.10:30900
bucket=episodes-data
die() { echo "${0##*/}: $*" >&2; exit 1; }

[[ -s $envf ]]             || die "no $envf - ./50-runner-install.sh first (it asks for the two MinIO keys)"
podman image exists "$rt"  || die "the runtime image is not in root's storage: sudo podman pull $rt"
podman image exists "$car" || die "the modelcar image is not in root's storage: sudo podman pull $car"

read -r -d '' py <<'PYEOF' || true
import io, os, sys, tarfile
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

src, bucket, key, force = "/modelcar/models/act", os.environ["BUCKET"], os.environ["KEY"], os.environ["FORCE"] == "yes"
if not os.path.isfile(f"{src}/model.safetensors"):
    sys.exit(f"no model.safetensors under {src} in the modelcar")
s3 = boto3.client("s3", endpoint_url=os.environ["ENDPOINT"], aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
                  config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"))
try:
    s3.head_bucket(Bucket=bucket)
except ClientError as e:
    if e.response["Error"].get("Code") not in ("404", "NoSuchBucket", "NotFound"):
        sys.exit(f"bucket {bucket} is not reachable with these keys: {e.response['Error'].get('Code')}")
    s3.create_bucket(Bucket=bucket)   # the dataset assembler would create it on its first push; this comes first
    print(f"created bucket {bucket}")
try:
    have = s3.head_object(Bucket=bucket, Key=key)
    if not force:
        print(f"already there: s3://{bucket}/{key} ({have['ContentLength']} bytes) - 'force' to replace it")
        sys.exit(0)
except ClientError as e:
    if e.response["Error"].get("Code") not in ("404", "NoSuchKey", "NotFound"):
        raise
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    t.add(src, arcname="pretrained_model")
size = buf.tell(); buf.seek(0)
s3.upload_fileobj(buf, bucket, key)
names = sorted(os.listdir(src))
print(f"uploaded s3://{bucket}/{key}: {size} bytes, {len(names)} files under pretrained_model/ ({', '.join(names)})")
PYEOF

date -u
# the modelcar is a data-only image built for another architecture: mounted, never run
podman run --rm --pull=never --network host --env-file "$envf" \
    -e ENDPOINT="$endpoint" -e BUCKET="$bucket" -e KEY="checkpoints/$mv/pretrained_model.tar.gz" \
    -e FORCE="$([[ ${1:-} == force ]] && echo yes || echo no)" \
    --mount "type=image,source=$car,destination=/modelcar" \
    --entrypoint python3 "$rt" -c "$py"

# This project was developed with assistance from AI tools.
"""prune_bags.py [--yes] — bag retention (D021 incident resolution; D043 port-as-you-go).

Versioned copy of ~/prune_bags.py on the desktop; after_assemble.sh runs it inside the
act-inference image with ~/flywheel-data mounted at /flywheel.

Classifies every bag dir under /flywheel/bags:
  proof     name-timestamp < CUTOFF (present when the 160 ladder assembled)     -> KEEP (re-port hedge)
  ported    episode is in the pushed dataset manifest (Kafka dataset-manifests) -> DELETE (trainable form is in the hub)
  unported  has a curated JSON in MinIO but is NOT in the manifest              -> KEEP (port it later; JSON arrived after the pull)
  orphan    no curated JSON in MinIO                                            -> KEEP (report; decide separately)
Dry-run prints counts/sizes. --yes deletes the 'ported' class only and lists each deleted bag.

Environment: MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required (no defaults; ~/.minio-env on the
desktop). Optional: DS, CUT, MINIO_URL, KAFKA_BOOTSTRAP, BAGS_ROOT.
"""
import os, sys, json, glob, shutil, boto3
from kafka import KafkaConsumer
DS = os.environ.get("DS", "flywheel-teacher-all-2026-09-08"); CUT = int(os.environ.get("CUT", "1788560700"))
MINIO_URL = os.environ.get("MINIO_URL", "http://10.0.0.49:30900")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "10.0.0.49:30903")
BAGS_ROOT = os.environ.get("BAGS_ROOT", "/flywheel/bags")
ak, sk = os.environ.get("MINIO_ACCESS_KEY"), os.environ.get("MINIO_SECRET_KEY")
if not ak or not sk:
    sys.exit("prune_bags: MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set (source ~/.minio-env); refusing to run")
s3 = boto3.client("s3", endpoint_url=MINIO_URL, aws_access_key_id=ak, aws_secret_access_key=sk)
# manifest: latest with dataset_id == DS
c = KafkaConsumer("dataset-manifests", bootstrap_servers=KAFKA_BOOTSTRAP, auto_offset_reset="earliest",
                  consumer_timeout_ms=8000, value_deserializer=lambda v: json.loads(v.decode()))
ported = set(); mf = None
for m in c:
    if m.value.get("dataset_id") == DS: mf = m.value; ported = set(mf.get("episode_ids", []))
c.close()
print(f"manifest for {DS}: {'found, ' + str(len(ported)) + ' episode_ids' if mf else 'NOT FOUND'}")
# curated JSONs -> episode_id -> bag name
ep2bag, bag2ep = {}, {}
for pg in s3.get_paginator("list_objects_v2").paginate(Bucket="episodes-curated", Prefix="upstream-act-teacher/"):
    for o in pg.get("Contents", []):
        if not o["Key"].endswith(".json"): continue
        ep = json.loads(s3.get_object(Bucket="episodes-curated", Key=o["Key"])["Body"].read())
        dp = ep.get("dataset_path")
        if dp:
            b = os.path.basename(str(dp).rstrip("/")); ep2bag[ep["episode_id"]] = b; bag2ep[b] = ep["episode_id"]
print(f"curated JSONs with a bag ref: {len(bag2ep)}")
def size(d):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)
cls = {"proof": [], "ported": [], "unported": [], "orphan": []}
for d in sorted(glob.glob(os.path.join(BAGS_ROOT, "*/"))):
    name = os.path.basename(d.rstrip("/"))
    try: sec = int(name.split("_")[0])
    except ValueError: sec = 0
    if sec < CUT: k = "proof"
    elif name in bag2ep and bag2ep[name] in ported: k = "ported"
    elif name in bag2ep: k = "unported"
    else: k = "orphan"
    cls[k].append(d)
for k, v in cls.items():
    print(f"{k:<9} {len(v):>4} bags  {sum(size(d) for d in v)/1e9:8.1f} GB")
if "--yes" in sys.argv:
    if not mf: print("refusing: no manifest"); sys.exit(1)
    n = 0
    for d in cls["ported"]:
        print(f"deleting {os.path.basename(d.rstrip('/'))}  {size(d)/1e9:.2f} GB")
        shutil.rmtree(d, ignore_errors=True); n += 1
    print(f"deleted {n} ported bags")
else:
    print("dry run — pass --yes to delete the 'ported' class")

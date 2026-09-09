# This project was developed with assistance from AI tools.
"""ACT flywheel promotion pipeline (KFP v2, RHOAI Data Science Pipelines) - D022, D025.

  trigger+wait -> gate -> package -> sign -> open_promotion_pr        (human merge = last gate)

The promotion PR edits the RHEM Fleet (modelcar digest + MODEL_VERSION) and the trigger's lineage
(manifest-consumer COLLECTOR/INCUMBENT/INCUMBENT_CHECKPOINT) in ONE commit; ResourceSync renders the
Fleet after the human merge and RHEM rolls it out to the device (D025).

Written for the in-cluster-GPU target. `mode="desktop"` is the [desktop shim]: train + eval are
delegated to the host runner over Kafka (training-triggers / training-results) and the pipeline
waits for the artifacts in MinIO. `mode="cluster"` runs them in-pod (GB10/GB300 - Phase 4).
Every artifact contract (checkpoint tar, eval_report.json, image digest) is identical in both.

Compile:  python pipeline/act_flywheel_pipeline.py  -> pipeline/act_flywheel_pipeline.yaml
Secrets expected in the DSP project namespace: hub-credentials (S3), quay-push (dockerconfigjson),
cosign-signing-key (cosign.key, cosign.pub, cosign.password -> COSIGN_PASSWORD), github-token (token).
"""
from typing import List

from kfp import dsl, compiler
from kfp import kubernetes as k8s

# Pinned tags (D022: no runtime :latest); bump deliberately. crane ls registry.access.redhat.com/ubi9/<name>
PY_IMG = "registry.access.redhat.com/ubi9/python-312:9.8-1788919789"
UBI_MICRO = "registry.access.redhat.com/ubi9/ubi-micro:9.8-1787778798"


@dsl.component(base_image=PY_IMG, packages_to_install=["kafka-python>=2.2,<4", "boto3==1.35.36"])
def trigger_and_wait(run_id: str, candidate: str, incumbent: str, collector: str,
                     incumbent_checkpoint: str, steps_per_frame: float, eval_n: int,
                     eval_seed_base: int, kafka_bootstrap: str, timeout_min: int,
                     eval_report: dsl.OutputPath(str), checkpoint: dsl.OutputPath(str)):
    """[desktop shim] publish a training trigger and wait for the host runner's result."""
    import json, sys, time
    from kafka import KafkaConsumer, KafkaProducer
    msg = dict(run_id=run_id, candidate=candidate, incumbent=incumbent, collector=collector,
               incumbent_checkpoint=incumbent_checkpoint, steps_per_frame=steps_per_frame,
               eval_n=eval_n, eval_seed_base=eval_seed_base)
    consumer = KafkaConsumer("training-results", bootstrap_servers=kafka_bootstrap,
                             group_id=f"pipeline-{run_id}", auto_offset_reset="latest",
                             consumer_timeout_ms=60_000,
                             value_deserializer=lambda v: json.loads(v.decode()))
    consumer.poll(1000)  # join the group before the trigger goes out
    p = KafkaProducer(bootstrap_servers=kafka_bootstrap, value_serializer=lambda v: json.dumps(v).encode())
    p.send("training-triggers", value=msg); p.flush()
    print("trigger sent:", msg, flush=True)
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        for m in consumer:
            if m.value.get("run_id") == run_id:
                res = m.value; print("result:", res, flush=True)
                if res.get("status") != "ok":
                    print("host runner failed:", res.get("message")); sys.exit(1)
                with open(eval_report, "w") as f: f.write(res["eval_report_uri"])
                with open(checkpoint, "w") as f: f.write(res["checkpoint_uri"])
                return
        print("waiting for training-results...", flush=True)
    print("timed out waiting for the host runner"); sys.exit(1)


@dsl.component(base_image=PY_IMG, packages_to_install=["boto3==1.35.36"])
def eval_gate(eval_report_uri: str, s3_endpoint: str) -> str:
    """Gate: candidate vs incumbent on the same fixed seeds. Promote iff net > 0 and p < 0.05 (D022).
    Fails the pipeline (sys.exit 1) otherwise - downstream never runs (thor-testing's hard-stop)."""
    import json, os, sys, boto3
    s3 = boto3.client("s3", endpoint_url=s3_endpoint, aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    b, k = eval_report_uri[5:].split("/", 1)
    rep = json.loads(s3.get_object(Bucket=b, Key=k)["Body"].read())
    print(json.dumps(rep, indent=1))
    ok = rep["net"] > 0 and rep["sign_test_p"] < 0.05
    print(f"GATE {'PASS' if ok else 'FAIL'}: {rep['incumbent']} {rep['incumbent_success_rate']:.2f} -> "
          f"{rep['candidate']} {rep['candidate_success_rate']:.2f}; fixed {rep['fixed']} broken {rep['broken']} "
          f"net {rep['net']:+d} p={rep['sign_test_p']}")
    if not ok: sys.exit(1)
    return json.dumps(rep)  # the report travels downstream as a parameter


@dsl.component(base_image=PY_IMG, packages_to_install=["boto3==1.35.36"])
def package_modelcar(checkpoint_uri: str, candidate: str, registry_repo: str, platform: List[str],
                     s3_endpoint: str, crane_version: str, modelcar_base: str) -> str:
    """crane append per platform: flat ACT checkpoint dir -> /models/act on ubi-micro, then one OCI index
    at the candidate tag (the model layer is shared; only the ubi-micro base differs). Returns index@digest."""
    import os, platform as _plat, subprocess, tarfile, io, boto3, urllib.request
    s3 = boto3.client("s3", endpoint_url=s3_endpoint, aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    b, k = checkpoint_uri[5:].split("/", 1)
    buf = io.BytesIO(s3.get_object(Bucket=b, Key=k)["Body"].read())
    os.makedirs("/tmp/layer/models/act", exist_ok=True)
    with tarfile.open(fileobj=buf, mode="r:gz") as t:
        for m in t:
            if m.isfile():
                m.name = "models/act/" + os.path.basename(m.name); t.extract(m, "/tmp/layer")
    with tarfile.open("/tmp/layer.tar", "w") as t: t.add("/tmp/layer/models", arcname="models")
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}[_plat.machine()]
    url = f"https://github.com/google/go-containerregistry/releases/download/{crane_version}/go-containerregistry_Linux_{arch}.tar.gz"
    urllib.request.urlretrieve(url, "/tmp/crane.tgz"); subprocess.run(["tar", "-xzf", "/tmp/crane.tgz", "-C", "/tmp", "crane"], check=True)
    # quay-push is a kubernetes.io/dockerconfigjson Secret: the mounted file is .dockerconfigjson,
    # while crane/cosign look for $DOCKER_CONFIG/config.json.
    import json as _j; os.makedirs("/tmp/docker", exist_ok=True)
    cfg = _j.load(open("/etc/quay/.dockerconfigjson"))
    _j.dump({"auths": cfg.get("auths", {})}, open("/tmp/docker/config.json", "w"))  # drop credsStore/credHelpers
    os.environ["DOCKER_CONFIG"] = "/tmp/docker"
    per_arch = []
    for p in platform:
        tag = f"{registry_repo}:{candidate}-{p.split('/', 1)[1].replace('/', '-')}"
        r = subprocess.run(["/tmp/crane", "append", "--platform", p, "-b", modelcar_base,
                            "-f", "/tmp/layer.tar", "-t", tag], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"crane append {p} failed:", r.stderr[-1500:]); raise SystemExit(1)
        per_arch.append(r.stdout.strip().splitlines()[-1]); print("pushed", per_arch[-1])
    cmd = ["/tmp/crane", "index", "append", "-t", f"{registry_repo}:{candidate}"]
    for m in per_arch: cmd += ["-m", m]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("crane index append failed:", r.stderr[-1500:]); raise SystemExit(1)
    ref = r.stdout.strip().splitlines()[-1]; print("pushed", ref); return ref


@dsl.component(base_image=PY_IMG)
def sign_modelcar(image_ref: str, rekor_url: str, cosign_version: str) -> str:
    """cosign v2.x sign by digest, --recursive so every per-arch manifest of the index carries its own
    signature (containers/image verifies the instance it selects); Rekor when rekor_url is set (RHTAS).
    COSIGN_PASSWORD arrives from the cosign-signing-key Secret's `cosign.password` key."""
    import os, platform as _plat, subprocess, urllib.request
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[_plat.machine()]
    urllib.request.urlretrieve(f"https://github.com/sigstore/cosign/releases/download/{cosign_version}/cosign-linux-{arch}", "/tmp/cosign")
    os.chmod("/tmp/cosign", 0o755)
    if "COSIGN_PASSWORD" not in os.environ:
        print("cosign-signing-key has no `cosign.password` key; assuming an unencrypted signing key"); os.environ["COSIGN_PASSWORD"] = ""
    import json as _j; os.makedirs("/tmp/docker", exist_ok=True)
    cfg = _j.load(open("/etc/quay/.dockerconfigjson"))
    _j.dump({"auths": cfg.get("auths", {})}, open("/tmp/docker/config.json", "w"))  # drop credsStore/credHelpers
    os.environ["DOCKER_CONFIG"] = "/tmp/docker"
    cmd = ["/tmp/cosign", "sign", "--key", "/etc/cosign/cosign.key", "-y", "--recursive", image_ref]
    cmd += ["--rekor-url", rekor_url, "--tlog-upload=true"] if rekor_url else ["--tlog-upload=false"]
    subprocess.run(cmd, check=True); print("signed", image_ref); return image_ref


@dsl.component(base_image=PY_IMG, packages_to_install=["PyGithub==2.4.0"])
def open_promotion_pr(image_ref: str, candidate: str, checkpoint_uri: str, report_json: str, github_repo: str,
                      gitops_branch: str, fleet_file: str, consumer_file: str, fleet_ui_url: str) -> str:
    """ONE commit (thor-testing 5e3e87a: a partial flip is an outage) editing the RHEM Fleet - modelcar
    digest + MODEL_VERSION - and the trigger's lineage in manifest-consumer, then a PR whose body carries
    the eval report, the Fleet URL and the rollback (D025). Human merge is the last gate."""
    import json, re
    from github import Github, InputGitTreeElement
    tok = open("/etc/github/token").read().strip()
    repo = Github(tok).get_repo(github_repo)
    base_ref = repo.get_git_ref(f"heads/{gitops_branch}"); base = repo.get_git_commit(base_ref.object.sha)
    def get(p): return repo.get_contents(p, ref=gitops_branch).decoded_content.decode()
    digest = image_ref.split("@", 1)[1]
    # Fleet: the two-regex edit. Both must match exactly once or the promotion is not attempted.
    fleet = get(fleet_file)
    old_mv = re.search(r"^\s+MODEL_VERSION:\s*(\S+)", fleet, re.M).group(1)
    fleet, n_img = re.subn(r"(soarm-act-modelcar)@sha256:[0-9a-f]{64}", r"\1@" + digest, fleet, count=1)
    fleet, n_mv = re.subn(r"^(\s+MODEL_VERSION:\s*)\S+", lambda m: m.group(1) + candidate, fleet, count=1, flags=re.M)
    # Trigger: the next round counts and fine-tunes from the new lineage (same commit, D025).
    consumer = get(consumer_file)
    n_c = 0
    for key, val in (("INCUMBENT", candidate), ("COLLECTOR", candidate), ("INCUMBENT_CHECKPOINT", checkpoint_uri)):
        consumer, n = re.subn(r"(\{name: " + key + r", value: \")[^\"]*(\")", lambda m: m.group(1) + val + m.group(2), consumer, count=1)
        n_c += n
    if (n_img, n_mv, n_c) != (1, 1, 3):
        print(f"promotion edit did not match exactly: image={n_img} model_version={n_mv} consumer={n_c}"); raise SystemExit(1)
    rep = json.loads(report_json)
    elems = [InputGitTreeElement(fleet_file, "100644", "blob", content=fleet),
             InputGitTreeElement(consumer_file, "100644", "blob", content=consumer)]
    tree = repo.create_git_tree(elems, base.tree)
    msg = (f"Promote {candidate}: Fleet act-inference <- {digest[:19]}..., MODEL_VERSION {old_mv} -> {candidate}; manifest-consumer lineage -> {candidate}\n\n"
           f"Eval gate: {rep['incumbent']} {rep['incumbent_success_rate']:.2f} -> {candidate} {rep['candidate_success_rate']:.2f}, fixed {rep['fixed']} broken {rep['broken']} net {rep['net']:+d} p={rep['sign_test_p']}")
    commit = repo.create_git_commit(msg, tree, [base])
    head = f"promote/{candidate}"; repo.create_git_ref(f"refs/heads/{head}", commit.sha)
    body = (f"## Promotion: `{candidate}` replaces `{rep['incumbent']}`\n\n"
            f"| | success | mean cubes |\n|---|---|---|\n| incumbent `{rep['incumbent']}` | {rep['incumbent_success_rate']:.0%} | {rep['incumbent_mean_cubes']:.2f} |\n"
            f"| candidate `{candidate}` | {rep['candidate_success_rate']:.0%} | {rep['candidate_mean_cubes']:.2f} |\n\n"
            f"Paired on {rep['n_paired']} identical seeded scenes: **{rep['fixed']} fixed / {rep['broken']} broken, net {rep['net']:+d}, sign-test p = {rep['sign_test_p']}** - gate rule: {rep['rule']} -> **{rep['verdict']}**.\n\n"
            f"Signed modelcar: `{image_ref}`\n\n"
            f"Merging edits Fleet `act-inference` in one commit (`{fleet_file}`: modelcar digest + `MODEL_VERSION` `{old_mv}` -> `{candidate}`) "
            f"and points the trigger at the new lineage (`{consumer_file}`: COLLECTOR/INCUMBENT/INCUMBENT_CHECKPOINT). "
            f"ResourceSync renders the Fleet; RHEM rolls it out batch by batch; each device pulls the modelcar under its policy.json "
            f"(cosign key + Rekor SET) and restarts the container, which publishes the new `model_version`.\n\n"
            f"Fleet: {fleet_ui_url}\n\n"
            f"Rollback: `git revert <sha>` - revert the merge commit of this PR and merge the revert. The previous modelcar is still in device storage "
            f"(image volume `reclaimPolicy: Retain`), so rolling back does not re-pull.")
    pr = repo.create_pull(title=f"Promote {candidate} ({rep['incumbent_success_rate']:.0%} -> {rep['candidate_success_rate']:.0%})", body=body, base=gitops_branch, head=head)
    print(pr.html_url); return pr.html_url


@dsl.pipeline(name="act-flywheel-promotion", description="assemble -> train -> eval gate -> package -> sign -> Fleet promotion PR (D022, D025)")
def act_flywheel_pipeline(candidate: str, incumbent: str = "upstream-act-teacher", collector: str = "upstream-act-teacher",
                          incumbent_checkpoint: str = "hf", steps_per_frame: float = 0.25, eval_n: int = 100,
                          eval_seed_base: int = 1000, mode: str = "desktop",
                          kafka_bootstrap: str = "edge-kafka.flywheel.svc:9092", s3_endpoint: str = "http://minio.minio.svc:9000",
                          registry_repo: str = "quay.io/jary/soarm-act-modelcar", platform: List[str] = ["linux/amd64", "linux/arm64"],
                          rekor_url: str = "http://rekor-server.trusted-artifact-signer.svc", crane_version: str = "v0.20.3", cosign_version: str = "v2.6.5",
                          github_repo: str = "RHPhysicalAI/hp-roscon-flywheel", gitops_branch: str = "desktop-gpu-split",
                          fleet_file: str = "gitops/rhem/fleet-act-inference.yaml", consumer_file: str = "gitops/flywheel/manifest-consumer.yaml",
                          fleet_ui_url: str = "https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference",
                          modelcar_base: str = UBI_MICRO, timeout_min: int = 600):
    run_id = dsl.PIPELINE_JOB_ID_PLACEHOLDER
    # mode == "cluster": in-pod train/eval components (Phase 4, GB10/GB300) replace this step; same outputs.
    t = trigger_and_wait(run_id=run_id, candidate=candidate, incumbent=incumbent, collector=collector,
                         incumbent_checkpoint=incumbent_checkpoint, steps_per_frame=steps_per_frame, eval_n=eval_n,
                         eval_seed_base=eval_seed_base, kafka_bootstrap=kafka_bootstrap, timeout_min=timeout_min)
    t.set_caching_options(False)
    g = eval_gate(eval_report_uri=t.outputs["eval_report"], s3_endpoint=s3_endpoint); g.set_caching_options(False)
    k8s.use_secret_as_env(g, secret_name="hub-credentials", secret_key_to_env={"s3-access-key": "AWS_ACCESS_KEY_ID", "s3-secret-key": "AWS_SECRET_ACCESS_KEY"})
    pk = package_modelcar(checkpoint_uri=t.outputs["checkpoint"], candidate=candidate, registry_repo=registry_repo,
                          platform=platform, s3_endpoint=s3_endpoint, crane_version=crane_version, modelcar_base=modelcar_base).after(g)
    pk.set_caching_options(False)
    k8s.use_secret_as_env(pk, secret_name="hub-credentials", secret_key_to_env={"s3-access-key": "AWS_ACCESS_KEY_ID", "s3-secret-key": "AWS_SECRET_ACCESS_KEY"})
    k8s.use_secret_as_volume(pk, secret_name="quay-push", mount_path="/etc/quay")
    sg = sign_modelcar(image_ref=pk.output, rekor_url=rekor_url, cosign_version=cosign_version); sg.set_caching_options(False)
    k8s.use_secret_as_volume(sg, secret_name="quay-push", mount_path="/etc/quay")
    k8s.use_secret_as_volume(sg, secret_name="cosign-signing-key", mount_path="/etc/cosign")
    k8s.use_secret_as_env(sg, secret_name="cosign-signing-key", secret_key_to_env={"cosign.password": "COSIGN_PASSWORD"}, optional=True)
    pr = open_promotion_pr(image_ref=sg.output, candidate=candidate, checkpoint_uri=t.outputs["checkpoint"], report_json=g.output,
                           github_repo=github_repo, gitops_branch=gitops_branch, fleet_file=fleet_file, consumer_file=consumer_file,
                           fleet_ui_url=fleet_ui_url)
    pr.set_caching_options(False)
    k8s.use_secret_as_volume(pr, secret_name="github-token", mount_path="/etc/github")


if __name__ == "__main__":
    compiler.Compiler().compile(act_flywheel_pipeline, "pipeline/act_flywheel_pipeline.yaml")
    print("compiled -> pipeline/act_flywheel_pipeline.yaml")

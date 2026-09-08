"""ACT flywheel promotion pipeline (KFP v2, RHOAI Data Science Pipelines) - D022.

  trigger+wait -> gate -> package -> sign -> open_promotion_pr        (human merge = last gate)

Written for the in-cluster-GPU target. `mode="desktop"` is the [desktop shim]: train + eval are
delegated to the host runner over Kafka (training-triggers / training-results) and the pipeline
waits for the artifacts in MinIO. `mode="cluster"` runs them in-pod (GB10/GB300 - Phase 4).
Every artifact contract (checkpoint tar, eval_report.json, image digest) is identical in both.

Compile:  python pipeline/act_flywheel_pipeline.py  -> pipeline/act_flywheel_pipeline.yaml
Secrets expected in the DSP project namespace: hub-credentials (S3), quay-push (dockerconfigjson),
cosign-signing-key (cosign.key), github-token (token).
"""
from kfp import dsl, compiler
from kfp import kubernetes as k8s

PY_IMG = "registry.access.redhat.com/ubi9/python-312:latest"


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
def eval_gate(eval_report_uri: str, s3_endpoint: str, report_out: dsl.OutputPath(str)) -> str:
    """Gate: candidate vs incumbent on the same fixed seeds. Promote iff net > 0 and p < 0.05 (D022).
    Fails the pipeline (sys.exit 1) otherwise - downstream never runs (thor-testing's hard-stop)."""
    import json, os, sys, boto3
    s3 = boto3.client("s3", endpoint_url=s3_endpoint, aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    b, k = eval_report_uri[5:].split("/", 1)
    rep = json.loads(s3.get_object(Bucket=b, Key=k)["Body"].read())
    print(json.dumps(rep, indent=1))
    with open(report_out, "w") as f: json.dump(rep, f)
    ok = rep["net"] > 0 and rep["sign_test_p"] < 0.05
    print(f"GATE {'PASS' if ok else 'FAIL'}: {rep['incumbent']} {rep['incumbent_success_rate']:.2f} -> "
          f"{rep['candidate']} {rep['candidate_success_rate']:.2f}; fixed {rep['fixed']} broken {rep['broken']} "
          f"net {rep['net']:+d} p={rep['sign_test_p']}")
    if not ok: sys.exit(1)
    return "PASS"


@dsl.component(base_image=PY_IMG, packages_to_install=["boto3==1.35.36"])
def package_modelcar(checkpoint_uri: str, candidate: str, registry_repo: str, platform: str,
                     s3_endpoint: str, crane_version: str) -> str:
    """crane append: flat ACT checkpoint dir -> /models/act on ubi-micro. Returns image@digest."""
    import os, subprocess, tarfile, io, boto3, urllib.request
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
    url = f"https://github.com/google/go-containerregistry/releases/download/{crane_version}/go-containerregistry_Linux_x86_64.tar.gz"
    urllib.request.urlretrieve(url, "/tmp/crane.tgz"); subprocess.run(["tar", "-xzf", "/tmp/crane.tgz", "-C", "/tmp", "crane"], check=True)
    # quay-push is a kubernetes.io/dockerconfigjson Secret: the mounted file is .dockerconfigjson,
    # while crane/cosign look for $DOCKER_CONFIG/config.json.
    import shutil; os.makedirs("/tmp/docker", exist_ok=True)
    shutil.copy("/etc/quay/.dockerconfigjson", "/tmp/docker/config.json"); os.environ["DOCKER_CONFIG"] = "/tmp/docker"
    r = subprocess.run(["/tmp/crane", "append", "--platform", platform, "-b", "registry.access.redhat.com/ubi9/ubi-micro:latest",
                        "-f", "/tmp/layer.tar", "-t", f"{registry_repo}:{candidate}"], capture_output=True, text=True)
    if r.returncode != 0:
        print("crane append failed:", r.stderr[-1500:]); raise SystemExit(1)
    ref = r.stdout.strip().splitlines()[-1]; print("pushed", ref); return ref


@dsl.component(base_image=PY_IMG)
def sign_modelcar(image_ref: str, rekor_url: str, cosign_version: str) -> str:
    """cosign v2.x sign by digest; Rekor transparency log when rekor_url is set (RHTAS)."""
    import os, subprocess, urllib.request
    urllib.request.urlretrieve(f"https://github.com/sigstore/cosign/releases/download/{cosign_version}/cosign-linux-amd64", "/tmp/cosign")
    os.chmod("/tmp/cosign", 0o755); os.environ["COSIGN_PASSWORD"] = ""
    import shutil; os.makedirs("/tmp/docker", exist_ok=True)
    shutil.copy("/etc/quay/.dockerconfigjson", "/tmp/docker/config.json"); os.environ["DOCKER_CONFIG"] = "/tmp/docker"
    cmd = ["/tmp/cosign", "sign", "--key", "/etc/cosign/cosign.key", "-y", image_ref]
    cmd += ["--rekor-url", rekor_url, "--tlog-upload=true"] if rekor_url else ["--tlog-upload=false"]
    subprocess.run(cmd, check=True); print("signed", image_ref); return image_ref


@dsl.component(base_image=PY_IMG, packages_to_install=["PyGithub==2.4.0"])
def open_promotion_pr(image_ref: str, candidate: str, report: dsl.InputPath(str), github_repo: str,
                      gitops_branch: str) -> str:
    """ONE commit editing the three act-serving files atomically (thor-testing 5e3e87a), then a PR
    whose body carries the eval report. Human merge is the last gate; Argo does the rest."""
    import json, re
    from github import Github, InputGitTreeElement
    tok = open("/etc/github/token").read().strip()
    repo = Github(tok).get_repo(github_repo)
    base_ref = repo.get_git_ref(f"heads/{gitops_branch}"); base = repo.get_git_commit(base_ref.object.sha)
    def get(p): return repo.get_contents(p, ref=gitops_branch).decoded_content.decode()
    green = get("gitops/act-serving/deployment-green.yaml"); blue = get("gitops/act-serving/deployment.yaml"); svc = get("gitops/act-serving/service.yaml")
    digest = image_ref.split("@", 1)[1]
    green = re.sub(r"(soarm-act-modelcar)@sha256:[0-9a-f]{64}", r"\1@" + digest, green, count=1)
    green = re.sub(r"(name: MODEL_VERSION\n\s+value: ).*", lambda m: m.group(1) + candidate, green, count=1)
    green = green.replace("replicas: 0", "replicas: 1", 1)
    blue = blue.replace("replicas: 1", "replicas: 0", 1)
    svc = svc.replace("color: blue", "color: green", 1)
    rep = json.load(open(report))
    elems = [InputGitTreeElement("gitops/act-serving/deployment-green.yaml", "100644", "blob", content=green),
             InputGitTreeElement("gitops/act-serving/deployment.yaml", "100644", "blob", content=blue),
             InputGitTreeElement("gitops/act-serving/service.yaml", "100644", "blob", content=svc)]
    tree = repo.create_git_tree(elems, base.tree)
    msg = f"Promote {candidate}: green <- {image_ref.split('@')[1][:19]}..., blue 0, service -> green\n\nEval gate: {rep['incumbent']} {rep['incumbent_success_rate']:.2f} -> {candidate} {rep['candidate_success_rate']:.2f}, fixed {rep['fixed']} broken {rep['broken']} net {rep['net']:+d} p={rep['sign_test_p']}"
    commit = repo.create_git_commit(msg, tree, [base])
    head = f"promote/{candidate}"; repo.create_git_ref(f"refs/heads/{head}", commit.sha)
    body = (f"## Promotion: `{candidate}` replaces `{rep['incumbent']}`\n\n"
            f"| | success | mean cubes |\n|---|---|---|\n| incumbent `{rep['incumbent']}` | {rep['incumbent_success_rate']:.0%} | {rep['incumbent_mean_cubes']:.2f} |\n"
            f"| candidate `{candidate}` | {rep['candidate_success_rate']:.0%} | {rep['candidate_mean_cubes']:.2f} |\n\n"
            f"Paired on {rep['n_paired']} identical seeded scenes: **{rep['fixed']} fixed / {rep['broken']} broken, net {rep['net']:+d}, sign-test p = {rep['sign_test_p']}** - gate rule: {rep['rule']} -> **{rep['verdict']}**.\n\n"
            f"Signed modelcar: `{image_ref}`\n\nMerging flips blue/green atomically (green replicas 1, blue 0, Service -> green); Argo syncs it; the swap agent applies it on the desktop.")
    pr = repo.create_pull(title=f"Promote {candidate} ({rep['incumbent_success_rate']:.0%} -> {rep['candidate_success_rate']:.0%})", body=body, base=gitops_branch, head=head)
    print(pr.html_url); return pr.html_url


@dsl.pipeline(name="act-flywheel-promotion", description="assemble -> train -> eval gate -> package -> sign -> promotion PR (D022)")
def act_flywheel_pipeline(candidate: str, incumbent: str = "upstream-act-teacher", collector: str = "upstream-act-teacher",
                          incumbent_checkpoint: str = "hf", steps_per_frame: float = 0.25, eval_n: int = 100,
                          eval_seed_base: int = 1000, mode: str = "desktop",
                          kafka_bootstrap: str = "edge-kafka.flywheel.svc:9092", s3_endpoint: str = "http://minio.minio.svc:9000",
                          registry_repo: str = "quay.io/jary/soarm-act-modelcar", platform: str = "linux/amd64",
                          rekor_url: str = "", crane_version: str = "v0.20.3", cosign_version: str = "v2.6.5",
                          github_repo: str = "RHPhysicalAI/hp-roscon-flywheel", gitops_branch: str = "desktop-gpu-split",
                          timeout_min: int = 600):
    run_id = dsl.PIPELINE_JOB_ID_PLACEHOLDER
    # mode == "cluster": in-pod train/eval components (Phase 4, GB10/GB300) replace this step; same outputs.
    t = trigger_and_wait(run_id=run_id, candidate=candidate, incumbent=incumbent, collector=collector,
                         incumbent_checkpoint=incumbent_checkpoint, steps_per_frame=steps_per_frame, eval_n=eval_n,
                         eval_seed_base=eval_seed_base, kafka_bootstrap=kafka_bootstrap, timeout_min=timeout_min)
    t.set_caching_options(False)
    g = eval_gate(eval_report_uri=t.outputs["eval_report"], s3_endpoint=s3_endpoint); g.set_caching_options(False)
    k8s.use_secret_as_env(g, secret_name="hub-credentials", secret_key_to_env={"s3-access-key": "AWS_ACCESS_KEY_ID", "s3-secret-key": "AWS_SECRET_ACCESS_KEY"})
    pk = package_modelcar(checkpoint_uri=t.outputs["checkpoint"], candidate=candidate, registry_repo=registry_repo,
                          platform=platform, s3_endpoint=s3_endpoint, crane_version=crane_version).after(g)
    pk.set_caching_options(False)
    k8s.use_secret_as_env(pk, secret_name="hub-credentials", secret_key_to_env={"s3-access-key": "AWS_ACCESS_KEY_ID", "s3-secret-key": "AWS_SECRET_ACCESS_KEY"})
    k8s.use_secret_as_volume(pk, secret_name="quay-push", mount_path="/etc/quay")
    sg = sign_modelcar(image_ref=pk.output, rekor_url=rekor_url, cosign_version=cosign_version); sg.set_caching_options(False)
    k8s.use_secret_as_volume(sg, secret_name="quay-push", mount_path="/etc/quay")
    k8s.use_secret_as_volume(sg, secret_name="cosign-signing-key", mount_path="/etc/cosign")
    pr = open_promotion_pr(image_ref=sg.output, candidate=candidate, report=g.outputs["report_out"], github_repo=github_repo, gitops_branch=gitops_branch)
    pr.set_caching_options(False)
    k8s.use_secret_as_volume(pr, secret_name="github-token", mount_path="/etc/github")


if __name__ == "__main__":
    compiler.Compiler().compile(act_flywheel_pipeline, "pipeline/act_flywheel_pipeline.yaml")
    print("compiled -> pipeline/act_flywheel_pipeline.yaml")

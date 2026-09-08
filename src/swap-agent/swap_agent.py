#!/usr/bin/env python3
"""Swap agent — [desktop shim] the blue/green swap for a host-GPU policy (D022, Phase 3 step 5).

On the target the promoted policy is swapped by Argo syncing gitops/act-serving (Deployment pair +
Service selector, Recreate). On the desktop there is no in-cluster GPU, so this agent applies the
SAME three files to the host container:

  1. git pull the repo; read gitops/act-serving/{service.yaml, deployment.yaml, deployment-green.yaml}
  2. live side = the Deployment whose `color` matches the Service selector (and has replicas: 1 on the
     target; on the desktop both are 0, the selector alone decides)
  3. take its modelcar initContainer image (must be pinned BY DIGEST) and MODEL_VERSION env
  4. if the running act-inference already serves that MODEL_VERSION -> nothing to do
  5. cosign verify the image against the operator's public key (the desktop stand-in for
     policy.json + registries.d enforcement); refuse to swap an unsigned/unknown image
  6. crane export the image, extract models/act -> ~/flywheel-data/models/<MODEL_VERSION>/
  7. recreate act-inference (Recreate semantics: stop old, start new) with POLICY_PATH=/model and
     the promoted MODEL_VERSION, copying every other env from the previous container; arm the
     bag watchdog

  swap_agent.py --check   report what would happen, change nothing
  swap_agent.py           one pass
  swap_agent.py --loop N  poll every N seconds (detached)
Deleted by the Fury port.
"""
import argparse, json, os, re, subprocess, sys, tarfile, time
from pathlib import Path
import yaml

HOME = Path.home()
REPO = HOME / "redhat/git/hp-roscon-flywheel"
SERVING = REPO / "gitops/act-serving"
MODELS = HOME / "flywheel-data/models"
PUBKEY = HOME / "cosign/cosign.pub"
BIN = HOME / "bin"
CONTAINER = "act-inference"
PLACEHOLDER = "sha256:" + "0" * 64


def log(m): print(time.strftime("%m-%d %H:%M ") + "[swap] " + m, flush=True)
def run(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def read_live():
    run(["git", "-C", str(REPO), "pull", "--ff-only", "-q"])
    svc = yaml.safe_load(open(SERVING / "service.yaml"))
    color = svc["spec"]["selector"]["color"]
    for f in ("deployment.yaml", "deployment-green.yaml"):
        d = yaml.safe_load(open(SERVING / f))
        if d["metadata"]["labels"].get("color") == color:
            spec = d["spec"]["template"]["spec"]
            image = next(c["image"] for c in spec["initContainers"] if c["name"] == "modelcar")
            env = {e["name"]: e.get("value") for c in spec["containers"] for e in c.get("env", [])}
            return color, image, env.get("MODEL_VERSION"), f
    raise SystemExit(f"no Deployment carries color={color}")


def running_version():
    r = run(["docker", "inspect", CONTAINER, "--format", "{{range .Config.Env}}{{println .}}{{end}}"])
    if r.returncode: return None, None
    env = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    st = run(["docker", "inspect", CONTAINER, "--format", "{{.State.Status}}"]).stdout.strip()
    return env.get("MODEL_VERSION"), st


def verify(image):
    r = run([str(BIN / "cosign"), "verify", "--key", str(PUBKEY), "--insecure-ignore-tlog", image])
    return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1:]


def export_models(image, mv):
    dest = MODELS / mv
    if (dest / "act" / "model.safetensors").exists(): return dest / "act"
    dest.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([str(BIN / "crane"), "export", image, "-"], stdout=subprocess.PIPE)
    with tarfile.open(fileobj=p.stdout, mode="r|*") as t:
        for m in t:
            if m.name.startswith("models/act/") and m.isfile():
                m.name = m.name[len("models/"):]; t.extract(m, dest)
    p.wait()
    assert (dest / "act" / "model.safetensors").exists(), "export produced no model.safetensors"
    return dest / "act"


def recreate(model_dir, mv):
    r = run(["docker", "inspect", CONTAINER, "--format", "{{range .Config.Env}}{{println .}}{{end}}"])
    env = [l for l in r.stdout.splitlines() if l and not l.startswith(("MODEL_VERSION=", "POLICY_PATH="))]
    run(["docker", "stop", CONTAINER]); run(["docker", "rename", CONTAINER, f"{CONTAINER}-pre-{mv}-{int(time.time())}"])
    cmd = ["docker", "run", "-d", "--name", CONTAINER, "--gpus", "all", "--network", "host"]
    for e in env: cmd += ["-e", e]
    cmd += ["-e", f"MODEL_VERSION={mv}", "-e", "POLICY_PATH=/model",
            "-v", f"{model_dir}:/model:ro", "-v", f"{HOME}/.cache/huggingface:/root/.cache/huggingface",
            "-v", f"{HOME}/flywheel-data/bags:/data/bags", "-v", f"{HOME}/flywheel-data:/data", "act-inference:latest"]
    r = run(cmd); assert r.returncode == 0, r.stderr
    if run(["pgrep", "-f", "bag_watchdog.sh"]).returncode:
        subprocess.Popen(["bash", str(HOME / "bag_watchdog.sh")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def one_pass(check):
    color, image, mv, f = read_live()
    if "@sha256:" not in image or PLACEHOLDER in image:
        log(f"live={color} ({f}) image not promoted yet ({image.split('@')[-1][:19]}...) — nothing to do"); return
    cur, st = running_version()
    log(f"live={color} model_version={mv} image={image.split('@')[-1][:19]}...  running={cur} ({st})")
    if cur == mv and st == "running":
        log("already serving the promoted version"); return
    ok, tail = verify(image)
    if not ok:
        log(f"REFUSING: signature verification failed for {image}: {tail}"); return
    log("signature verified")
    if check:
        log(f"check mode: would export models/act and recreate {CONTAINER} as {mv}"); return
    d = export_models(image, mv); log(f"exported -> {d}")
    recreate(d, mv); log(f"recreated {CONTAINER} serving {mv} (watchdog armed)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); ap.add_argument("--loop", type=int, default=0)
    a = ap.parse_args()
    while True:
        try: one_pass(a.check)
        except Exception as e: log(f"error: {type(e).__name__}: {e}")
        if not a.loop: break
        time.sleep(a.loop)

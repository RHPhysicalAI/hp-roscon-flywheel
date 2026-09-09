#!/usr/bin/env python3
# This project was developed with assistance from AI tools.
"""Offline CPU forward-latency benchmark for the ACT policy (D024 spike, part 1)."""

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time

import torch


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile of a sample list."""
    ordered = sorted(samples)
    idx = max(0, math.ceil(p / 100.0 * len(ordered)) - 1)
    return ordered[idx]


def cpu_model() -> str:
    """Best-effort CPU model string from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def synthetic_observation(input_features: dict) -> dict[str, torch.Tensor]:
    """Observation shaped like lerobot's raw_observation_to_observation output, random content."""
    obs = {}
    for key, feat in input_features.items():
        shape = tuple(feat["shape"])
        if feat["type"] == "VISUAL":
            # policy_server hands the preprocessor images already batched (1, C, H, W) in [0, 1]
            obs[key] = torch.rand((1, *shape), dtype=torch.float32).contiguous()
        else:
            obs[key] = torch.randn(shape, dtype=torch.float32)
    return obs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="/data/models/act-v2-ft160/act")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("BENCH_THREADS", "8")))
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--fps", type=float, default=50.0, help="control rate the contract runs at")
    ap.add_argument("--json", default=None, help="write the result record here as JSON")
    args = ap.parse_args()

    assert not torch.cuda.is_available(), "CUDA visible; this benchmark must run on CPU only"
    torch.set_num_threads(args.threads)

    from lerobot.policies.factory import get_policy_class, make_pre_post_processors

    with open(os.path.join(args.model, "config.json")) as f:
        cfg = json.load(f)
    n_action_steps = cfg["n_action_steps"]
    chunk_size = cfg["chunk_size"]
    # D024: p95 forward latency must stay under half the wall time one chunk covers.
    threshold_ms = 0.5 * (n_action_steps / args.fps) * 1000.0

    from lerobot.configs.policies import PreTrainedConfig

    t_load = time.perf_counter()
    # The ImageNet backbone init is overwritten by the checkpoint; skipping it avoids a download.
    policy_cfg = PreTrainedConfig.from_pretrained(args.model)
    policy_cfg.pretrained_backbone_weights = None
    policy_cfg.device = "cpu"
    policy = get_policy_class("act").from_pretrained(args.model, config=policy_cfg)
    policy.to("cpu")
    policy.eval()
    device_override = {"device": "cpu"}
    preprocessor, postprocessor = make_pre_post_processors(
        policy.config,
        pretrained_path=args.model,
        preprocessor_overrides={
            "device_processor": device_override,
            "rename_observations_processor": {"rename_map": {}},
        },
        postprocessor_overrides={"device_processor": device_override},
    )
    load_s = time.perf_counter() - t_load

    obs = synthetic_observation(cfg["input_features"])

    def one_pass() -> tuple[float, float, float]:
        sample = {k: v.clone() for k, v in obs.items()}
        t0 = time.perf_counter()
        batch = preprocessor(sample)
        t1 = time.perf_counter()
        chunk = policy.predict_action_chunk(batch)
        t2 = time.perf_counter()
        for i in range(chunk.shape[1]):
            postprocessor(chunk[:, i, :])
        t3 = time.perf_counter()
        return (t1 - t0) * 1000.0, (t2 - t1) * 1000.0, (t3 - t2) * 1000.0

    with torch.inference_mode():
        for _ in range(args.warmup):
            one_pass()
        pre, fwd, post = [], [], []
        t_run = time.perf_counter()
        for _ in range(args.iters):
            a, b, c = one_pass()
            pre.append(a)
            fwd.append(b)
            post.append(c)
        run_s = time.perf_counter() - t_run

    e2e = [a + b + c for a, b, c in zip(pre, fwd, post)]
    p95_fwd = percentile(fwd, 95)
    verdict = "PASS" if p95_fwd < threshold_ms else "FAIL"

    def stats(xs: list[float]) -> dict:
        return {
            "p50_ms": round(percentile(xs, 50), 1),
            "p95_ms": round(percentile(xs, 95), 1),
            "p99_ms": round(percentile(xs, 99), 1),
            "mean_ms": round(statistics.fmean(xs), 1),
            "max_ms": round(max(xs), 1),
        }

    record = {
        "model": args.model,
        "torch": torch.__version__,
        "threads": torch.get_num_threads(),
        "affinity_cpus": sorted(os.sched_getaffinity(0)),
        "cpu_model": cpu_model(),
        "arch": platform.machine(),
        "chunk_size": chunk_size,
        "n_action_steps": n_action_steps,
        "input_features": cfg["input_features"],
        "fps": args.fps,
        "threshold_p95_ms": threshold_ms,
        "warmup": args.warmup,
        "iters": args.iters,
        "load_s": round(load_s, 1),
        "run_s": round(run_s, 1),
        "preprocess": stats(pre),
        "forward": stats(fwd),
        "postprocess": stats(post),
        "end_to_end": stats(e2e),
        "verdict": verdict,
    }

    print(f"model={args.model} torch={record['torch']} threads={record['threads']} "
          f"cpus={record['affinity_cpus']} arch={record['arch']}")
    print(f"cpu={record['cpu_model']}")
    print(f"chunk_size={chunk_size} n_action_steps={n_action_steps} fps={args.fps:g} "
          f"threshold_p95={threshold_ms:.0f} ms  (load {load_s:.1f}s, {args.iters} iters in {run_s:.1f}s)")
    print(f"{'stage':<12}{'p50':>9}{'p95':>9}{'p99':>9}{'mean':>9}{'max':>9}")
    for name in ("preprocess", "forward", "postprocess", "end_to_end"):
        s = record[name]
        print(f"{name:<12}{s['p50_ms']:>9.1f}{s['p95_ms']:>9.1f}{s['p99_ms']:>9.1f}"
              f"{s['mean_ms']:>9.1f}{s['max_ms']:>9.1f}")
    print(f"VERDICT {verdict}: forward p95 {p95_fwd:.1f} ms vs threshold {threshold_ms:.0f} ms")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(record, f, indent=2)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

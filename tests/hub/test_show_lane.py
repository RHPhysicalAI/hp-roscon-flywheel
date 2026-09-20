# This project was developed with assistance from AI tools.
"""The live lane (D166): the show curator has no way to the flywheel's data, the shared curator code is unchanged unless asked, and the page says what it shows."""
import ast
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FLYWHEEL = ROOT / "gitops" / "flywheel"
PIPELINE_NAMES = re.compile(r"S3|MINIO|KAFKA|AWS|BUCKET|BOOTSTRAP|SECRET|ACCESS_KEY", re.IGNORECASE)


def documents(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((FLYWHEEL / name).read_text()) if d]


def one(name: str, kind: str, resource: str) -> dict:
    (found,) = [d for d in documents(name) if d["kind"] == kind and d["metadata"]["name"] == resource]
    return found


def embedded(name: str, configmap: str, key: str) -> str:
    return one(name, "ConfigMap", configmap)["data"][key]


def functions(source: str, *names: str) -> dict:
    """The named top-level functions of an embedded module, exec'd alone: the module itself needs flask and a cluster."""
    tree = ast.parse(source)
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in picked} == set(names)
    scope: dict = {"json": json, "pathlib": __import__("pathlib")}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<configmap>", "exec"), scope)  # noqa: S102 - the repository's own code
    return scope


def container(deployment: dict) -> dict:
    (only,) = deployment["spec"]["template"]["spec"]["containers"]
    return only


def env(deployment: dict) -> dict:
    return {e["name"]: e for e in container(deployment).get("env", [])}


# ---- the shared curator code: one code, two deployments ----

@pytest.fixture
def curator(tmp_path, monkeypatch):
    """curator.py from the ConfigMap, loaded as a module would be (its main loop is behind __main__)."""
    def load(**settings: str) -> dict:
        for key in ("KEEP_NEWEST", "MAX_BODY_BYTES", "RAW_DIR", "CURATED_DIR", "REJECTED_DIR"):
            monkeypatch.delenv(key, raising=False)
        for key, value in {"RAW_DIR": f"{tmp_path}/raw", "CURATED_DIR": f"{tmp_path}/curated",
                           "REJECTED_DIR": f"{tmp_path}/rejected", **settings}.items():
            monkeypatch.setenv(key, value)
        scope: dict = {"__name__": "curator_under_test"}
        exec(compile(embedded("curator.yaml", "curator-code", "curator.py"), "curator.py", "exec"), scope)  # noqa: S102 - the repository's own code
        for d in ("raw", "curated", "rejected"):
            (tmp_path / d).mkdir(exist_ok=True)
        return scope
    return load


def records(directory: Path, count: int, start: float = 1_000_000.0) -> list[Path]:
    made = []
    for i in range(count):
        path = directory / f"ep-{i:03d}.json"
        path.write_text("{}")
        os.utime(path, (start + i, start + i))
        made.append(path)
    return made


def test_the_embedded_python_parses():
    """A ConfigMap with a syntax error is a pod that never becomes ready."""
    for name, configmap, key in (("curator.yaml", "curator-code", "curator.py"), ("dashboard.yaml", "dashboard-code", "dashboard.py")):
        ast.parse(embedded(name, configmap, key))


def test_the_flywheels_curator_prunes_nothing_and_writes_no_totals(curator, tmp_path):
    """KEEP_NEWEST unset is the flywheel's own lane: the housekeeping does nothing at all."""
    module = curator()
    assert module["KEEP_NEWEST"] == 0
    assert "KEEP_NEWEST" not in env(one("curator.yaml", "Deployment", "curator"))
    assert (module["RECORD_INDENT"], module["MAX_BODY_BYTES"]) == (2, 1_000_000), "the flywheel's records and body limit are what they were"
    made = records(tmp_path / "curated", 5) + records(tmp_path / "rejected", 5) + records(tmp_path / "raw", 5)
    (tmp_path / "raw" / "old.json.bad").write_text("x")
    for verdict in ("pass", "reject", None):
        module["keep_lane_small"](verdict)
    assert all(p.exists() for p in made) and (tmp_path / "raw" / "old.json.bad").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["curated", "raw", "rejected"]


def test_the_live_lane_keeps_the_newest_records_and_counts_every_verdict(curator, tmp_path):
    """KEEP_NEWEST=3: three records a verdict directory stay, the totals go on counting."""
    module = curator(KEEP_NEWEST="3")
    made = records(tmp_path / "curated", 5)
    records(tmp_path / "rejected", 2)
    for verdict in ("pass", "pass", "reject"):
        module["keep_lane_small"](verdict)
    assert [p.name for p in made if p.exists()] == ["ep-002.json", "ep-003.json", "ep-004.json"]
    assert len(list((tmp_path / "rejected").glob("*.json"))) == 2
    totals = json.loads((tmp_path / "totals.json").read_text())
    assert {k: totals[k] for k in ("pass", "reject")} == {"pass": 2, "reject": 1} and sorted(totals) == ["pass", "reject", "since"]
    assert abs(totals["since"] - time.time()) < 60, "when this count began, for the page to show beside it"
    assert not list(tmp_path.glob("*.tmp"))


def test_an_unreadable_totals_file_starts_over_and_says_so(curator, tmp_path, capsys):
    """A half-written or foreign file must not wedge the loop - and the number on the page never drops to zero in silence."""
    module = curator(KEEP_NEWEST="3")
    for junk in ("{not json", "[1, 2]", '{"pass": "many"}'):
        (tmp_path / "totals.json").write_text(junk)
        got = module["bump_totals"](tmp_path / "totals.json", "pass")
        assert got["pass"] == 1 and "since" in got
        assert "the live lane's count starts again at zero" in capsys.readouterr().out
    (tmp_path / "totals.json").unlink()
    module["bump_totals"](tmp_path / "totals.json", "pass")
    assert capsys.readouterr().out == "", "a first verdict on an empty volume is not a reset"


def test_the_count_keeps_the_moment_it_began(curator, tmp_path):
    """since is set once and survives every later verdict: a total written by hand does not get a new age."""
    module = curator(KEEP_NEWEST="3")
    (tmp_path / "totals.json").write_text(json.dumps({"pass": 7, "reject": 2, "since": 1_700_000_000}))
    assert module["bump_totals"](tmp_path / "totals.json", "reject") == {"pass": 7, "reject": 3, "since": 1_700_000_000}


def test_prune_zero_keeps_everything(curator, tmp_path):
    """0 means off, not 'keep none'."""
    module = curator()
    made = records(tmp_path / "curated", 4)
    assert module["prune"](tmp_path / "curated", 0) == 0 and all(p.exists() for p in made)
    assert module["prune"](tmp_path / "curated", 1) == 3 and [p.name for p in made if p.exists()] == ["ep-003.json"]


def test_no_gate_looks_at_a_bag_so_the_live_lane_needs_no_knob(curator):
    """Robot zero records nothing: its summary carries dataset_path null, and the verdict is the same as with a bag."""
    module = curator()
    episode = {"episode_id": "e", "has_failure": False, "rollout": {"status": "ok", "steps": 900, "duration_s": 41.0},
               "task_success": True, "cubes_placed": 3, "avg_smoothness": 0.004, "dataset_path": None}
    score, reason = module["score_episode"](episode)
    assert score >= module["PASS_THRESHOLD"] and reason.startswith("clean")
    assert module["score_episode"]({**episode, "dataset_path": "bags/episode_0001"}) == (score, reason)
    assert module["score_episode"]({**episode, "task_success": False, "cubes_placed": 2})[1] == "task-failed (2/3 cubes placed)"
    assert "dataset_path" not in embedded("curator.yaml", "curator-code", "curator.py")


# ---- what the receiver accepts, and what becomes of a record nobody can judge ----

def emitted(**changes) -> dict:
    """An episode summary exactly as src/episode-emitter/episode_emitter.py builds it."""
    return {"episode_id": "0b1f6c1e-7c1d-4f7e-9a59-0d2f3c4b5a69", "timestamp": "2026-09-20T20:15:42Z", "scene": "place_cubes_on_tray",
            "model_version": "act-v2-ft160", "has_failure": False, "rollout": {"status": "ok", "steps": 1243, "duration_s": 41.27},
            "task_success": True, "cubes_placed": 3, "score_reason": None, "avg_smoothness": 0.004112, "dataset_path": None, **changes}


@pytest.mark.parametrize("episode", [
    emitted(),
    emitted(dataset_path="bags/episode_20260920_201542"),
    emitted(cubes_placed=None, task_success=False, score_reason="sensor-unavailable"),
    emitted(rollout={"status": "truncated", "steps": 6, "duration_s": 0.4}, task_success=False, cubes_placed=0),
    emitted(model_version="eval-act-v2-ft160-ft160-202609201129", avg_smoothness=0),
    {"episode_id": "minimal"},
])
def test_what_the_real_emitter_sends_is_accepted(curator, episode):
    """The flywheel's accepted inputs stay accepted: every shape the emitter produces, bag or no bag."""
    curator()["check_episode"](episode)


@pytest.mark.parametrize("episode, names", [
    ([], "not a JSON object"), ("x", "not a JSON object"), (None, "not a JSON object"),
    (emitted(scene="<img src=x onerror=alert(1)>"), "scene"),
    (emitted(scene="s" * 65), "scene"), (emitted(scene=7), "scene"),
    (emitted(model_version="v2<script>"), "model_version"),
    (emitted(timestamp="\"><svg onload=alert(1)>"), "timestamp"),
    (emitted(score_reason="<b>"), "score_reason"),
    (emitted(rollout={"status": "<img src=x onerror=alert(1)>", "steps": 900}), "rollout.status"),
    (emitted(rollout="ok"), "rollout"),
    (emitted(rollout={"status": "ok", "steps": "900"}), "rollout.steps"),
    (emitted(rollout={"status": "ok", "steps": 900, "duration_s": "41"}), "rollout.duration_s"),
    (emitted(cubes_placed="3"), "cubes_placed"), (emitted(cubes_placed=True), "cubes_placed"),
    (emitted(cubes_placed=-1), "cubes_placed"), (emitted(cubes_placed=3.5), "cubes_placed"),
    (emitted(task_success="yes"), "task_success"), (emitted(has_failure=0), "has_failure"),
    (emitted(avg_smoothness="<i>"), "avg_smoothness"), (emitted(avg_smoothness=float("nan")), "avg_smoothness"),
    (emitted(avg_smoothness=float("inf")), "avg_smoothness"),
])
def test_what_a_page_would_display_must_be_plain(curator, episode, names):
    """Markup, a string where a number belongs, a number no browser parses: refused, and the refusal names the field."""
    with pytest.raises(ValueError, match=re.escape(names)):
        curator()["check_episode"](episode)


def test_a_good_record_is_judged_as_before_and_compact_only_on_the_live_lane(curator, tmp_path, capsys):
    """The flywheel's lane writes what it always wrote, byte for byte; the live lane writes the same record without the indent."""
    for settings, indent in (({}, 2), ({"KEEP_NEWEST": "3"}, None)):
        module = curator(**settings)
        raw = tmp_path / "raw" / "good.json"
        raw.write_text(json.dumps(emitted(), indent=2))
        assert module["curate"](raw) == "pass" and not raw.exists()
        want = {**emitted(), "curation_score": 1.0, "curation_reason": "clean (task=success rollout=ok steps=1243 smoothness=0.0041)",
                "curation_verdict": "pass"}
        assert (tmp_path / "curated" / "good.json").read_text() == json.dumps(want, indent=indent)
        assert capsys.readouterr().out.endswith(f"[curator] PASS  {emitted()['episode_id']} score=1.000 ({want['curation_reason']})\n")
        (tmp_path / "curated" / "good.json").unlink()


@pytest.mark.parametrize("body", ["[]", '"x"', "{half", '{"episode_id": "e", "avg_smoothness": NaN, "cubes_placed": 3}',
                                  json.dumps(emitted(scene="<img src=x onerror=alert(1)>")), "\xff\xfe"])
def test_a_record_nobody_can_judge_leaves_the_queue_once(curator, tmp_path, capsys, body):
    """It used to fail again every three seconds for ever; now it is set aside as .bad, said once, and never read again."""
    module = curator()
    bad = tmp_path / "raw" / "bad.json"
    bad.write_bytes(body.encode("latin-1"))
    os.utime(bad, (time.time() - 60,) * 2)
    good = tmp_path / "raw" / "good.json"
    good.write_text(json.dumps(emitted()))
    for _ in range(3):
        module["curate_all"]()
    out = capsys.readouterr().out
    assert out.count("Set aside bad.json as .bad") == 1 and "Error processing" not in out
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["bad.json.bad"]
    assert (tmp_path / "curated" / "good.json").exists() and not list((tmp_path / "rejected").iterdir())


def test_a_record_still_being_written_gets_another_pass(curator, tmp_path, capsys):
    """Half a file that is seconds old is a writer at work, not a bad record."""
    module = curator()
    raw = tmp_path / "raw" / "young.json"
    raw.write_text(json.dumps(emitted())[:40])
    module["curate_all"]()
    assert raw.exists() and capsys.readouterr().out == ""
    raw.write_text(json.dumps(emitted()))
    module["curate_all"]()
    assert (tmp_path / "curated" / "young.json").exists()


def test_a_disk_that_refuses_the_verdict_is_not_the_records_fault(curator, tmp_path, capsys):
    """An OSError keeps the record in the queue, as it always did: it is tried again, never set aside."""
    module = curator()
    raw = tmp_path / "raw" / "good.json"
    raw.write_text(json.dumps(emitted()))
    os.utime(raw, (time.time() - 60,) * 2)
    (tmp_path / "curated").rmdir()
    module["curate_all"]()
    assert raw.exists() and "Error processing good.json" in capsys.readouterr().out


def test_the_live_lane_prunes_its_queue_and_what_was_set_aside(curator, tmp_path):
    """KEEP_NEWEST bounds raw/ and the .bad files too; the verdict directories as before."""
    module = curator(KEEP_NEWEST="2")
    for i in range(5):
        path = tmp_path / "raw" / f"junk-{i}.json"
        path.write_text("[]")
        os.utime(path, (time.time() - 100 + i,) * 2)
    module["curate_all"]()
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["junk-3.json.bad", "junk-4.json.bad"]
    assert not (tmp_path / "totals.json").exists(), "nothing was judged, so nothing was counted"
    records(tmp_path / "raw", 5)
    module["keep_lane_small"](None)
    assert len(list((tmp_path / "raw").glob("*.json"))) == 2


@pytest.fixture
def receiver(curator):
    """The curator's receiver from the ConfigMap, on loopback; returns start(**settings) -> (module, port)."""
    servers = []

    def start(timeout_s: int = 10, **settings: str) -> tuple[dict, int]:
        module = curator(**settings)
        module["RECEIVER_TIMEOUT_S"] = timeout_s
        servers.append(module["start_receiver"](0, "127.0.0.1"))
        return module, servers[-1].server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def post(port: int, body: bytes) -> tuple[int, bytes]:
    """(status, body) of one POST; (0, b"") when the server hung up on a body it would not read."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", "/episode", body=body, headers={"Content-Type": "application/json"})
        answer = conn.getresponse()
        return answer.status, answer.read()
    except ConnectionError:
        return 0, b""
    finally:
        conn.close()


def test_the_receiver_stores_what_the_emitter_sends_and_answers_as_before(receiver, tmp_path):
    """200 ok, the record whole under its id, nothing half-written left behind; an eval run is still acknowledged and dropped."""
    _, port = receiver()
    assert post(port, json.dumps(emitted()).encode()) == (200, b"ok")
    assert json.loads((tmp_path / "raw" / f"{emitted()['episode_id']}.json").read_text()) == emitted()
    assert post(port, json.dumps(emitted(episode_id="e2", model_version="eval-act-v2")).encode()) == (200, b"discarded")
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == [f"{emitted()['episode_id']}.json"]


@pytest.mark.parametrize("body, statuses", [
    pytest.param(b"[]", (400,), id="a list"), pytest.param(b'"x"', (400,), id="a string"), pytest.param(b"{half", (400,), id="half an object"),
    pytest.param(b"", (400,), id="nothing"),
    pytest.param(b'{"episode_id": "e", "avg_smoothness": NaN}', (400,), id="NaN"),
    pytest.param(b'{"episode_id": "e", "rollout": {"duration_s": Infinity}}', (400,), id="Infinity"),
    pytest.param(json.dumps(emitted(scene="<img src=x onerror=alert(1)>")).encode(), (400,), id="markup in scene"),
    pytest.param(json.dumps(emitted(rollout={"status": "<svg onload=alert(1)>", "steps": 900})).encode(), (400,), id="markup in rollout.status"),
    pytest.param(json.dumps(emitted(episode_id="../curated/evil")).encode(), (400,), id="an id that leaves raw"),
    # 413 is sent without reading the body, as before: a client still writing may see the hang-up instead of the answer
    pytest.param(json.dumps(emitted(pad="x" * 70_000)).encode(), (413, 0), id="over the body limit"),
])
def test_the_receiver_refuses_what_is_not_an_episode(receiver, tmp_path, body, statuses):
    """Not an object, not JSON a browser would parse, markup in a displayed field, too large: refused, nothing stored."""
    _, port = receiver(MAX_BODY_BYTES="65536")
    assert post(port, body)[0] in statuses
    assert not list((tmp_path / "raw").iterdir())


def test_a_content_length_that_is_no_number_is_a_400_not_a_dead_thread(receiver):
    """The header is parsed inside the handler's try."""
    _, port = receiver()
    for value in ("many", "-5"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(f"POST /episode HTTP/1.0\r\nContent-Length: {value}\r\n\r\n".encode())
            assert sock.recv(64).startswith(b"HTTP/1.0 400")


def test_a_client_that_stalls_holds_up_nobody_and_is_dropped(receiver, tmp_path):
    """Headers, a large Content-Length, then silence: the next POST is served at once, and the silent one is closed after the timeout."""
    _, port = receiver(timeout_s=1)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as stalled:
        stalled.sendall(b"POST /episode HTTP/1.0\r\nContent-Length: 60000\r\n\r\n{")
        began = time.monotonic()
        assert post(port, json.dumps(emitted()).encode()) == (200, b"ok")
        assert time.monotonic() - began < 1, "the second client did not wait for the first"
        stalled.settimeout(5)
        answer = stalled.recv(64)
        assert answer == b"" or answer.startswith(b"HTTP/1.0 400"), "dropped once the socket timeout passed"
    assert len(list((tmp_path / "raw").glob("*.json"))) == 1

# ---- curator-show.yaml: what the second deployment is, and is not ----

def test_the_show_curator_runs_the_flywheels_own_code_and_gates():
    """Same ConfigMap, same image, same command, same thresholds: the verdict shown is the one the flywheel would give."""
    real, show = one("curator.yaml", "Deployment", "curator"), one("curator-show.yaml", "Deployment", "curator-show")
    for key in ("image", "command"):
        assert container(show)[key] == container(real)[key]
    for key in ("PASS_THRESHOLD", "SMOOTHNESS_THRESHOLD"):
        assert env(show)[key]["value"] == env(real)[key]["value"]
    (code,) = [v for v in show["spec"]["template"]["spec"]["volumes"] if v["name"] == "code"]
    assert code == {"name": "code", "configMap": {"name": "curator-code"}}
    assert not [d for d in documents("curator-show.yaml") if d["kind"] == "ConfigMap"]


def test_the_show_curator_has_no_way_to_the_dataset_or_the_trigger():
    """No pipeline variable, no credential, no token, no host path - and an SCC that would refuse one."""
    show = one("curator-show.yaml", "Deployment", "curator-show")
    pod = show["spec"]["template"]["spec"]
    assert not [name for name in env(show) if PIPELINE_NAMES.search(name)]
    assert not [e for e in env(show).values() if "valueFrom" in e] and "envFrom" not in container(show)
    assert [sorted(v) for v in pod["volumes"]] == [["configMap", "name"], ["name", "persistentVolumeClaim"]]
    assert pod["serviceAccountName"] == "curator-show" and pod["automountServiceAccountToken"] is False
    assert one("curator-show.yaml", "ServiceAccount", "curator-show")["automountServiceAccountToken"] is False
    assert show["spec"]["template"]["metadata"]["annotations"]["openshift.io/required-scc"] == "restricted-v2"
    granted = [s["name"] for d in documents("scc-rolebinding.yaml") if d["kind"] == "RoleBinding" for s in d["subjects"]]
    assert "curator-show" not in granted and "default" in granted, "the default account may mount host paths: the show curator must not be it"
    policy = one("curator-show.yaml", "NetworkPolicy", "curator-show-host-only")["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app": "curator-show"}} and policy["egress"] == []
    assert sorted(policy["policyTypes"]) == ["Egress", "Ingress"]
    assert show["spec"]["template"]["metadata"]["labels"] == {"app": "curator-show"}


def test_only_the_dashboard_reads_the_show_volume_and_only_read_only():
    """The sync agent, the rejected mirror and the consumer never see it; nothing mounts it writable but its curator."""
    users = {}
    for path in sorted(FLYWHEEL.glob("*.yaml")):
        text = path.read_text()
        if path.name not in ("curator-show.yaml", "dashboard.yaml", "curator.yaml"):
            assert "curator-show" not in text and "show-lane" not in text, path.name
        for doc in (d for d in yaml.safe_load_all(text) if d):
            pod = doc.get("spec", {}).get("template", {}).get("spec") or doc.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec") or {}
            for volume in pod.get("volumes", []):
                claim = volume.get("persistentVolumeClaim", {})
                if claim.get("claimName") == "curator-show-episodes":
                    users[doc["metadata"]["name"]] = (claim.get("readOnly", False), volume["name"], pod)
    assert set(users) == {"curator-show", "dashboard"}
    read_only, name, pod = users["dashboard"]
    (mount,) = [m for c in pod["containers"] for m in c["volumeMounts"] if m["name"] == name]
    assert read_only is True and mount["readOnly"] is True
    assert env(one("dashboard.yaml", "Deployment", "dashboard"))["SHOW_LANE_DIR"]["value"] == mount["mountPath"]


def test_the_show_curators_nodeport_is_its_own_and_the_bar_can_always_find_its_round():
    """30812 nowhere else in gitops/, and enough passes are kept to find the one that completed a round."""
    ports = [p for path in (ROOT / "gitops").rglob("*.yaml") for p in re.findall(r"nodePort:\s*(\d+)", path.read_text())]
    assert ports.count("30812") == 1 and len(ports) == len(set(ports))
    assert one("curator-show.yaml", "Service", "curator-show")["spec"]["ports"][0]["nodePort"] == 30812
    keep = int(env(one("curator-show.yaml", "Deployment", "curator-show"))["KEEP_NEWEST"]["value"])
    assert keep >= int(env(one("dashboard.yaml", "Deployment", "dashboard"))["TRIGGER_THRESHOLD"]["value"])


def test_argo_renders_the_whole_directory_so_the_new_file_is_picked_up():
    """A plain directory source: no kustomization or chart that would have to list curator-show.yaml."""
    source = yaml.safe_load((ROOT / "argocd" / "flywheel-app.yaml").read_text())["spec"]["source"]
    assert source["path"] == "gitops/flywheel" and "directory" not in source and "kustomize" not in source and "helm" not in source
    assert not [p.name for p in FLYWHEEL.iterdir() if p.name.lower() in ("kustomization.yaml", "kustomization.yml", "chart.yaml")]


def test_only_the_gpu_host_may_reach_the_show_curators_port():
    """One source, one port - and the Service keeps the client's address, or the rule would see the node's instead."""
    policy = one("curator-show.yaml", "NetworkPolicy", "curator-show-host-only")["spec"]
    host = re.search(r"<ip address='([0-9.]+)'", (ROOT / "tools" / "host" / "fury" / "fury-net.xml").read_text()).group(1)
    assert policy["ingress"] == [{"from": [{"ipBlock": {"cidr": f"{host}/32"}}], "ports": [{"protocol": "TCP", "port": 8082}]}]
    service = one("curator-show.yaml", "Service", "curator-show")["spec"]
    assert service["externalTrafficPolicy"] == "Local" and service["ports"][0]["targetPort"] == 8082
    assert "livenessProbe" not in container(one("curator-show.yaml", "Deployment", "curator-show")), "a probe would need a rule of its own"


def test_both_curators_move_to_new_code_together():
    """One code, two deployments: the same CURATOR_CODE_REV, so neither lane adopts a ConfigMap change whenever a pod happens to restart."""
    real, show = env(one("curator.yaml", "Deployment", "curator")), env(one("curator-show.yaml", "Deployment", "curator-show"))
    assert real["CURATOR_CODE_REV"]["value"] == show["CURATOR_CODE_REV"]["value"] != ""
    assert "MAX_BODY_BYTES" not in real and show["MAX_BODY_BYTES"]["value"] == "65536"


def test_the_show_volume_names_its_class_and_the_class_makes_it_writable():
    """local-path is a directory on the node: no quota, no fsGroup - writable because the provisioner's script makes it so."""
    claim = one("curator-show.yaml", "PersistentVolumeClaim", "curator-show-episodes")["spec"]
    assert claim["storageClassName"] == "local-path"
    assert "securityContext" not in one("curator-show.yaml", "Deployment", "curator-show")["spec"]["template"]["spec"], \
        "restricted-v2 assigns fsGroup from the namespace's range; a fixed one breaks where the range differs"
    storage = (ROOT / "gitops" / "storage" / "local-path-provisioner.yaml").read_text()
    assert 'mkdir -m 0777 -p "$VOL_DIR"' in storage and 'chcon -Rt container_file_t "$VOL_DIR"' in storage


# ---- the dashboard's live-lane helpers ----

@pytest.fixture(scope="module")
def helpers() -> dict:
    return functions(embedded("dashboard.yaml", "dashboard-code", "dashboard.py"), "show_totals", "show_progress", "newest_records")


@pytest.mark.parametrize("passed, newest_first, now, want", [
    (0, [], 1000.0, (0, 0, False)),
    (37, [990.0] * 37, 1000.0, (37, 23, False)),
    (159, [990.0] * 159, 1000.0, (159, 99, False)),
    (160, [990.0] * 160, 1000.0, (160, 100, True)),                    # the pass that completed the round, 10 s ago
    (161, [995.0, 990.0] + [900.0] * 159, 1000.0, (160, 100, True)),   # one more since: the moment is still up
    (161, [995.0, 600.0] + [500.0] * 159, 1000.0, (1, 1, False)),      # 400 s on: wrapped
    (320, [990.0] * 300, 1000.0, (160, 100, True)),                    # the second round
    (485, [100.0] * 300, 1000.0, (5, 3, False)),
    (161, [995.0], 1000.0, (1, 1, False)),                             # the completing pass is not on the volume: no moment
])
def test_the_bar_is_passed_mod_threshold_and_holds_the_threshold_for_a_while(helpers, passed, newest_first, now, want):
    """Crossing 160 shows the full bar for hold_s, then the count starts over."""
    got = helpers["show_progress"](passed, newest_first, 160, now, 300.0)
    assert (got["progress"], got["pct"], got["triggered"]) == want and got["threshold"] == 160


def test_the_totals_are_the_show_curators_and_never_less_than_the_volume(helpers, tmp_path):
    """totals.json outlives the pruning; without it, or with a broken one, the page counts what is there."""
    totals = helpers["show_totals"]
    assert totals(tmp_path, 4, 2) == (4, 2, 0)
    (tmp_path / "totals.json").write_text(json.dumps({"pass": 412, "reject": 97, "since": 1_790_000_000}))
    assert totals(tmp_path, 300, 97) == (412, 97, 1_790_000_000)
    assert totals(tmp_path, 500, 120) == (500, 120, 1_790_000_000)
    (tmp_path / "totals.json").write_text("{half")
    assert totals(tmp_path, 4, 2) == (4, 2, 0)


def test_a_full_volume_cannot_cost_the_page_its_memory(helpers, tmp_path):
    """Everything is stat'ed, only the newest few small files are read: oversized ones never, and never more than the cap."""
    newest = helpers["newest_records"]
    curated, rejected = tmp_path / "curated", tmp_path / "rejected"
    for d, verdict, count in ((curated, "pass", 40), (rejected, "reject", 40)):
        d.mkdir()
        for i in range(count):
            path = d / f"{verdict}-{i:02d}.json"
            path.write_text(json.dumps({"episode_id": path.stem}))
            os.utime(path, (2_000_000 - i * 10 - (verdict == "reject"), ) * 2)
    big = curated / "big.json"
    big.write_text(json.dumps({"episode_id": "big", "pad": "x" * 70_000}))
    os.utime(big, (3_000_000, 3_000_000))
    (rejected / "list.json").write_text("[1, 2, 3]")
    os.utime(rejected / "list.json", (2_999_999, 2_999_999))
    sources = [(curated, "pass"), (tmp_path / "sent", "pass"), (rejected, "reject")]
    rows = newest(sources, 30, 65536, 120)
    assert [r["episode_id"] for r in rows[:4]] == ["pass-00", "reject-00", "pass-01", "reject-01"] and len(rows) == 30
    assert all(r["_verdict"] == ("pass" if r["episode_id"].startswith("pass") else "reject") for r in rows)
    assert "big" not in [r["episode_id"] for r in newest(sources, 200, 65536, 500)], "a file over the size cap is never read"
    assert len(newest(sources, 200, 65536, 10)) == 9, "ten files looked at, one of them not a record: the scan stops at its cap"
    assert newest(sources, 1, 65536, 120)[0]["episode_id"] == "pass-00"


def test_in_show_mode_the_snapshot_never_asks_the_manifest_consumer():
    """The live lane's branch of status_snapshot has no call into the lineage code."""
    tree = ast.parse(embedded("dashboard.yaml", "dashboard-code", "dashboard.py"))
    (snapshot,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "status_snapshot"]
    (branch,) = [n for n in snapshot.body if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "SHOW_LANE"]

    def called(nodes: list[ast.stmt]) -> set[str]:
        return {c.func.id for n in nodes for c in ast.walk(n) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

    assert not called(branch.body) & {"_lineage", "_lineage_count", "get_model_version"}
    assert {"_lineage", "_lineage_count"} <= called(branch.orelse)


# ---- the backend itself, where flask is installed (.plans/show-lane/.venv; skipped elsewhere) ----

@pytest.fixture
def backend(tmp_path, monkeypatch):
    """dashboard.py from the ConfigMap, loaded for real; returns load(show) -> (module scope, flask test client)."""
    pytest.importorskip("flask")
    lane = tmp_path / "show-lane"
    (tmp_path / "index.html").write_text(PAGE)

    def load(show: bool) -> tuple[dict, object]:
        monkeypatch.setenv("HTML_FILE", str(tmp_path / "index.html"))
        monkeypatch.setenv("TRIGGER_THRESHOLD", "160")
        monkeypatch.setenv("KUBECONFIG", str(tmp_path / "no-cluster"))
        monkeypatch.setenv("SHOW_LANE_DIR", str(lane)) if show else monkeypatch.delenv("SHOW_LANE_DIR", raising=False)
        scope: dict = {"__name__": "dashboard_under_test"}
        exec(compile(embedded("dashboard.yaml", "dashboard-code", "dashboard.py"), "dashboard.py", "exec"), scope)  # noqa: S102 - the repository's own code
        return scope, scope["app"].test_client()
    return load, lane


def judged(lane: Path, verdict: str, count: int, newest: float, model: str = "act-v2-ft160") -> None:
    directory = lane / {"pass": "curated", "reject": "rejected"}[verdict]
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        path = directory / f"{verdict}-{i:03d}.json"
        path.write_text(json.dumps({"episode_id": path.stem, "model_version": model, "curation_score": 1.0 if verdict == "pass" else 0.0,
                                    "curation_verdict": verdict, "cubes_placed": 3 if verdict == "pass" else 1, "dataset_path": None}))
        os.utime(path, (newest - i * 60, newest - i * 60))


def test_the_backend_in_show_mode_reads_the_show_volume_and_nothing_else(backend):
    """Totals from the show curator, the bar mod 160 with the moment held, the badge from the latest episode, no clearing."""
    load, lane = backend
    judged(lane, "pass", 162, time.time() - 20)
    judged(lane, "reject", 9, time.time() - 50)
    (lane / "totals.json").write_text(json.dumps({"pass": 322, "reject": 140, "since": 1_790_000_000}))
    module, client = load(show=True)
    module["_lineage"] = module["_lineage_count"] = lambda *a, **k: pytest.fail("the manifest consumer was consulted")
    got = client.get("/api/status").get_json()
    assert got["show_lane"] is True and got["counts"] == {"raw": 0, "curated": 322, "rejected": 140, "sent": 0, "since": 1_790_000_000}
    # 322 = 2 x 160 + 2: the pass that completed the round is two back, 140 s old: inside the hold
    assert got["trigger"] == {"threshold": 160, "progress": 160, "pct": 100, "triggered": True}
    assert got["model_version"] == "act-v2-ft160" and got["flywheel_running"] is True and got["lineage_since"] == 0.0
    assert len(got["log"]) == 30 and got["log"][0]["episode_id"] == "pass-000"
    module["SHOW_TRIGGER_HOLD_S"] = 100
    assert client.get("/api/status").get_json()["trigger"] == {"threshold": 160, "progress": 2, "pct": 1, "triggered": False}
    assert "const SHOW_LANE = '1' === '1';" in client.get("/").get_data(as_text=True)
    refused = client.post("/api/control/clear")
    assert refused.status_code == 409 and len(list((lane / "curated").glob("*.json"))) == 162
    module["set_replicas"] = lambda *a, **k: pytest.fail("the live lane scaled one of the flywheel's deployments")
    assert client.post("/api/control/flywheel", json={"action": "stop"}).status_code == 409


def test_the_backend_without_the_variable_is_the_flywheels_own(backend):
    """Unset: the flywheel's paths, the old trigger arithmetic, the page's switch empty."""
    load, _ = backend
    module, client = load(show=False)
    assert module["SHOW_LANE"] is False and str(module["EP_CUR"]) == "/data/episodes/curated" and str(module["EP_SENT"]) == "/data/episodes/sent"
    got = client.get("/api/status").get_json()
    assert got["show_lane"] is False and got["trigger"] == {"threshold": 160, "progress": 0, "pct": 0, "triggered": False}
    assert "const SHOW_LANE = '' === '1';" in client.get("/").get_data(as_text=True)


# ---- the page: it says what it shows ----

PAGE = embedded("dashboard.yaml", "dashboard-code", "index.html")
SCRIPT = re.search(r"<script>\n(.*)</script>", PAGE, re.DOTALL).group(1)
LANE = re.search(r"const LANE = \{.*?\n\s*\};", SCRIPT, re.DOTALL).group(0).encode().decode("unicode_escape")

STUB_DOM = r"""
const fs = require('fs'), vm = require('vm');
const els = {};
function el(id) {
  if (!els[id]) {
    const classes = new Set();
    els[id] = { id, style: {}, dataset: {}, textContent: '', innerHTML: '', className: '', children: [], scrollTop: 0,
      classList: { toggle: (c, on) => on ? classes.add(c) : classes.delete(c), add: c => classes.add(c),
                   remove: c => classes.delete(c), contains: c => classes.has(c) },
      addEventListener() {}, appendChild() {}, prepend() {}, removeChild() {}, setAttribute() {}, load() {},
      play: () => Promise.resolve(), querySelector: () => el('inner'), querySelectorAll: () => [] };
  }
  return els[id];
}
let made = 0;
global.document = { getElementById: el, documentElement: el('html'), body: el('body'), addEventListener() {},
                    createElement: () => el('made-' + made++), querySelectorAll: () => [] };
global.window = global;
global.localStorage = { getItem: () => null, setItem() {} };
global.location = { href: 'http://page.example/', search: process.argv[4] || '' };
global.history = { replaceState() {} };
global.EventSource = function () { this.addEventListener = () => {}; };
global.fetch = () => Promise.reject(new Error('no network in a test'));
global.confirm = () => false;
global.setTimeout = () => 0; global.setInterval = () => 0; global.clearTimeout = () => {};
console.log = () => {};                   // the page logs its failed fetches: stdout is the result's alone
el('lane-note').style.display = 'none';   // as in the markup: only the live lane shows it
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
const before = { badge: el('flywheel-status').innerHTML, lane_note: el('lane-note').style.display };
const given = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
applyStatus(given);
(given._prepend || []).forEach(ep => prependLogEntry(ep));
const made_html = Array.from({ length: made }, (_, i) => el('made-' + i).innerHTML).join('\n');
const text = id => el(id).textContent, html = id => el(id).innerHTML;
process.stdout.write(JSON.stringify({ before, badge: html('flywheel-status'), sb_fw: text('sb-fw'), model: text('model-label'),
  prog_text: text('prog-text'), prog_state: text('prog-state'), prog_note: text('prog-note'), gate_note: text('curation-note'),
  banner: text('trigger-banner'), banner_shown: el('trigger-banner').classList.contains('show'), idle: text('gen-empty'),
  log: html('log-body'), total: text('c-total'), total_label: text('c-total-label'), sent: text('c-sent'),
  sent_label: text('c-sent-label'), rejected: text('c-rejected'), lane_note: el('lane-note').style.display,
  since: text('lane-since'), made_html, rollout_status: html('g-rollout-status'), smoothness: html('g-smoothness'),
  ops: el('body').classList.contains('ops') }));
"""


@pytest.fixture
def page(tmp_path):
    """applyStatus run by node against a stand-in DOM, with the page in show mode or not."""
    if not shutil.which("node"):
        pytest.skip("needs node")
    (tmp_path / "dom.js").write_text(STUB_DOM)

    def render(show: bool, status: dict, search: str = "") -> dict:
        (tmp_path / "page.js").write_text(SCRIPT.replace("__SHOW_LANE__", "1" if show else ""))
        (tmp_path / "status.json").write_text(json.dumps(status))
        done = subprocess.run(["node", str(tmp_path / "dom.js"), str(tmp_path / "page.js"), str(tmp_path / "status.json"), search],
                              capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)
    return render


def status(running: bool, progress: int, triggered: bool, passed: int = 0, rejected: int = 0, model: str = "act-v2-ft160") -> dict:
    return {"model_version": model, "promoted": bool(model), "flywheel_running": running, "latest": {}, "log": [], "ts": time.time(),
            "trigger": {"threshold": 160, "progress": progress, "pct": round(progress / 160 * 100), "triggered": triggered},
            "counts": {"raw": 0, "curated": passed, "rejected": rejected, "sent": 0}}


def test_the_page_script_is_valid_javascript(tmp_path):
    """The script lives in YAML, where nothing else would notice a stray brace."""
    if not shutil.which("node"):
        pytest.skip("needs node")
    (tmp_path / "page.js").write_text(SCRIPT)
    done = subprocess.run(["node", "--check", str(tmp_path / "page.js")], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr


def test_the_live_lane_says_what_it_is(page):
    """Serving, judged and not kept: never 'Loop stopped', never 'sent to hub'."""
    got = page(True, status(True, 37, False, passed=357, rejected=120))
    assert "Live lane" in got["before"]["badge"] and got["before"]["lane_note"] == ""
    assert "Serving &mdash; live lane" in got["badge"] and got["sb_fw"] == "live lane"
    assert got["prog_text"] == "37 / 160 episodes passed the curator’s gates" and got["prog_state"] == "judging"
    assert (got["total"], got["total_label"], got["sent"], got["sent_label"], got["rejected"]) == (477, "judged", 357, "passed", 120)
    assert "judged, not kept" in got["prog_note"] and "object storage" in got["prog_note"] and "judged, not kept" in got["gate_note"]
    assert got["lane_note"] == "" and not got["banner_shown"] and got["model"] == "act-v2-ft160"
    assert not [words for words in ("Loop stopped", "sent to hub", "TRIGGERED", "fine-tune starting") if words in json.dumps(got)]


def test_the_live_lane_without_episodes_waits_and_does_not_look_broken(page):
    """Robot zero out of service: a quiet 'waiting', not an error, and no label it cannot know."""
    got = page(True, status(False, 0, False, model=""), search="?ops=1")
    assert "Live lane &mdash; waiting for robot zero" in got["badge"] and got["sb_fw"] == "waiting" and got["prog_state"] == "waiting"
    assert got["idle"].startswith("Live lane") and "Live lane" in got["log"] and got["model"] == "awaiting first episode"
    assert got["ops"] is False, "the clear control is not offered on the live lane"


def test_the_threshold_moment_is_worded_as_where_a_run_would_start(page):
    """At 160 the page points at the moment; it does not claim a run."""
    got = page(True, status(True, 160, True, passed=160, rejected=41))
    assert got["banner_shown"] and got["prog_state"] == "THRESHOLD REACHED" and got["prog_text"].startswith("160 / 160")
    assert "this is where a governed training run would start" in got["banner"]
    assert "nothing was kept and nothing was started" in got["banner"]
    assert "starting" not in got["banner"] and "fine-tune" not in got["banner"]


def test_without_the_variable_the_page_is_the_flywheels_own_as_before(page):
    """Unset is today's behaviour: the same words, the same sums, the clear control behind ?ops=1."""
    got = page(False, {**status(False, 12, False), "counts": {"raw": 1, "curated": 12, "rejected": 5, "sent": 12}}, search="?ops=1")
    assert "Loop stopped" in got["badge"] and got["sb_fw"] == "stopped" and got["prog_state"] == "loop stopped"
    assert got["prog_text"] == "12 / 160 curated episodes sent to hub"
    assert (got["total"], got["sent"], got["rejected"]) == (18, 12, 5)
    assert got["idle"] == "Collection loop stopped — episodes appear here once it runs." and got["ops"] is True
    assert got["lane_note"] == "none" and 'id="lane-note" class="tag" style="display:none' in PAGE, "the live lane's note stays hidden"
    running = page(False, status(True, 160, True))
    assert "Flywheel running" in running["badge"] and running["prog_state"] == "TRIGGERED" and running["banner"] == ""


HOSTILE = "<img src=x onerror=alert(1)>"


@pytest.mark.parametrize("show", [True, False])
def test_no_field_of_a_record_reaches_the_page_as_markup(page, show):
    """M1: records arrive over an open port. Markup in any field a log row or the rollout panel shows comes out as text, on both lanes."""
    hostile = {"episode_id": HOSTILE, "verdict": HOSTILE, "curation_score": HOSTILE, "curation_reason": f"low {HOSTILE}", "scene": HOSTILE,
               "rollout": {"status": HOSTILE, "steps": HOSTILE, "duration_s": HOSTILE}, "task_success": True, "cubes_placed": HOSTILE,
               "avg_smoothness": HOSTILE, "ts": HOSTILE}
    passed = {**hostile, "episode_id": "p", "verdict": "pass"}
    rejected = {**hostile, "episode_id": "r", "verdict": "reject", "curation_reason": f"task-failed {HOSTILE}"}
    latest = {"scene": HOSTILE, "rollout": hostile["rollout"], "task_success": True, "cubes_placed": HOSTILE, "avg_smoothness": HOSTILE,
              "curation_score": 0.5, "curation_reason": HOSTILE, "curation_verdict": HOSTILE}
    got = page(show, {**status(True, 3, False), "latest": latest, "log": [hostile, passed, rejected], "_prepend": [hostile, passed, rejected]})
    rendered = "\n".join((got["made_html"], got["rollout_status"], got["smoothness"], got["log"], got["badge"]))
    assert got["made_html"].count("log-main") == 6, "three rows rebuilt from the status, three prepended"
    assert "<img" not in rendered and "onerror=alert(1)>" not in rendered
    assert "&#60;img src=x onerror=alert(1)&#62;" in got["made_html"] and "&#60;img" in got["rollout_status"]


def test_the_count_shows_since_when_it_has_been_counting(page):
    """S4: a number on the page is never without its age; without one from the curator, nothing is claimed."""
    got = page(True, {**status(True, 37, False, passed=357, rejected=120), "counts": {"raw": 0, "curated": 357, "rejected": 120, "sent": 0,
                                                                                    "since": time.time() - 7200}})
    assert re.fullmatch(r"\u00b7 since \S.*\d{1,2}[:.]\d{2}.*", got["since"]), got["since"]
    assert page(True, status(True, 37, False))["since"] == ""
    assert page(False, {**status(True, 37, False), "counts": {"raw": 0, "curated": 1, "rejected": 0, "sent": 1, "since": 1}})["since"] == ""


def test_audience_facing_text_names_no_storage_product_and_the_record_stays():
    """'object storage', never the product; the pinned evaluation link and the v1-vs-v2 clips are still on the page."""
    assert "minio" not in PAGE.lower()
    assert "live lane: judged, not kept" in PAGE and "<!-- __EVAL_LINK__ -->" in PAGE and 'id="paired-card"' in PAGE
    assert "this is where a governed training run would start" in LANE and "object storage" in LANE
    backend = embedded("dashboard.yaml", "dashboard-code", "dashboard.py")
    assert 'replace("__SHOW_LANE__", "1" if SHOW_LANE else "")' in backend and '"show_lane": SHOW_LANE' in backend

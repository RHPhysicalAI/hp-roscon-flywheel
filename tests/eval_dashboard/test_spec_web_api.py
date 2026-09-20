# This project was developed with assistance from AI tools.
"""Spec section 5: the web API, through Flask's test client."""
import pytest

from eval_dashboard.web.app import create_app

ENV_FIELDS = ("LIVE_DASHBOARD_URL", "SOURCE_LABEL", "OTHER_VIEW_URL", "OTHER_VIEW_LABEL")

PAIRED = {
    "run_id": "r1",
    "candidate": "act-v2-ft160",
    "incumbent": "upstream-act-teacher",
    "n_paired": 360,
    "fixed": 57,
    "broken": 19,
    "net": 38,
    "sign_test_p": 0.0,
    "verdict": "PASS",
    "rule": "promote iff net > 0 and p < 0.05 (D022)",
    "candidate_success_rate": 0.925,
    "incumbent_success_rate": 0.8194,
    "delta": 0.1056,
    "timestamp": "2026-09-20T01:02:03Z",
    "fixed_seeds": [1001, 1004, 1005],
    "broken_seeds": [1002, 1008],
}


class EpisodeStore:
    def __init__(self, episodes=(), snapshot=None):
        self._episodes = list(episodes)
        self._snapshot = snapshot if snapshot is not None else {}

    def episodes(self):
        return list(self._episodes)

    def snapshot(self):
        return self._snapshot


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with none of the env-driven fields set."""
    for name in ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)


def stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    return response.get_json()


# --- /api/paired -----------------------------------------------------------


def test_paired_unavailable_without_a_provider():
    """With no paired_provider the endpoint answers 200 {"available": false}."""
    response = create_app(EpisodeStore(), "files").test_client().get("/api/paired")
    assert response.status_code == 200
    assert response.get_json() == {"available": False}


def test_paired_unavailable_with_an_explicit_none_provider():
    """paired_provider=None is the same as leaving it out."""
    response = create_app(EpisodeStore(), "live", paired_provider=None).test_client().get("/api/paired")
    assert response.status_code == 200
    assert response.get_json() == {"available": False}


def test_paired_unavailable_when_the_provider_returns_none():
    """A provider that has nothing yet answers 200 {"available": false}."""
    response = create_app(EpisodeStore(), "eval", paired_provider=lambda: None).test_client().get("/api/paired")
    assert response.status_code == 200
    assert response.get_json() == {"available": False}


def test_paired_available_carries_the_whole_payload():
    """A payload from the provider is returned with available true."""
    response = create_app(EpisodeStore(), "eval", paired_provider=lambda: dict(PAIRED)).test_client().get("/api/paired")
    assert response.status_code == 200
    assert response.get_json() == {"available": True, **PAIRED}


@pytest.mark.parametrize("key", sorted(PAIRED))
def test_paired_available_has_each_named_field(key):
    """Each field the spec names is present with the provider's value."""
    body = create_app(EpisodeStore(), "eval", paired_provider=lambda: dict(PAIRED)).test_client().get("/api/paired").get_json()
    assert body["available"] is True
    assert body[key] == PAIRED[key]


def test_paired_keeps_a_zero_p_value():
    """A sign_test_p of 0.0 reaches the client as 0.0."""
    body = create_app(EpisodeStore(), "eval", paired_provider=lambda: dict(PAIRED)).test_client().get("/api/paired").get_json()
    assert body["sign_test_p"] == 0.0
    assert body["sign_test_p"] is not None


def test_paired_provider_is_consulted_on_each_request():
    """The endpoint reflects a run that appears after the app was created."""
    state = {"payload": None}
    client = create_app(EpisodeStore(), "eval", paired_provider=lambda: state["payload"]).test_client()
    assert client.get("/api/paired").get_json() == {"available": False}
    state["payload"] = dict(PAIRED)
    assert client.get("/api/paired").get_json()["available"] is True
    state["payload"] = {**PAIRED, "run_id": "r2", "verdict": "FAIL"}
    body = client.get("/api/paired").get_json()
    assert body["run_id"] == "r2"
    assert body["verdict"] == "FAIL"


def test_paired_provider_is_accepted_positionally():
    """paired_provider is create_app's third positional parameter."""
    body = create_app(EpisodeStore(), "eval", lambda: dict(PAIRED)).test_client().get("/api/paired").get_json()
    assert body["available"] is True


# --- /api/stats ------------------------------------------------------------


def test_stats_keeps_todays_fields():
    """source_mode, live_dashboard_url and snapshot are still returned."""
    snapshot = {"episode_count": 0, "versions": {}}
    body = stats(create_app(EpisodeStore(snapshot=snapshot), "files").test_client())
    assert body["source_mode"] == "files"
    assert "live_dashboard_url" in body
    assert body["snapshot"] == snapshot


def test_stats_adds_the_three_new_fields():
    """source_label, other_view_url and other_view_label are returned."""
    body = stats(create_app(EpisodeStore(), "files").test_client())
    assert "source_label" in body
    assert "other_view_url" in body
    assert "other_view_label" in body


def test_stats_live_dashboard_url_has_no_default():
    """With LIVE_DASHBOARD_URL unset the field is an empty string."""
    assert stats(create_app(EpisodeStore(), "files").test_client())["live_dashboard_url"] == ""


def test_stats_live_dashboard_url_empty_env_is_empty(monkeypatch):
    """An empty LIVE_DASHBOARD_URL gives an empty string."""
    monkeypatch.setenv("LIVE_DASHBOARD_URL", "")
    assert stats(create_app(EpisodeStore(), "files").test_client())["live_dashboard_url"] == ""


def test_stats_live_dashboard_url_from_env(monkeypatch):
    """A set LIVE_DASHBOARD_URL is returned as given."""
    monkeypatch.setenv("LIVE_DASHBOARD_URL", "http://ops.example.test:30801")
    assert stats(create_app(EpisodeStore(), "files").test_client())["live_dashboard_url"] == "http://ops.example.test:30801"


@pytest.mark.parametrize(
    "mode, label",
    [("files", "Saved files"), ("live", "Live"), ("eval", "Paired evaluation")],
)
def test_stats_source_label_defaults_by_mode(mode, label):
    """Without SOURCE_LABEL the label follows the source mode."""
    body = stats(create_app(EpisodeStore(), mode).test_client())
    assert body["source_label"] == label
    assert body["source_mode"] == mode


@pytest.mark.parametrize("mode", ["files", "live", "eval"])
def test_stats_source_label_env_wins_in_every_mode(monkeypatch, mode):
    """SOURCE_LABEL overrides the per-mode label."""
    monkeypatch.setenv("SOURCE_LABEL", "Booth replay")
    assert stats(create_app(EpisodeStore(), mode).test_client())["source_label"] == "Booth replay"


def test_stats_other_view_fields_default_to_empty():
    """With the env unset both other-view fields are empty strings."""
    body = stats(create_app(EpisodeStore(), "eval").test_client())
    assert body["other_view_url"] == ""
    assert body["other_view_label"] == ""


def test_stats_other_view_fields_from_env(monkeypatch):
    """OTHER_VIEW_URL and OTHER_VIEW_LABEL are returned as given."""
    monkeypatch.setenv("OTHER_VIEW_URL", "http://live-view.example.test:8080")
    monkeypatch.setenv("OTHER_VIEW_LABEL", "Live results")
    body = stats(create_app(EpisodeStore(), "eval").test_client())
    assert body["other_view_url"] == "http://live-view.example.test:8080"
    assert body["other_view_label"] == "Live results"


def test_stats_other_view_fields_are_independent(monkeypatch):
    """Setting only the URL leaves the label empty."""
    monkeypatch.setenv("OTHER_VIEW_URL", "http://live-view.example.test:8080")
    body = stats(create_app(EpisodeStore(), "eval").test_client())
    assert body["other_view_url"] == "http://live-view.example.test:8080"
    assert body["other_view_label"] == ""


def test_stats_env_is_read_per_request(monkeypatch):
    """Env changes after the app was created show up on the next request."""
    client = create_app(EpisodeStore(), "live").test_client()
    first = stats(client)
    assert first["source_label"] == "Live"
    assert first["live_dashboard_url"] == ""
    assert first["other_view_url"] == ""

    monkeypatch.setenv("SOURCE_LABEL", "Live from the cell")
    monkeypatch.setenv("LIVE_DASHBOARD_URL", "http://ops.example.test:30801")
    monkeypatch.setenv("OTHER_VIEW_URL", "http://comparison.example.test:8080")
    monkeypatch.setenv("OTHER_VIEW_LABEL", "Paired comparison")
    second = stats(client)
    assert second["source_label"] == "Live from the cell"
    assert second["live_dashboard_url"] == "http://ops.example.test:30801"
    assert second["other_view_url"] == "http://comparison.example.test:8080"
    assert second["other_view_label"] == "Paired comparison"

    monkeypatch.delenv("SOURCE_LABEL")
    assert stats(client)["source_label"] == "Live"


def test_stats_with_a_paired_provider_still_answers():
    """Passing a paired_provider does not change /api/stats."""
    body = stats(create_app(EpisodeStore(), "eval", paired_provider=lambda: dict(PAIRED)).test_client())
    assert body["source_mode"] == "eval"
    assert body["source_label"] == "Paired evaluation"


def test_index_page_is_served():
    """The static page is still served at the root."""
    response = create_app(EpisodeStore(), "eval").test_client().get("/")
    assert response.status_code == 200

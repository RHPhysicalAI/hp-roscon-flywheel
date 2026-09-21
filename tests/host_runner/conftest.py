# This project was developed with assistance from AI tools.
"""Shared fixtures for host_runner tests: fresh, isolated imports of the module under test."""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types
from pathlib import Path

import pytest

# tests/host_runner/conftest.py -> repo root -> src/host-runner/host_runner.py
# (the hyphen in "host-runner" means this can't be imported as a normal package)
MODULE_PATH = Path(__file__).resolve().parents[2] / "src" / "host-runner" / "host_runner.py"

_import_counter = itertools.count()


def _install_stub_modules(monkeypatch):
    """Inject fake boto3/kafka/botocore modules so host_runner imports without those deps."""
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "boto3", boto3_stub)

    kafka_stub = types.ModuleType("kafka")
    kafka_stub.KafkaConsumer = object
    kafka_stub.KafkaProducer = object
    monkeypatch.setitem(sys.modules, "kafka", kafka_stub)

    botocore_stub = types.ModuleType("botocore")
    monkeypatch.setitem(sys.modules, "botocore", botocore_stub)

    botocore_config_stub = types.ModuleType("botocore.config")
    botocore_config_stub.Config = object
    botocore_stub.config = botocore_config_stub
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config_stub)


@pytest.fixture
def load_runner(monkeypatch):
    """Return a helper importing host_runner.py fresh, per call, with env/stub deps set up.

    Usage: ``runner = load_runner(RUNNER_MODE="inprocess", SOME_VAR=None)``. The two MinIO
    keys are always set to dummy values first so import succeeds by default; pass a key with
    value ``None`` to delete it instead (e.g. to exercise the SystemExit-on-missing-key path).
    Every call gets a brand-new module object, since configuration is read at import time.
    """

    def _load(**env):
        monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
        monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret-key")
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, str(value))

        _install_stub_modules(monkeypatch)

        module_name = f"host_runner_under_test_{next(_import_counter)}"
        spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        return module

    return _load

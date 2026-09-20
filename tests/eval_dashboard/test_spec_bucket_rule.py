# This project was developed with assistance from AI tools.
"""Spec section 6: the Kafka path only fetches from the configured buckets."""
import pytest

from eval_dashboard.main import Store
from eval_dashboard.sources import minio_source

CONFIGURED = ["episodes-curated", "episodes-rejected"]


def test_uri_in_the_curated_bucket_is_allowed():
    """A URI naming the curated bucket is in the set."""
    assert minio_source.uri_in_buckets("s3://episodes-curated/2026/09/20/ep-0001.json", CONFIGURED) is True


def test_uri_in_the_rejected_bucket_is_allowed():
    """A URI naming the rejected bucket is in the set."""
    assert minio_source.uri_in_buckets("s3://episodes-rejected/ep-0002.json", CONFIGURED) is True


def test_uri_in_another_bucket_is_refused():
    """A URI naming any other bucket is not in the set."""
    assert minio_source.uri_in_buckets("s3://episodes-data/eval/r1/eval_report.json", CONFIGURED) is False


def test_bucket_name_must_match_exactly():
    """A bucket whose name merely starts with a configured one is refused."""
    assert minio_source.uri_in_buckets("s3://episodes-curated-old/ep-0001.json", CONFIGURED) is False
    assert minio_source.uri_in_buckets("s3://episodes/ep-0001.json", CONFIGURED) is False


def test_bucket_name_in_the_key_does_not_count():
    """A configured bucket name inside the object key does not make the URI allowed."""
    assert minio_source.uri_in_buckets("s3://scratch/episodes-curated/ep-0001.json", CONFIGURED) is False


@pytest.mark.parametrize("uri", ["", "   ", "not-a-uri", "episodes-curated/ep-0001.json", "s3://", "s3:///ep-0001.json"])
def test_malformed_or_empty_uri_is_refused(uri):
    """A URI that is empty or names no bucket is refused."""
    assert minio_source.uri_in_buckets(uri, CONFIGURED) is False


def test_no_configured_buckets_refuses_everything():
    """With an empty bucket set nothing is allowed."""
    assert minio_source.uri_in_buckets("s3://episodes-curated/ep-0001.json", []) is False


@pytest.mark.parametrize(
    "buckets",
    [
        ("episodes-curated", "episodes-rejected"),
        {"episodes-curated", "episodes-rejected"},
        frozenset(CONFIGURED),
    ],
)
def test_buckets_may_be_any_iterable(buckets):
    """Tuples and sets work as well as lists."""
    assert minio_source.uri_in_buckets("s3://episodes-rejected/ep-0002.json", buckets) is True
    assert minio_source.uri_in_buckets("s3://episodes-data/ep-0003.json", buckets) is False


def test_buckets_may_be_a_generator():
    """A one-shot iterable of bucket names is accepted."""
    assert minio_source.uri_in_buckets("s3://episodes-rejected/ep-0002.json", (name for name in CONFIGURED)) is True


def test_result_is_a_bool():
    """The helper returns a real bool either way."""
    assert isinstance(minio_source.uri_in_buckets("s3://episodes-curated/ep.json", CONFIGURED), bool)
    assert isinstance(minio_source.uri_in_buckets("", CONFIGURED), bool)


def test_skip_count_appears_in_the_integrity_payload():
    """The snapshot carries kafka_skipped_other_bucket, zero on a fresh store."""
    snapshot = Store().snapshot()
    assert "kafka_skipped_other_bucket" in snapshot
    assert snapshot["kafka_skipped_other_bucket"] == 0

# This project was developed with assistance from AI tools.
"""Reads a frozen directory of episode/manifest JSON files.

This is the booth/offline mode: no Kafka or MinIO required. Accepts:

- a flat directory of `<episode_id>.json` files
- MinIO's key layout, `<model_version>/<episode_id>.json`
- a JSON *array* of episode records in one file
- a `{episode_id, ...}` single record, or a `{episodes: [...]}` wrapper

an rglob covers the directory shapes without caring which one it's given.

A directory somebody else keeps writing to can be bounded: `newest` reads only
the most recently modified files, `max_bytes` never opens a larger one. Both
are found by stat alone, and only `newest` candidates are held at a time, so
the cost of a full volume is a directory walk. 0, the default, is no bound.
"""
from __future__ import annotations

import heapq
import json
import logging
import pathlib
from typing import Iterator

from eval_dashboard import schema

log = logging.getLogger("eval_dashboard.files")


def _iter_raw(raw) -> Iterator[dict]:
    if isinstance(raw, list):
        yield from (item for item in raw if isinstance(item, dict))
    elif isinstance(raw, dict):
        if raw.get("episode_id"):
            yield raw
        elif isinstance(raw.get("episodes"), list):
            yield from (item for item in raw["episodes"] if isinstance(item, dict))


class FileSource:
    def __init__(self, directory: str, newest: int = 0, max_bytes: int = 0):
        self.directory = pathlib.Path(directory)
        self.newest = newest
        self.max_bytes = max_bytes
        self.skipped = 0

    def _small_enough(self) -> Iterator[tuple[float, str]]:
        """(mtime, path) of every *.json no larger than max_bytes; an oversized file is counted as skipped."""
        for path in self.directory.rglob("*.json"):
            try:
                st = path.stat()
            except OSError:
                continue
            if self.max_bytes > 0 and st.st_size > self.max_bytes:
                self.skipped += 1
                continue
            yield st.st_mtime, str(path)

    def _paths(self) -> list[pathlib.Path]:
        """The files to read, in path order: all of them, or the bounded selection."""
        if self.newest <= 0 and self.max_bytes <= 0:
            return sorted(self.directory.rglob("*.json"))
        found = self._small_enough()
        kept = heapq.nlargest(self.newest, found) if self.newest > 0 else found
        return sorted(pathlib.Path(path) for _, path in kept)

    def read(self) -> Iterator[dict]:
        self.skipped = 0
        if not self.directory.exists():
            return
        for path in self._paths():
            try:
                raw = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                self.skipped += 1
                log.warning("skipping unreadable %s: %s", path, exc)
                continue
            yielded = False
            for item in _iter_raw(raw):
                normalized = schema.normalize(item)
                if normalized:
                    yielded = True
                    yield normalized
                else:
                    self.skipped += 1
            if not yielded and not isinstance(raw, (list, dict)):
                self.skipped += 1

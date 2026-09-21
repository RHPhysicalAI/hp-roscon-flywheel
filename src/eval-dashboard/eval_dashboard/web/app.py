# This project was developed with assistance from AI tools.
"""Read-only Flask app: JSON API + a static comparison UI.

No write endpoints -- this process only ever reads from its configured
sources and serves computed aggregates + the raw records behind them.
"""
from __future__ import annotations

import os
import pathlib

from flask import Flask, jsonify, request, send_from_directory

STATIC_DIR = pathlib.Path(__file__).parent / "static"

SOURCE_LABELS = {"files": "Saved files", "live": "Live", "eval": "Paired evaluation"}


def http_url(value: str) -> str:
    """The value when it is an http(s) address, else empty: the page makes a link of it."""
    value = value.strip()
    return value if value.startswith(("http://", "https://")) else ""


def create_app(store, source_mode: str, paired_provider=None) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(
            {
                "source_mode": source_mode,
                "source_label": os.environ.get("SOURCE_LABEL") or SOURCE_LABELS.get(source_mode, source_mode),
                "live_dashboard_url": os.environ.get("LIVE_DASHBOARD_URL", ""),
                "other_view_url": os.environ.get("OTHER_VIEW_URL", ""),
                "other_view_label": os.environ.get("OTHER_VIEW_LABEL", ""),
                # What this instance is, in its own words: a heading, and a line above the numbers with one link.
                "page_title": os.environ.get("PAGE_TITLE", ""),
                "page_note": os.environ.get("PAGE_NOTE", ""),
                "page_note_link_url": http_url(os.environ.get("PAGE_NOTE_LINK_URL", "")),
                "page_note_link_label": os.environ.get("PAGE_NOTE_LINK_LABEL", ""),
                "snapshot": store.snapshot(),
            }
        )

    @app.route("/api/paired")
    def api_paired():
        payload = paired_provider() if paired_provider is not None else None
        if payload is None:
            return jsonify({"available": False})
        return jsonify({**payload, "available": True})

    @app.route("/api/episodes")
    def api_episodes():
        # Repeating model_version selects their union.  Keeping the sort and
        # pagination here (rather than paging each policy in the browser)
        # preserves one chronological evidence table across a comparison.
        model_versions = set(request.args.getlist("model_version"))
        try:
            limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
            offset = max(int(request.args.get("offset", 0)), 0)
        except ValueError:
            return jsonify({"error": "limit and offset must be integers"}), 400

        episodes = store.episodes()
        if model_versions:
            episodes = [r for r in episodes if r["model_version"] in model_versions]
        episodes.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        return jsonify(episodes[offset : offset + limit])

    return app

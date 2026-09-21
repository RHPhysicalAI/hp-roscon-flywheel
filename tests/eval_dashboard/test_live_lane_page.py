# This project was developed with assistance from AI tools.
"""Files mode over a directory somebody else keeps writing to: a bounded read, a page that says what it is, and no record field that reaches the page as markup."""
import ast
import json
import os
import pathlib
import shutil
import subprocess

import pytest

from eval_dashboard import aggregate, main
from eval_dashboard.sources.file_source import FileSource
from eval_dashboard.web.app import create_app

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "src" / "eval-dashboard" / "eval_dashboard"
STATIC = PACKAGE / "web" / "static"
PAGE_ENV = ("PAGE_TITLE", "PAGE_NOTE", "PAGE_NOTE_LINK_URL", "PAGE_NOTE_LINK_LABEL", "SOURCE_LABEL", "OTHER_VIEW_URL",
            "OTHER_VIEW_LABEL", "LIVE_DASHBOARD_URL", "FILES_NEWEST", "FILES_MAX_BYTES")
HOSTILE = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with none of the settings under test."""
    for name in PAGE_ENV:
        monkeypatch.delenv(name, raising=False)


def judged(root: pathlib.Path, name: str, passed: bool, mtime: float, **changes) -> pathlib.Path:
    """One record as the curator leaves it: curated/ or rejected/, the verdict inside the record."""
    directory = root / ("curated" if passed else "rejected")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"episode_id": name, "model_version": "act-v2-ft160", "has_failure": False, "task_success": passed,
                                "cubes_placed": 3 if passed else 1, "avg_smoothness": 0.004, "scene": "place_cubes_on_tray",
                                "rollout": {"status": "ok", "steps": 900, "duration_s": 41.0}, "dataset_path": None,
                                "curation_verdict": "pass" if passed else "reject", **changes}))
    os.utime(path, (mtime, mtime))
    return path


# --- the bounded read ---------------------------------------------------------


def test_verdict_directories_are_read_as_one_population_and_the_record_carries_the_verdict(tmp_path):
    """Unbounded, as ever: curated/ and rejected/ under one root, the verdict taken from the record."""
    judged(tmp_path, "p1", True, 1000)
    judged(tmp_path, "r1", False, 1001)
    records = list(FileSource(str(tmp_path)).read())
    assert {r["episode_id"]: r["curation_verdict"] for r in records} == {"p1": "pass", "r1": "reject"}
    stats = aggregate.aggregate(records)["act-v2-ft160"]
    assert (stats.episode_count, stats.success_count, stats.success_rate_incomplete) == (2, 1, False)


def test_newest_is_the_last_episodes_judged_not_two_full_directories(tmp_path):
    """A curator that keeps the newest 5 of EACH verdict leaves 5 + 5 whatever the policy does; the newest 5 of both are the last 5 judged."""
    # 50 episodes, every tenth a reject: the volume ends up with passes 45..49 (minus the reject) and rejects from long ago
    kept_passes = [i for i in range(50) if i % 10 != 9][-5:]
    kept_rejects = [i for i in range(50) if i % 10 == 9][-5:]
    for i in kept_passes:
        judged(tmp_path, f"ep-{i:02d}", True, 1000 + i)
    for i in kept_rejects:
        judged(tmp_path, f"ep-{i:02d}", False, 1000 + i)
    everything = aggregate.aggregate(list(FileSource(str(tmp_path)).read()))["act-v2-ft160"]
    assert everything.success_rate == 0.5, "what the two directories say, which is nothing about the policy"
    window = list(FileSource(str(tmp_path), newest=5).read())
    assert sorted(r["episode_id"] for r in window) == ["ep-45", "ep-46", "ep-47", "ep-48", "ep-49"]
    assert aggregate.aggregate(window)["act-v2-ft160"].success_rate == 0.8


def test_an_oversized_file_is_never_opened_and_only_the_newest_are(tmp_path, monkeypatch):
    """Found by stat alone: the size cap and the window decide what is read before anything is."""
    for i in range(6):
        judged(tmp_path, f"ep-{i}", True, 1000 + i)
    big = judged(tmp_path, "big", False, 5000, pad="x" * 70_000)
    opened = []
    read_text = pathlib.Path.read_text
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: (opened.append(self.name), read_text(self, *a, **k))[1])
    source = FileSource(str(tmp_path), newest=2, max_bytes=65536)
    assert [r["episode_id"] for r in source.read()] == ["ep-4", "ep-5"]
    assert opened == ["ep-4.json", "ep-5.json"] and source.skipped == 1 and big.stat().st_size > 65536
    opened.clear()
    capped = FileSource(str(tmp_path), max_bytes=65536)
    assert len(list(capped.read())) == 6 and "big.json" not in opened, "the cap alone: every small file, never the large one"


def test_without_a_bound_every_file_is_read_as_before(tmp_path):
    """0 is off, not 'none': the default reader is the one every other mode and test has always had."""
    for i in range(4):
        judged(tmp_path, f"ep-{i}", True, 1000 + i)
    judged(tmp_path, "big", False, 5000, pad="x" * 70_000)
    assert len(list(FileSource(str(tmp_path)).read())) == 5
    assert len(list(FileSource(str(tmp_path), newest=0, max_bytes=0).read())) == 5


def test_the_bounds_come_from_the_environment_and_default_to_none(monkeypatch, tmp_path):
    """FILES_NEWEST and FILES_MAX_BYTES; unset, the reader is unbounded."""
    source = main.file_source(str(tmp_path))
    assert (source.newest, source.max_bytes) == (0, 0)
    monkeypatch.setenv("FILES_NEWEST", "300")
    monkeypatch.setenv("FILES_MAX_BYTES", "65536")
    source = main.file_source(str(tmp_path))
    assert (source.newest, source.max_bytes, source.directory) == (300, 65536, tmp_path)


def test_every_read_in_files_mode_goes_through_the_bounded_reader():
    """The first load and the poll build their reader in one place, so neither can forget the bounds."""
    tree = ast.parse((PACKAGE / "main.py").read_text())
    built_in = [fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                for call in ast.walk(fn) if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "FileSource"]
    assert built_in == ["file_source"]


# --- the page says what it is ---------------------------------------------------


class EmptyStore:
    def episodes(self):
        return []

    def snapshot(self):
        return {}


def stats(client) -> dict:
    response = client.get("/api/stats")
    assert response.status_code == 200
    return response.get_json()


def test_an_instance_says_nothing_about_itself_unless_told_to():
    """Unset, the four fields are empty and the page is the one it was."""
    body = stats(create_app(EmptyStore(), "files").test_client())
    assert [body[k] for k in ("page_title", "page_note", "page_note_link_url", "page_note_link_label")] == ["", "", "", ""]
    html = (STATIC / "index.html").read_text()
    assert '<div class="notice page-note" id="page-note" hidden>' in html and '<span id="page-note-text"></span>' in html
    assert '<a id="page-note-link" hidden></a>' in html and '<h1 id="page-title">SO-ARM Policy Evaluation</h1>' in html


def test_title_note_and_link_come_from_the_environment(monkeypatch):
    """PAGE_TITLE, PAGE_NOTE and the note's one link are returned as given."""
    monkeypatch.setenv("PAGE_TITLE", "Live episodes - live lane")
    monkeypatch.setenv("PAGE_NOTE", "Judged, not kept.")
    monkeypatch.setenv("PAGE_NOTE_LINK_URL", "https://collection.example.test")
    monkeypatch.setenv("PAGE_NOTE_LINK_LABEL", "Live episodes (collection)")
    body = stats(create_app(EmptyStore(), "files").test_client())
    assert (body["page_title"], body["page_note"]) == ("Live episodes - live lane", "Judged, not kept.")
    assert (body["page_note_link_url"], body["page_note_link_label"]) == ("https://collection.example.test", "Live episodes (collection)")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "//collection.example.test", "collection.example.test"])
def test_the_notes_link_is_an_http_address_or_nothing(monkeypatch, url):
    """The page makes a link of it, so anything that is not http(s) is dropped."""
    monkeypatch.setenv("PAGE_NOTE_LINK_URL", url)
    assert stats(create_app(EmptyStore(), "files").test_client())["page_note_link_url"] == ""


# --- nothing a record or a setting says reaches the page as markup -----------------

STUB_DOM = r"""
const fs = require('fs'), vm = require('vm');
const given = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const markup = [];   // everything the page hands to the HTML parser
const els = {};
let made = 0;
function el(id) {
  if (els[id]) return els[id];
  const attrs = {}, classes = new Set();
  let inner = '';
  const e = { id, style: {}, dataset: {}, textContent: '', hidden: false, open: false, href: '',
    classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), add: c => classes.add(c), remove: c => classes.delete(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: k => (k in attrs ? attrs[k] : null),
    removeAttribute: k => { delete attrs[k]; if (k === 'href') e.href = ''; },
    addEventListener() {}, append() {}, prepend() {}, after() {}, remove() {},
    insertAdjacentHTML: (where, html) => markup.push(String(html)),
    querySelector: selector => el(id + ' ' + selector), querySelectorAll: () => [] };
  Object.defineProperty(e, 'innerHTML', { get: () => inner, set: v => { inner = String(v); markup.push(inner); } });
  return (els[id] = e);
}
global.document = { getElementById: el, documentElement: el('html'), createElement: () => el('made-' + made++), title: '' };
el('html').setAttribute('data-theme', 'dark');
global.window = global;
global.getComputedStyle = () => ({ getPropertyValue: () => ' teal ' });
global.localStorage = { getItem: () => null, setItem() {} };
global.location = { href: 'http://page.example.test/', search: '' };
global.history = { replaceState() {} };
global.setInterval = () => 0;
console.error = () => {};
global.fetch = url => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(
  url.startsWith('/api/stats') ? given.stats : url.startsWith('/api/paired') ? { available: false } : given.rows) });
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
(async () => {
  await render();
  const rows = evidenceRowsMarkup(given.rows);
  markup.push(rows);
  const link = id => ({ hidden: el(id).hidden, href: el(id).href, text: el(id).textContent });
  process.stdout.write(JSON.stringify({ markup: markup.join('\n'), rows, title: document.title, heading: el('page-title').textContent,
    note: el('page-note-text').textContent, note_hidden: el('page-note').hidden, note_link: link('page-note-link'),
    other_view: link('other-view-link'), backlink: link('backlink'), chip: el('source-label').textContent,
    status: el('status-line').textContent }));
})().catch(err => { process.stderr.write(String(err && err.stack || err)); process.exit(1); });
"""


@pytest.fixture
def page(tmp_path):
    """dashboard.js run by node against a stand-in DOM that keeps everything handed to the HTML parser."""
    if not shutil.which("node"):
        pytest.skip("needs node")
    (tmp_path / "dom.js").write_text(STUB_DOM)

    def render(api_stats: dict, rows: list) -> dict:
        (tmp_path / "given.json").write_text(json.dumps({"stats": api_stats, "rows": rows}))
        done = subprocess.run(["node", str(tmp_path / "dom.js"), str(STATIC / "dashboard.js"), str(tmp_path / "given.json")],
                              capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)
    return render


def served(records_dir: pathlib.Path) -> tuple[dict, list]:
    """(/api/stats, /api/episodes) of files mode over a directory, through the real reader, store and app."""
    store = main.Store()
    store.replace(main.file_source(str(records_dir)).read())
    client = create_app(store, "files").test_client()
    return stats(client), client.get("/api/episodes").get_json()


def test_a_hostile_record_and_hostile_settings_come_out_as_text(page, tmp_path, monkeypatch):
    """Markup in scene, model_version, curation_reason, the rollout fields, the id or the timestamp - and in the page's own settings - is never parsed."""
    hostile = {"model_version": HOSTILE, "scene": HOSTILE, "timestamp": HOSTILE, "curation_reason": HOSTILE, "curation_score": HOSTILE,
               "score_reason": HOSTILE, "rollout": {"status": "ok", "steps": HOSTILE, "duration_s": HOSTILE}}
    judged(tmp_path, "p", True, 1000, **hostile, episode_id=f"{HOSTILE}-p")
    judged(tmp_path, "r", False, 1001, **hostile, episode_id=f"{HOSTILE}-r")
    judged(tmp_path, "u", False, 1002, **{**hostile, "rollout": {"status": HOSTILE, "steps": 3}}, episode_id=f"{HOSTILE}-u")
    for name in ("PAGE_TITLE", "PAGE_NOTE", "PAGE_NOTE_LINK_LABEL", "SOURCE_LABEL", "OTHER_VIEW_LABEL"):
        monkeypatch.setenv(name, HOSTILE)
    monkeypatch.setenv("PAGE_NOTE_LINK_URL", "https://collection.example.test/?q=" + HOSTILE)
    monkeypatch.setenv("OTHER_VIEW_URL", "https://paired.example.test")
    api_stats, rows = served(tmp_path)
    assert list(api_stats["snapshot"]["versions"]) == [HOSTILE] and len(rows) == 3, "the records were taken in: this test is not vacuous"
    got = page(api_stats, rows)
    assert "<img" not in got["markup"] and "onerror=alert(1)>" not in got["markup"]
    assert got["markup"].count(ESCAPED) >= 8, "cards, picker, tables, charts and rows all show the label - as text"
    assert got["rows"].count(f"<td>{ESCAPED}</td>") == 2, "rollout.steps, the one field that used to reach a row unescaped"
    # settings are set as text nodes and link targets, never handed to the parser
    assert (got["title"], got["heading"], got["note"], got["chip"]) == (HOSTILE,) * 4
    assert got["note_link"]["text"] == HOSTILE and got["note_link"]["href"].startswith("https://collection.example.test/?q=")


def test_a_row_is_text_whatever_type_its_fields_have(page):
    """Rows straight from a store that holds anything: every cell escaped, a number that is none shown as a dash."""
    row = {"episode_id": HOSTILE, "model_version": "v", "task_success": True, "cubes_placed": HOSTILE, "avg_smoothness": HOSTILE,
           "rollout_steps": HOSTILE, "rollout_duration_s": HOSTILE, "rollout_status": HOSTILE, "seed": HOSTILE, "origin": "eval",
           "scene": HOSTILE, "timestamp": HOSTILE, "curation_verdict": HOSTILE}
    got = page({"source_mode": "files", "snapshot": {"episode_count": 0, "versions": {}}}, [row, {**row, "task_success": False}])
    assert "<img" not in got["rows"] and got["rows"].count(ESCAPED) >= 6
    assert got["rows"].count("<td title=\"\">--</td>") == 1, "a smoothness that is no number"


def test_the_page_names_itself_and_links_carry_the_theme(page):
    """Title, heading, the note with its one link, the sibling link and the way back - each with the viewer's theme."""
    got = page({"source_mode": "files", "source_label": "Live lane: judged, not kept", "page_title": "Live episodes - live lane",
                "page_note": "Judged, not kept.", "page_note_link_url": "https://collection.example.test",
                "page_note_link_label": "Live episodes (collection)", "other_view_url": "https://paired.example.test",
                "other_view_label": "Paired evaluation", "live_dashboard_url": "https://flywheel.example.test",
                "snapshot": {"episode_count": 0, "versions": {}}}, [])
    assert got["title"] == got["heading"] == "Live episodes - live lane" and got["chip"] == "Live lane: judged, not kept"
    assert (got["note"], got["note_hidden"]) == ("Judged, not kept.", False)
    assert got["note_link"] == {"hidden": False, "href": "https://collection.example.test/?theme=dark", "text": "Live episodes (collection)"}
    assert got["other_view"] == {"hidden": False, "href": "https://paired.example.test/?theme=dark", "text": "Paired evaluation"}
    assert got["backlink"]["href"] == "https://flywheel.example.test/?theme=dark"


def test_without_a_note_the_page_is_the_one_it_was(page):
    """No title, no note: the heading keeps the markup's text, the note and its link stay hidden."""
    got = page({"source_mode": "live", "source_label": "Live", "snapshot": {"episode_count": 0, "versions": {}}}, [])
    assert (got["title"], got["heading"], got["note"]) == ("", "", "") and got["note_hidden"] is True and got["note_link"]["hidden"] is True

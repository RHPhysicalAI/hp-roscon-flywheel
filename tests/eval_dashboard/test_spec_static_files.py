# This project was developed with assistance from AI tools.
"""Spec sections 10 and 11, the section 7 page line and the file conventions, as static checks on files."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "eval-dashboard"
PACKAGE = ROOT / "eval_dashboard"
STATIC = PACKAGE / "web" / "static"
STATIC_FILES = ("index.html", "dashboard.js", "styles.css")
AI_NOTE = "This project was developed with assistance from AI tools."

# Files the spec's sections cannot be implemented without touching or creating.
TOUCHED_FILES = (
    "eval_dashboard/schema.py",
    "eval_dashboard/aggregate.py",
    "eval_dashboard/main.py",
    "eval_dashboard/web/app.py",
    "eval_dashboard/web/static/dashboard.js",
    "eval_dashboard/sources/minio_source.py",
    "eval_dashboard/sources/eval_source.py",
    "Dockerfile",
)


def text_files_under(root):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def dockerfile_instructions():
    """Dockerfile instructions with comments dropped and continuations joined."""
    joined = re.sub(r"\\\s*\n", " ", (ROOT / "Dockerfile").read_text())
    lines = (line.strip() for line in joined.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def page_source():
    return "\n".join((STATIC / name).read_text() for name in ("index.html", "dashboard.js"))


# --- section 10: page ------------------------------------------------------


def test_no_tailscale_anywhere_in_the_component():
    """The literal 'Tailscale' is gone from code and comments, in any case."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in text_files_under(ROOT)
        if "tailscale" in path.read_bytes().decode("utf-8", errors="ignore").lower()
    ]
    assert offenders == []


@pytest.mark.parametrize("pattern", ["http://10.", "https://", "//cdn"])
@pytest.mark.parametrize("name", STATIC_FILES)
def test_static_files_reference_nothing_external(name, pattern):
    """The page has no private-address link, no remote URL and no CDN reference."""
    assert pattern not in (STATIC / name).read_text()


def test_dashboard_js_calls_the_paired_endpoint():
    """dashboard.js references /api/paired."""
    assert "/api/paired" in (STATIC / "dashboard.js").read_text()


@pytest.mark.parametrize("field", ["fixed_seeds", "broken_seeds", "sign_test_p"])
def test_page_reads_the_paired_fields(field):
    """The page reads the paired payload's seed lists and p value by name."""
    assert field in page_source()


def test_page_shows_a_floor_for_a_zero_p_value():
    """The page has the 0.0001 floor it prints when p is 0.0."""
    assert "0.0001" in page_source()


@pytest.mark.parametrize("field", ["source_label", "other_view_url", "other_view_label"])
def test_page_reads_the_new_stats_fields(field):
    """The page reads the source label and the other-view link from the config."""
    assert field in page_source()


def test_page_reports_episodes_not_scored():
    """The page reads not_scored and words it as 'not scored'."""
    source = page_source()
    assert "not_scored" in source
    assert "not scored" in source.lower()


# --- section 11: container -------------------------------------------------


def test_dockerfile_base_image():
    """The final FROM line names the UBI 9 Python 3.12 image."""
    from_lines = [line for line in dockerfile_instructions() if line.upper().startswith("FROM ")]
    assert from_lines
    assert "registry.access.redhat.com/ubi9/python-312" in from_lines[-1]


def test_dockerfile_exposes_8080():
    """The Dockerfile has EXPOSE 8080."""
    assert any(re.fullmatch(r"EXPOSE\s+8080(/tcp)?", line) for line in dockerfile_instructions())


def test_dockerfile_cmd():
    """The Dockerfile's CMD is the exec form the spec gives."""
    cmd_lines = [line for line in dockerfile_instructions() if line.startswith("CMD")]
    assert cmd_lines
    assert re.sub(r"\s+", "", cmd_lines[-1]) == 'CMD["python","-m","eval_dashboard.main"]'


def test_dockerfile_pythonpath_is_app():
    """PYTHONPATH is /app."""
    env_lines = [line for line in dockerfile_instructions() if line.startswith("ENV")]
    assert any(re.search(r"\bPYTHONPATH[= ]\"?/app\"?(\s|$)", line) for line in env_lines)


@pytest.mark.parametrize("source", ["eval_dashboard", "config"])
def test_dockerfile_copies_the_package_and_config(source):
    """eval_dashboard/ and config/ are copied from the build context."""
    copy_lines = [line for line in dockerfile_instructions() if line.startswith("COPY")]
    assert any(re.search(rf"\s(\./)?{source}/?\s", line) for line in copy_lines)


def test_dockerfile_installs_requirements():
    """requirements.txt is installed with pip."""
    run_lines = [line for line in dockerfile_instructions() if line.startswith("RUN")]
    assert any("pip" in line and "requirements.txt" in line for line in run_lines)


def test_dockerfile_does_not_end_as_root():
    """The image runs as a non-root user: no final USER of root or 0."""
    user_lines = [line.split(None, 1)[1].strip() for line in dockerfile_instructions() if line.startswith("USER")]
    if user_lines:
        assert user_lines[-1].split(":")[0] not in {"root", "0"}


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_have_upper_bounds(name):
    """Every line that names a package pins it with <, == or ~=."""
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    unbounded = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = line.split(";", 1)[0]
        if not re.search(r"<|==|~=", requirement):
            unbounded.append(line)
    assert unbounded == []


# --- conventions -----------------------------------------------------------


@pytest.mark.parametrize("relative", TOUCHED_FILES)
def test_touched_files_carry_the_ai_note_in_their_first_lines(relative):
    """Each file the change touches or creates has the AI-assistance comment at the top."""
    path = ROOT / relative
    assert path.exists(), f"{relative} does not exist"
    first_lines = path.read_text().splitlines()[:5]
    assert any(AI_NOTE in line for line in first_lines)

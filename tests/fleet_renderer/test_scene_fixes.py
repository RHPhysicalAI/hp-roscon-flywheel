# This project was developed with assistance from AI tools.
"""The image build's changes to upstream's MJCF, on a cut-down scene; no MuJoCo needed."""
import xml.etree.ElementTree as ET

import pytest
import scene

FLAT = """<mujoco model="s"><compiler angle="radian" meshdir="/ws_pai/install/share/meshes/"/>
<asset><material name="3d_printed" rgba="0.2 0.7 0.85 1"/><material name="sts3215" rgba="0.1 0.1 0.1 1"/>
<material name="material_7" specular="0.2"/><material name="material_12" rgba="1 0.36 0.24 1"/>
<mesh name="a" file="a.stl"/><mesh name="b" file="sub/b.stl"/></asset>
<worldbody><camera name="static_camera" fovy="55"/><body><camera name="wrist_camera" fovy="55"/></body></worldbody></mujoco>"""
WORLD = """<mujoco><asset><material name="material_7"/><material name="material_12"/></asset></mujoco>"""


def fixed():
    text, meshdir = scene.apply_fixes(FLAT, scene.material_names(WORLD))
    return ET.fromstring(text), meshdir


def test_both_cameras_get_gazebos_field_of_view():
    """fovy 55 becomes 55.4114 on every camera."""
    root, _ = fixed()
    assert [c.get("fovy") for c in root.iter("camera")] == ["55.4114", "55.4114"]


def test_servo_material_takes_the_printed_colour():
    """sts3215 is painted like 3d_printed, which itself is unchanged."""
    materials = {m.get("name"): m.get("rgba") for m in fixed()[0].iter("material")}
    assert materials["sts3215"] == materials["3d_printed"] == "0.2 0.7 0.85 1"


def test_world_materials_lose_the_converters_gain():
    """rgb is divided by 1.2, alpha is not; a material saved without rgba was white."""
    materials = {m.get("name"): [float(v) for v in m.get("rgba").split()] for m in fixed()[0].iter("material")}
    assert materials["material_12"] == pytest.approx([1 / 1.2, 0.3, 0.2, 1.0])
    assert materials["material_7"] == pytest.approx([1 / 1.2] * 3 + [1.0], rel=1e-5)


def test_meshdir_becomes_relative_and_the_old_one_is_reported():
    """The scene points at meshes/ next to itself; the build copies from where upstream had them."""
    root, meshdir = fixed()
    assert root.find("compiler").get("meshdir") == "meshes"
    assert meshdir == "/ws_pai/install/share/meshes/"


def test_only_named_meshes_are_copied(tmp_path):
    """Files the scene does not name stay behind, sub-directories are kept."""
    source = tmp_path / "src"
    (source / "sub").mkdir(parents=True)
    for name in ("a.stl", "sub/b.stl", "unrelated.txt"):
        (source / name).write_text(name)
    assert scene.copy_meshes(scene.apply_fixes(FLAT, ["material_7"])[0], source, tmp_path / "out") == 2
    copied = sorted(str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert copied == ["meshes/a.stl", "meshes/sub/b.stl"]


@pytest.mark.parametrize("flat, world_materials", [
    (FLAT.replace('name="sts3215"', 'name="servo"'), ["material_7"]),
    (FLAT, ["material_99"]),
    (FLAT, []),
    (FLAT.replace(' meshdir="/ws_pai/install/share/meshes/"', ""), ["material_7"]),
    (FLAT.replace("<camera", "<site"), ["material_7"]),
])
def test_a_scene_that_is_not_the_expected_one_fails_the_build(flat, world_materials):
    """A missing material, camera or meshdir ends the build instead of baking a wrong scene."""
    with pytest.raises(SystemExit):
        scene.apply_fixes(flat, world_materials)


def test_a_missing_mesh_fails_the_build(tmp_path):
    """A mesh the scene names and the image lacks is an error at build time, not at the first render."""
    with pytest.raises(SystemExit):
        scene.copy_meshes(scene.apply_fixes(FLAT, ["material_7"])[0], tmp_path, tmp_path / "out")

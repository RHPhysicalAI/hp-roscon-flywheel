# This project was developed with assistance from AI tools.
"""Write a copy of a Gazebo world without its sensors system plugin, so nothing renders and no GPU is needed."""

# Used by docker/entrypoint.sh when SIM_CAMERAS=off:  physics_only_world.py <world.sdf> <copy.sdf>
# The block is cut out of the text (everything else stays byte for byte) and the result is then compared with the
# original as XML: same world name, the same children of <world> in the same order, less exactly one element.

import re
import sys
import xml.etree.ElementTree as ET

SENSORS_FILENAME = "gz-sim-sensors-system"
# The opening tag with its two attributes, up to the first closing tag; a plugin cannot contain a plugin.
_BLOCK = re.compile(
    r'[ \t]*<plugin\s+filename="gz-sim-sensors-system"\s+name="gz::sim::systems::Sensors"\s*>.*?</plugin>[ \t]*\r?\n?',
    re.DOTALL)


def _world_children(sdf: str) -> tuple[str, list[str]]:
    """The world's name and one canonical string per direct child of <world>."""
    worlds = ET.fromstring(sdf).findall("world")
    if len(worlds) != 1:
        raise ValueError(f"expected one <world>, found {len(worlds)}")
    return worlds[0].get("name", ""), [ET.tostring(child, encoding="unicode").strip() for child in worlds[0]]


def strip_sensors(sdf: str) -> str:
    """The world without its sensors plugin block; ValueError unless exactly that one element is what went."""
    if sdf.count(SENSORS_FILENAME) != 1:
        raise ValueError(f"'{SENSORS_FILENAME}' occurs {sdf.count(SENSORS_FILENAME)} times, expected exactly once")
    blocks = _BLOCK.findall(sdf)
    if len(blocks) != 1:
        raise ValueError(f"the sensors plugin block matched {len(blocks)} times, expected exactly once")
    block = blocks[0]
    if block.count("<plugin") != 1 or block.count("</plugin>") != 1:
        raise ValueError("the matched block holds more than one plugin element")
    stripped = _BLOCK.sub("", sdf, count=1)
    if len(stripped) != len(sdf) - len(block):
        raise ValueError("more than the matched block changed")

    name_before, before = _world_children(sdf)
    name_after, after = _world_children(stripped)
    gone = [i for i, child in enumerate(before) if SENSORS_FILENAME in child]
    if len(gone) != 1 or not before[gone[0]].startswith("<plugin"):
        raise ValueError("the sensors plugin is not exactly one direct child of <world>")
    if name_after != name_before or after != before[:gone[0]] + before[gone[0] + 1:]:
        raise ValueError("the copy differs from the original by more than the sensors plugin")
    return stripped


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: physics_only_world.py <world.sdf> <copy.sdf>")
    with open(sys.argv[1], encoding="utf-8") as src:
        sdf = src.read()
    try:
        stripped = strip_sensors(sdf)
    except (ValueError, ET.ParseError) as err:
        sys.exit(f"physics_only_world: {sys.argv[1]}: {err}")
    with open(sys.argv[2], "w", encoding="utf-8") as dst:
        dst.write(stripped)
    print(f"physics_only_world: wrote {sys.argv[2]} ({len(sdf) - len(stripped)} bytes of sensors plugin removed)")


if __name__ == "__main__":
    main()

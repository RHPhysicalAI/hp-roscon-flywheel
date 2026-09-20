# This project was developed with assistance from AI tools.
"""Cutting the sensors plugin out of a world: exactly that block goes, or nothing is written."""

import pytest

import physics_only_world as pow_

SENSORS = """    <plugin
      filename="gz-sim-sensors-system"
      name="gz::sim::systems::Sensors">
      <render_engine>ogre2</render_engine>
      <global_illumination type="vct">
        <enabled>true</enabled>
      </global_illumination>
    </plugin>
"""
WORLD = """<?xml version="1.0" ?>
<!-- a comment that must survive -->
<sdf version="1.9">
  <world name="pai_world">
    <gui><plugin filename="MinimalScene" name="3D View"><engine>ogre2</engine></plugin></gui>

    <!-- System plugins -->
{sensors}    <plugin
      filename="gz-sim-user-commands-system"
      name="gz::sim::systems::UserCommands">
    </plugin>
    <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"></plugin>
    <gravity>0 0 -9.8</gravity>
    <model name="cube_small"><link name="link"/></model>
  </world>
</sdf>
"""


def test_only_the_sensors_block_goes():
    """The result is the original text with exactly the block's bytes missing."""
    stripped = pow_.strip_sensors(WORLD.format(sensors=SENSORS))
    assert stripped == WORLD.format(sensors="")
    assert 'name="pai_world"' in stripped and "a comment that must survive" in stripped
    assert "gz-sim-user-commands-system" in stripped and "gz-sim-contact-system" in stripped


def test_refuses_a_world_without_the_block():
    """Not found is an error, not a silent copy."""
    with pytest.raises(ValueError):
        pow_.strip_sensors(WORLD.format(sensors=""))


def test_refuses_two_blocks():
    """Found twice is an error."""
    with pytest.raises(ValueError):
        pow_.strip_sensors(WORLD.format(sensors=SENSORS + SENSORS))


def test_refuses_a_block_with_other_attributes():
    """A sensors plugin that is not written the expected way is not guessed at."""
    odd = SENSORS.replace('name="gz::sim::systems::Sensors"', 'name="gz::sim::systems::Sensors" extra="1"')
    with pytest.raises(ValueError):
        pow_.strip_sensors(WORLD.format(sensors=odd))


def test_refuses_a_mention_outside_the_block():
    """The plugin's filename anywhere else in the file stops the cut."""
    world = WORLD.format(sensors=SENSORS).replace("<gravity>", "<!-- gz-sim-sensors-system --><gravity>")
    with pytest.raises(ValueError):
        pow_.strip_sensors(world)


def test_refuses_a_block_nested_below_the_world():
    """A sensors plugin inside a model is not the world's system plugin."""
    world = WORLD.format(sensors="").replace('<link name="link"/>', '<link name="link"/>' + SENSORS)
    with pytest.raises(ValueError):
        pow_.strip_sensors(world)

# This project was developed with assistance from AI tools.
"""The wall's grid for N robots, the tiles' places, the labels and the stale grey."""
import numpy as np
import pytest
import render_core as core


@pytest.mark.parametrize("n, cols, rows, tile", [(1, 1, 1, 480), (2, 2, 1, 480), (7, 4, 2, 480),
                                                 (20, 5, 4, 384), (24, 6, 4, 320)])
def test_grid_for_the_fleets_sizes(n, cols, rows, tile):
    """Columns, rows and tile size at the default 480 px render."""
    layout = core.mosaic_layout(n, 480)
    assert (layout.cols, layout.rows, layout.tile) == (cols, rows, tile)


@pytest.mark.parametrize("n", list(range(1, 101)))
def test_every_fleet_fits_within_1920_without_an_empty_row(n):
    """For any N the grid holds all tiles, has no empty row, and is at most 1920 px wide."""
    layout = core.mosaic_layout(n, 480)
    assert layout.cols * layout.rows >= n > layout.cols * (layout.rows - 1)
    assert layout.width <= 1920 and layout.tile >= 1 and layout.tile <= 480


def test_small_renders_are_not_enlarged():
    """Tiles are never bigger than what was rendered."""
    assert core.mosaic_layout(2, 96).tile == 96 and core.mosaic_layout(2, 96).width == 192


def test_cells_fill_rows_left_to_right():
    """Tile i sits at column i mod cols, row i div cols."""
    layout = core.mosaic_layout(7, 480)
    assert [layout.cell(i) for i in (0, 3, 4, 6)] == [(0, 0), (1440, 0), (0, 480), (960, 480)]


def test_compose_places_tiles_and_leaves_empty_cells_dark():
    """Each tile's pixels land in its cell; a cell without a robot keeps the background."""
    layout = core.mosaic_layout(5, 64)
    tiles = [np.full((64, 64, 3), value, dtype=np.uint8) for value in (50, 100, 150, 200, 250)]
    mosaic = core.compose_mosaic(layout, tiles, [f"r{i:02d}" for i in range(5)], [False] * 5)
    assert mosaic.shape == (layout.height, layout.width, 3) == (128, 192, 3)
    assert [int(mosaic[y, x, 0]) for y, x in ((40, 40), (40, 104), (40, 168), (104, 40), (104, 104))] == \
        [50, 100, 150, 200, 250]
    assert (mosaic[64:, 128:] == core.BACKGROUND).all()


def test_labels_are_white_on_black_in_the_tiles_corner():
    """The robot id is drawn at the top-left of its tile and differs between robots."""
    layout = core.mosaic_layout(2, 64)
    tiles = [np.full((64, 64, 3), 100, dtype=np.uint8)] * 2
    mosaic = core.compose_mosaic(layout, tiles, ["r00", "r01"], [False, False])
    corner_a, corner_b = mosaic[:9, :19], mosaic[:9, 64:83]
    assert set(np.unique(corner_a)) == {0, 255}
    assert not np.array_equal(corner_a, corner_b)
    assert np.array_equal(corner_a[:, :12], corner_b[:, :12])   # "r0" is the same, the last digit is not


def test_stale_tiles_are_greyed_and_say_so():
    """A stale robot's tile is grey, darker, and its label is longer than the id."""
    layout = core.mosaic_layout(2, 96)
    tile = np.zeros((96, 96, 3), dtype=np.uint8)
    tile[..., 0] = 200
    mosaic = core.compose_mosaic(layout, [tile, tile], ["r00", "r01"], [False, True])
    live, stale = mosaic[50, 50], mosaic[50, 96 + 50]
    assert live.tolist() == [200, 0, 0]
    assert stale[0] == stale[1] == stale[2] and 0 < stale[0] < 100
    assert (mosaic[:9, 96 + 20: 96 + 50] == 255).any() and not (mosaic[:9, 20:50] == 255).any()


def test_a_robot_without_a_picture_keeps_its_labelled_cell():
    """None as a tile draws only the label."""
    layout = core.mosaic_layout(1, 64)
    mosaic = core.compose_mosaic(layout, [None], ["r09"], [False])
    assert (mosaic[:9, :19] == 255).any() and (mosaic[20:, :] == core.BACKGROUND).all()


def test_wrongly_sized_tile_is_refused():
    """compose_mosaic does not resize: a tile of another size is a bug in the caller."""
    with pytest.raises(ValueError):
        core.compose_mosaic(core.mosaic_layout(1, 64), [np.zeros((32, 32, 3), np.uint8)], ["r00"], [False])


def test_text_bitmap_geometry_and_unknown_characters():
    """Glyphs are 5x7 with one column between them, scaled by whole factors; unknown characters are a block."""
    assert core.text_bitmap("r07").shape == (7, 17)
    assert core.text_bitmap("r07", scale=3).shape == (21, 51)
    assert core.text_bitmap("").shape == (7, 0)
    assert core.text_bitmap("#").all()
    assert all(len(rows.split()) == 7 and all(len(r) == 5 for r in rows.split()) for rows in core._GLYPHS.values())


def test_labels_are_clipped_at_the_edge():
    """A label that does not fit is cut, never an error."""
    image = np.zeros((8, 10, 3), dtype=np.uint8)
    core.draw_label(image, 4, 4, "r00 stale", 2)
    assert image.shape == (8, 10, 3)


def test_placeholder_has_text_in_the_middle():
    """The empty wall is dark with a centred line."""
    image = core.placeholder(960, 480, "waiting for robots")
    assert image.shape == (480, 960, 3)
    assert (image[230:250, 300:660] == 255).any() and (image[:100] == core.BACKGROUND).all()

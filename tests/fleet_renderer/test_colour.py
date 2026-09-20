# This project was developed with assistance from AI tools.
"""Linear to sRGB, the packed pixel format, and the grey of a stale robot."""
import numpy as np
import render_core as core


def test_srgb_table_endpoints():
    """Black stays black and white stays white."""
    lut = core.srgb_lut()
    assert lut.dtype == np.uint8 and lut.shape == (256,)
    assert (lut[0], lut[255]) == (0, 255)


def test_srgb_table_is_monotonic_and_brightens():
    """The table never decreases and lifts every value between the endpoints."""
    lut = core.srgb_lut().astype(int)
    assert (np.diff(lut) >= 0).all()
    assert (lut[1:255] > np.arange(1, 255)).all()


def test_srgb_table_known_values():
    """Linear 0.5 encodes to 188, and the linear toe is 12.92 x."""
    lut = core.srgb_lut()
    assert lut[128] == 188
    assert lut[1] == round(12.92 * 1)


def test_srgb_table_applies_per_channel_by_indexing():
    """Indexing the table with an image converts it."""
    image = np.array([[[0, 128, 255]]], dtype=np.uint8)
    assert core.srgb_lut()[image].tolist() == [[[0, 188, 255]]]


def test_unpack_rgb_reads_0xAARRGGBB_at_the_cameras_offset():
    """The second camera's pixels start at its address and come out as R, G, B rows."""
    width, height = 3, 2
    packed = np.zeros(2 * width * height, dtype=np.uint32)
    packed[width * height:] = 0xFF102030
    packed[width * height + 4] = 0xFFAABBCC   # row 1, column 1
    image = core.unpack_rgb(packed, width * height, width, height)
    assert image.shape == (2, 3, 3) and image.flags["C_CONTIGUOUS"]
    assert image[0, 0].tolist() == [0x10, 0x20, 0x30]
    assert image[1, 1].tolist() == [0xAA, 0xBB, 0xCC]


def test_grey_out_is_colourless_and_darker():
    """A stale picture has equal channels at about half the luminance."""
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    image[0, 0] = (255, 255, 255)
    image[0, 1] = (255, 0, 0)
    grey = core.grey_out(image)
    assert (grey[..., 0] == grey[..., 1]).all() and (grey[..., 1] == grey[..., 2]).all()
    assert grey[0, 0, 0] == 127 and grey[0, 1, 0] == 26 and grey[1, 1, 0] == 0

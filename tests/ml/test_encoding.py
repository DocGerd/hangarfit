"""Tests for the observation tensorizer (ml/encoding.py, sub-project #2)."""

from __future__ import annotations

import numpy as np
import shapely

from ml import encoding
from ml.encoding import EncoderConfig, _cell_centers, _rasterize, _static_channels
from tests.ml.conftest import empty_hangar


def test_schema_version_and_dims_constants():
    assert encoding.SCHEMA_VERSION == 1
    assert encoding.TOKEN_DIM == 24
    assert encoding.RASTER_CHANNELS == 7
    assert encoding.ACTION_DIM == 9
    assert encoding.PARK_INDEX == 8


def test_config_defaults():
    c = EncoderConfig()
    assert (c.cell_m, c.grid_w, c.grid_h, c.max_objects) == (0.25, 96, 192, 16)
    assert c.z_split_m == 1.6 and c.pos_ref_m == 20.0 and c.apron_band_m == 10.0


def test_cell_centers_shape_and_anchor():
    c = EncoderConfig()
    origin_x, origin_y, xs, ys = _cell_centers(c)
    assert origin_x == 0.0 and origin_y == -10.0
    assert xs.shape == (96,) and ys.shape == (192,)
    # first cell centre is half a cell in from the origin
    assert xs[0] == 0.125
    assert ys[0] == -10.0 + 0.125


def test_rasterize_box_cell_count_and_dtype():
    c = EncoderConfig()
    # a 2 m x 2 m box anchored inside the hangar (x in [10,12], y in [5,7])
    box = shapely.box(10.0, 5.0, 12.0, 7.0)
    grid = _rasterize(box, c)
    assert grid.shape == (192, 96)
    assert grid.dtype == np.float32
    # ~ (2/0.25)^2 = 64 cell centres inside; allow a 1-cell boundary slop per axis
    assert 49 <= int(grid.sum()) <= 81
    # values are binary
    assert set(np.unique(grid)).issubset({0.0, 1.0})


def test_rasterize_none_is_empty():
    c = EncoderConfig()
    grid = _rasterize(None, c)
    assert grid.shape == (192, 96) and grid.sum() == 0.0


def test_static_channels_shape_and_content():
    c = EncoderConfig()
    h = empty_hangar()  # synthetic 22 m hangar, apron_depth_m=8.0, has door + bay
    static = _static_channels(h, c)
    assert static.shape == (4, 192, 96) and static.dtype == np.float32
    oob, bay, apron, door = static
    # oob has both inside (0) and outside (1) cells
    assert 0.0 in np.unique(oob) and 1.0 in np.unique(oob)
    # apron occupies the y<0 band → some rows fully set
    assert apron.sum() > 0.0
    # bay and door markers are non-empty
    assert bay.sum() > 0.0
    assert door.sum() > 0.0


def test_static_channels_notch_marks_oob():
    from dataclasses import replace

    from hangarfit.models import StructuralNotch

    c = EncoderConfig()
    h = empty_hangar()
    # carve a notch in the back-right corner; its interior must read oob=1
    notch = StructuralNotch(
        x_min_m=h.width_m - 4.0,
        x_max_m=h.width_m,
        y_min_m=h.length_m - 4.0,
        y_max_m=h.length_m,
    )
    h2 = replace(h, structural_notches=(notch,))
    oob = _static_channels(h2, c)[0]
    oob_base = _static_channels(h, c)[0]
    # the notch adds out-of-floor area inside the outer rectangle
    assert oob.sum() > oob_base.sum()

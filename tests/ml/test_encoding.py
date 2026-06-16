"""Tests for the observation tensorizer (ml/encoding.py, sub-project #2)."""

from __future__ import annotations

import numpy as np
import shapely

from hangarfit.models import Placement
from ml import encoding
from ml.encoding import (
    ACTION_DIM,
    PARK_INDEX,
    TOKEN_DIM,
    EncoderConfig,
    _active_occupancy,
    _cell_centers,
    _legal_action_mask,
    _parked_occupancy,
    _rasterize,
    _static_channels,
    _tokens,
)
from ml.types import ActiveObject, Observation, ParkedObject, Pose
from tests.ml.conftest import _fuji, empty_hangar


def _obs(*, parked=(), active=None, unplaced=()):
    return Observation(
        active=active,
        parked=tuple(parked),
        unplaced_ids=tuple(unplaced),
        steps_this_object=0,
        steps_total=0,
    )


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
    # Exactly (2/0.25)^2 = 64 cell centres inside: centres sit at 0.125 m half-steps
    # and never land on the 0.25 m-aligned box edges (10/12/5/7), so no boundary slop.
    assert int(grid.sum()) == 64
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


def test_static_channels_zero_apron_is_empty():
    from dataclasses import replace

    c = EncoderConfig()
    h = replace(empty_hangar(), apron_depth_m=0.0)
    apron = _static_channels(h, c)[2]
    assert apron.sum() == 0.0


def test_parked_occupancy_has_low_and_wing_bands():
    c = EncoderConfig()
    fleet = _fuji()
    pid = "aviat_husky"  # an aircraft with both low (gear/fuselage) and high (wing) parts
    pl = Placement(plane_id=pid, x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id=pid, placement=pl),))
    low, wing = _parked_occupancy(obs, fleet, c)
    assert low.shape == (192, 96) and wing.shape == (192, 96)
    assert low.sum() > 0.0  # low parts (gear/fuselage) below z_split
    assert wing.sum() > 0.0  # wing/tail above z_split


def test_active_occupancy_painted_at_pose():
    c = EncoderConfig()
    fleet = _fuji()
    body = fleet["fuji"]
    active = ActiveObject(
        object_id="fuji",
        body=body,
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
    )
    obs = _obs(active=active)
    occ = _active_occupancy(obs, c)
    assert occ.shape == (192, 96) and occ.sum() > 0.0


def test_active_occupancy_empty_when_terminal():
    c = EncoderConfig()
    occ = _active_occupancy(_obs(), c)
    assert occ.sum() == 0.0


# ---------------------------------------------------------------------------
# Task 5: Set-token table + mask + active index
# ---------------------------------------------------------------------------


def test_tokens_status_type_pose_and_padding():
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=90.0),
        on_carts=False,
    )
    obs = _obs(
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        active=active,
        unplaced=("cessna_150",),
    )
    tokens, mask, active_index = _tokens(obs, fleet, c)
    assert tokens.shape == (16, TOKEN_DIM) and tokens.dtype == np.float32
    assert mask.dtype == bool
    # order = [parked, active, unplaced], padded
    assert list(mask[:3]) == [True, True, True] and mask[3:].sum() == 0
    assert active_index == 1
    # status one-hots (cols 0..2): parked / active / unplaced
    assert list(tokens[0, 0:3]) == [1.0, 0.0, 0.0]
    assert list(tokens[1, 0:3]) == [0.0, 1.0, 0.0]
    assert list(tokens[2, 0:3]) == [0.0, 0.0, 1.0]
    # all three are aircraft (type col 3)
    assert tokens[0, 3] == 1.0 and tokens[1, 3] == 1.0 and tokens[2, 3] == 1.0
    # unplaced row has zero pose (cols 18..21)
    assert list(tokens[2, 18:22]) == [0.0, 0.0, 0.0, 0.0]
    # active pose is populated and heading 90deg -> sin≈1, cos≈0 (cols 20,21)
    assert abs(tokens[1, 20] - 1.0) < 1e-6 and abs(tokens[1, 21]) < 1e-6
    # reserved slots (22,23) are zero in v1
    assert list(tokens[1, 22:24]) == [0.0, 0.0]
    # padding rows are all zero
    assert tokens[5].sum() == 0.0


def test_tokens_wing_and_movement_one_hots():
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="aviat_husky", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id="aviat_husky", placement=pl),))
    tokens, _, _ = _tokens(obs, fleet, c)
    # wing one-hot is cols 8..10; exactly one set for an aircraft
    assert tokens[0, 8:11].sum() == 1.0
    # movement one-hot is cols 11..13; exactly one set for an aircraft
    assert tokens[0, 11:14].sum() == 1.0


# ---------------------------------------------------------------------------
# Task 6: Legal-action mask
# ---------------------------------------------------------------------------


def _active_for(fleet, pid, *, on_carts):
    return ActiveObject(
        object_id=pid,
        body=fleet[pid],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=on_carts,
    )


def test_legal_mask_own_gear_no_strafe():
    c = EncoderConfig()
    fleet = _fuji()
    # fuji is always_own_gear (turn radius > 0): no strafe (idx6, idx7 False)
    mask = _legal_action_mask(_obs(active=_active_for(fleet, "fuji", on_carts=False)), c)
    assert mask.shape == (ACTION_DIM,) and mask.dtype == bool
    assert mask[6] == False and mask[7] == False  # noqa: E712
    assert mask[PARK_INDEX] == True  # noqa: E712


def test_legal_mask_cart_has_strafe_and_holes():
    c = EncoderConfig()
    fleet = _fuji()
    cart_ids = [i for i, b in fleet.items() if b.effective_turn_radius_m() == 0.0]
    assert cart_ids, "expected at least one cart/pivot body in the fleet"
    mask = _legal_action_mask(_obs(active=_active_for(fleet, cart_ids[0], on_carts=True)), c)
    # strafe legal when on carts
    assert mask[6] == True and mask[7] == True  # noqa: E712
    # cart reverse-arc holes idx3 (L,-1) and idx5 (R,-1) are False
    assert mask[3] == False and mask[5] == False  # noqa: E712
    assert mask[PARK_INDEX] == True  # noqa: E712


def test_legal_mask_terminal_all_false():
    c = EncoderConfig()
    mask = _legal_action_mask(_obs(), c)
    assert mask.sum() == 0  # entirely False, including PARK

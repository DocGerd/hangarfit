"""Tests for the observation tensorizer (ml/encoding.py, sub-project #2)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from hangarfit.loader import load_ground_objects
from hangarfit.models import Placement
from ml import encoding
from ml.encoding import (
    ACTION_DIM,
    PARK_INDEX,
    RASTER_CHANNELS,
    TOKEN_DIM,
    EncoderConfig,
    _active_occupancy,
    _cell_centers,
    _legal_action_mask,
    _parked_occupancy,
    _rasterize,
    _static_channels,
    _tokens,
    encode,
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


def test_ego_constants_and_helpers():
    # OFF constants are unchanged (byte-identity anchor)
    assert encoding.TOKEN_DIM == 24 and encoding.SCHEMA_VERSION == 1
    # New ego constants
    assert encoding.EGO_EXTRA_COLS == 4
    assert encoding.EGO_TOKEN_DIM == 28
    assert encoding.SCHEMA_VERSION_EGO == 2
    off = EncoderConfig()
    on = EncoderConfig(ego_centric=True)
    assert off.ego_centric is False and on.ego_centric is True
    assert encoding.token_dim(off) == 24 and encoding.token_dim(on) == 28
    assert encoding.schema_version_for(off) == 1
    assert encoding.schema_version_for(on) == 2


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


def test_tokens_ego_relative_cols_worked_example():
    c = EncoderConfig(ego_centric=True)
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
    tokens, _mask, active_index = _tokens(obs, fleet, c)
    # width grew to 28
    assert tokens.shape == (16, encoding.EGO_TOKEN_DIM)
    # absolute cols 18..21 are STILL written (augment, not replace): active heading 90 -> sin1 cos0
    assert abs(tokens[1, 20] - 1.0) < 1e-6 and abs(tokens[1, 21]) < 1e-6
    # active object's own ego cols are the origin (0,0,0,1)
    assert active_index == 1
    assert list(tokens[1, 24:28]) == [0.0, 0.0, 0.0, 1.0]
    # parked fuji is 16 m due-north of an east-facing active -> fwd 0, right -16 (to its left),
    # normalized by pos_ref_m=20 -> (0, -0.8); relative heading 0-90=-90 -> sin -1, cos 0
    assert abs(tokens[0, 24] - 0.0) < 1e-6
    assert abs(tokens[0, 25] - (-0.8)) < 1e-6
    assert abs(tokens[0, 26] - (-1.0)) < 1e-6
    assert abs(tokens[0, 27] - 0.0) < 1e-6
    # unplaced row has zero ego cols (no pose)
    assert list(tokens[2, 24:28]) == [0.0, 0.0, 0.0, 0.0]


def test_tokens_off_path_is_24_wide_and_unchanged():
    # OFF config: width stays 24, no ego cols exist (byte-identity anchor)
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=None, unplaced=())
    tokens, _mask, _ai = _tokens(obs, fleet, c)
    assert tokens.shape == (16, encoding.TOKEN_DIM) == (16, 24)


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
    fleet = _fuji()
    # fuji is always_own_gear (turn radius > 0): no strafe (idx6, idx7 False)
    mask = _legal_action_mask(_obs(active=_active_for(fleet, "fuji", on_carts=False)))
    assert mask.shape == (ACTION_DIM,) and mask.dtype == bool
    assert not mask[6] and not mask[7]
    assert mask[PARK_INDEX]


def test_legal_mask_cart_has_strafe_and_holes():
    fleet = _fuji()
    cart_ids = [i for i, b in fleet.items() if b.effective_turn_radius_m() == 0.0]
    assert cart_ids, "expected at least one cart/pivot body in the fleet"
    mask = _legal_action_mask(_obs(active=_active_for(fleet, cart_ids[0], on_carts=True)))
    # strafe legal when on carts
    assert mask[6] and mask[7]
    # cart reverse-arc holes idx3 (L,-1) and idx5 (R,-1) are False
    assert not mask[3] and not mask[5]
    assert mask[PARK_INDEX]


def test_legal_mask_terminal_all_false():
    mask = _legal_action_mask(_obs())
    assert mask.sum() == 0  # entirely False, including PARK


# ---------------------------------------------------------------------------
# Task 7: Public encode() + determinism
# ---------------------------------------------------------------------------


def _two_body_obs(fleet):
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
    )
    return _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=active)


def test_encode_full_shapes_and_meta():
    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    out = encode(_two_body_obs(fleet), h, fleet, c)
    assert out.schema_version == 1
    assert out.raster.shape == (RASTER_CHANNELS, 192, 96) and out.raster.dtype == np.float32
    assert out.tokens.shape == (16, 24) and out.tokens.dtype == np.float32
    assert out.token_mask.shape == (16,) and out.token_mask.dtype == bool
    assert out.legal_action_mask.shape == (9,)
    assert out.active_index == 1
    assert set(out.meta) == {
        "cell_m",
        "origin_x_m",
        "origin_y_m",
        "grid_h",
        "grid_w",
        "steps_this_object",
        "steps_total",
    }
    assert all(isinstance(v, float) for v in out.meta.values())


def test_encode_meta_is_immutable():
    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    out = encode(_two_body_obs(fleet), h, fleet, c)
    with pytest.raises(TypeError):
        out.meta["cell_m"] = 999.0  # type: ignore[index]


def test_encode_is_deterministic():
    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    a = encode(_two_body_obs(fleet), h, fleet, c)
    b = encode(_two_body_obs(fleet), h, fleet, c)
    assert np.array_equal(a.raster, b.raster)
    assert np.array_equal(a.tokens, b.tokens)
    assert np.array_equal(a.token_mask, b.token_mask)
    assert np.array_equal(a.legal_action_mask, b.legal_action_mask)
    assert a.active_index == b.active_index and a.meta == b.meta


# ---------------------------------------------------------------------------
# Hardening: silent-wrong paths converted to explicit errors
# ---------------------------------------------------------------------------


def test_tokens_overflow_raises_instead_of_terminal():
    # max_objects=3 but 4 parked + 1 active = 5 entries; the active row would land
    # out of range. The encoder must raise rather than silently reset active_index=-1
    # (which would look terminal mid-episode and corrupt training).
    c = EncoderConfig(max_objects=3)
    fleet = _fuji()
    parked = tuple(
        ParkedObject(
            object_id="fuji",
            placement=Placement(plane_id="fuji", x_m=11.0, y_m=y, heading_deg=0.0, on_carts=False),
        )
        for y in (5.0, 8.0, 11.0, 14.0)
    )
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
    )
    obs = _obs(parked=parked, active=active)
    with pytest.raises(ValueError, match="max_objects"):
        _tokens(obs, fleet, c)


def test_require_body_missing_raises_keyerror_with_context():
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="ghost", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id="ghost", placement=pl),))
    with pytest.raises(KeyError, match="ghost"):
        _tokens(obs, fleet, c)


def test_tokens_ground_object_type_and_flags():
    c = EncoderConfig()
    gos = load_ground_objects("examples/herrenteich/fleet.yaml")
    fixed = gos["maul_fuel_trailer"]  # fixed_obstacle
    mover = gos["vw_caddy"]  # placed_routed_mover, hard_door_mover=True
    assert fixed.object_class == "fixed_obstacle"
    assert mover.object_class == "placed_routed_mover" and mover.hard_door_mover
    bodies = {"maul_fuel_trailer": fixed, "vw_caddy": mover}
    pl_fixed = Placement(
        plane_id="maul_fuel_trailer", x_m=8.0, y_m=10.0, heading_deg=0.0, on_carts=False
    )
    pl_mover = Placement(plane_id="vw_caddy", x_m=12.0, y_m=10.0, heading_deg=0.0, on_carts=False)
    obs = _obs(
        parked=(
            ParkedObject(object_id="maul_fuel_trailer", placement=pl_fixed),
            ParkedObject(object_id="vw_caddy", placement=pl_mover),
        )
    )
    tokens, mask, _ = _tokens(obs, bodies, c)
    assert list(mask[:2]) == [True, True]
    # fixed_obstacle -> type col 4; placed_routed_mover -> type col 5
    assert tokens[0, 4] == 1.0 and tokens[0, 5] == 0.0
    assert tokens[1, 4] == 0.0 and tokens[1, 5] == 1.0
    # neither sets the aircraft type bit (col 3)
    assert tokens[0, 3] == 0.0 and tokens[1, 3] == 0.0
    # hard_door_mover at col 17: only the caddy
    assert tokens[0, 17] == 0.0 and tokens[1, 17] == 1.0
    # wing (8..10) and movement (11..13) one-hots are all-zero for ground objects
    assert tokens[0, 8:14].sum() == 0.0 and tokens[1, 8:14].sum() == 0.0


# ---------------------------------------------------------------------------
# #752: static/dynamic raster split + uint8 dynamic block, for the IPC trim.
# The full encode() stays the 7-channel float32 reference (above); the worker
# path ships only the 3 dynamic channels as uint8 and the parent re-prepends a
# cached static block. The contract: reassemble(static_block, encode_dynamic) is
# bit-for-bit equal to encode(). (Local imports keep these RED scoped to the new
# symbols until they exist.)
# ---------------------------------------------------------------------------


def test_static_block_equals_full_static_channels_bitwise():
    """static_block(hangar, config) is the (4,H,W) float32 block encode() prepends —
    bit-for-bit equal to the first STATIC_CHANNELS channels of the full raster."""
    from ml.encoding import STATIC_CHANNELS, static_block

    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    sb = static_block(h, c)
    assert sb.shape == (STATIC_CHANNELS, 192, 96) and sb.dtype == np.float32
    full = encode(_two_body_obs(fleet), h, fleet, c).raster
    assert np.array_equal(sb, full[:STATIC_CHANNELS])


def test_encode_dynamic_raster_is_binary_uint8():
    """encode_dynamic ships ONLY the dynamic channels (low/wing/active) as uint8 0/1 —
    4x smaller on the wire, and provably binary so float32(0/1)->uint8 is lossless."""
    from ml.encoding import DYNAMIC_CHANNELS, encode_dynamic

    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    dyn = encode_dynamic(_two_body_obs(fleet), h, fleet, c).raster
    assert dyn.shape == (DYNAMIC_CHANNELS, 192, 96) and dyn.dtype == np.uint8
    assert set(np.unique(dyn)).issubset({0, 1})


def test_encode_dynamic_nonraster_fields_match_full_encode():
    """encode_dynamic differs from encode ONLY in the raster; tokens/mask/active_index/
    legal/meta are produced by the same code, so they are bit-identical."""
    from ml.encoding import encode_dynamic

    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    obs = _two_body_obs(fleet)
    full = encode(obs, h, fleet, c)
    dyn = encode_dynamic(obs, h, fleet, c)
    assert np.array_equal(dyn.tokens, full.tokens)
    assert np.array_equal(dyn.token_mask, full.token_mask)
    assert np.array_equal(dyn.legal_action_mask, full.legal_action_mask)
    assert dyn.active_index == full.active_index
    assert dyn.meta == full.meta
    assert dyn.schema_version == full.schema_version


def test_reassemble_raster_equals_full_encode_bitwise():
    """The byte-identity heart of #752: re-prepending the cached static_block onto the
    uint8 dynamic block reproduces encode()'s (7,H,W) float32 raster bit-for-bit."""
    from ml.encoding import RASTER_CHANNELS, encode_dynamic, reassemble_raster, static_block

    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    obs = _two_body_obs(fleet)
    full = encode(obs, h, fleet, c).raster
    dyn = encode_dynamic(obs, h, fleet, c).raster
    reassembled = reassemble_raster(static_block(h, c), dyn)
    assert reassembled.shape == (RASTER_CHANNELS, 192, 96) and reassembled.dtype == np.float32
    assert np.array_equal(reassembled, full)
    assert reassembled.tobytes() == full.tobytes()


def test_reassemble_raster_rejects_wrong_channel_counts():
    """A wrong-sized static or dynamic block must fail loudly in reassemble_raster, not
    silently concat to the wrong channel count and surface as a cryptic conv error far
    downstream (silent-failure review of #752)."""
    from ml.encoding import DYNAMIC_CHANNELS, STATIC_CHANNELS, reassemble_raster

    good_static = np.zeros((STATIC_CHANNELS, 8, 8), np.float32)
    good_dyn = np.zeros((DYNAMIC_CHANNELS, 8, 8), np.uint8)
    # sanity: the well-formed pair reassembles
    assert reassemble_raster(good_static, good_dyn).shape[0] == STATIC_CHANNELS + DYNAMIC_CHANNELS
    with pytest.raises(ValueError, match="static"):
        reassemble_raster(np.zeros((STATIC_CHANNELS + 1, 8, 8), np.float32), good_dyn)
    with pytest.raises(ValueError, match="dynamic"):
        reassemble_raster(good_static, np.zeros((DYNAMIC_CHANNELS + 1, 8, 8), np.uint8))

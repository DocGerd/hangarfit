"""Tests for the cold-joint policy network (ml/policy.py). Requires torch."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # whole module skips without the [train] extra

from hangarfit.models import Placement  # noqa: E402
from ml.encoding import EncoderConfig, encode  # noqa: E402
from ml.policy import HangarFitPolicy, PolicyOutput, _sincos_pos_2d, to_batch  # noqa: E402
from ml.types import ActiveObject, Observation, Park, ParkedObject, Pose, Primitive  # noqa: E402
from tests.ml.conftest import _fuji, empty_hangar  # noqa: E402


def _obs():
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
    )
    obs = Observation(
        active=active,
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        unplaced_ids=("cessna_150",),
        steps_this_object=0,
        steps_total=0,
    )
    return encode(obs, empty_hangar(), fleet, EncoderConfig())


def test_to_batch_shapes_and_dtypes():
    batch = to_batch([_obs(), _obs()])
    assert batch["raster"].shape == (2, 7, 192, 96) and batch["raster"].dtype == torch.float32
    assert batch["tokens"].shape == (2, 16, 24)
    assert batch["token_mask"].shape == (2, 16) and batch["token_mask"].dtype == torch.bool
    assert batch["active_index"].shape == (2,) and batch["active_index"].dtype == torch.long
    assert (
        batch["legal_action_mask"].shape == (2, 9)
        and batch["legal_action_mask"].dtype == torch.bool
    )


def _model(seed=0):
    torch.manual_seed(seed)
    return HangarFitPolicy(d_model=64, n_layers=2, n_heads=4).eval()


def test_forward_output_shapes():
    out = _model()(to_batch([_obs(), _obs()]))
    assert isinstance(out, PolicyOutput)
    assert out.kind_gear_logits.shape == (2, 9)
    assert out.magnitude_bin_logits.shape == (2, 5)
    assert out.value.shape == (2,)


def test_illegal_kinds_are_masked_to_zero_probability():
    batch = to_batch([_obs()])  # fuji/husky are own-gear -> strafe (idx 6,7) illegal
    out = _model()(batch)
    legal = batch["legal_action_mask"][0]
    probs = out.kind_gear_logits.softmax(-1)[0]
    assert torch.all(probs[~legal] == 0.0)  # illegal -> exactly 0 after softmax
    assert torch.isclose(probs[legal].sum(), torch.tensor(1.0))  # legal mass sums to 1


def test_forward_is_deterministic_in_eval():
    batch = to_batch([_obs()])
    m = _model(seed=3)
    a, b = m(batch), m(batch)
    assert torch.equal(a.kind_gear_logits, b.kind_gear_logits)
    assert torch.equal(a.magnitude_bin_logits, b.magnitude_bin_logits)
    assert torch.equal(a.value, b.value)


def test_gradients_flow():
    m = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4)  # train mode
    out = m(to_batch([_obs(), _obs()]))
    # avoid the -inf masked kind logits in the loss; use mag logits + value
    loss = out.magnitude_bin_logits.sum() + out.value.sum()
    loss.backward()
    assert any(p.grad is not None and torch.any(p.grad != 0) for p in m.parameters())


def test_single_and_batched_consistency():
    m = _model(seed=1)
    o = _obs()
    single = m(to_batch([o]))
    batched = m(to_batch([o, o]))
    assert torch.allclose(single.value, batched.value[:1], atol=1e-5)


def test_act_returns_only_legal_actions_and_decodes():
    m = _model(seed=2)
    obs_t = _obs()  # active = aviat_husky, own-gear (turn_radius > 0): strafe illegal
    tr = _fuji()["aviat_husky"].effective_turn_radius_m()
    legal = obs_t.legal_action_mask
    for _ in range(50):
        (kind_idx, mag_idx), log_prob, decoded = m.act(obs_t, turn_radius_m=tr)
        assert legal[kind_idx]  # never samples an illegal (kind,gear)
        assert isinstance(decoded, (Primitive, Park))
        assert isinstance(log_prob, float)
        assert 0 <= mag_idx < 5


def test_act_deterministic_takes_argmax():
    m = _model(seed=2)
    obs_t = _obs()
    tr = _fuji()["aviat_husky"].effective_turn_radius_m()
    a = m.act(obs_t, turn_radius_m=tr, deterministic=True)
    b = m.act(obs_t, turn_radius_m=tr, deterministic=True)
    assert a[0] == b[0]  # same (kind_idx, mag_idx) every time


def test_act_deterministic_in_train_mode_raises():
    # A model NOT put in eval() has live dropout, so deterministic=True is not
    # reproducible — act() must reject it rather than return a flaky argmax.
    m = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4)  # train mode (default)
    obs_t = _obs()
    tr = _fuji()["aviat_husky"].effective_turn_radius_m()
    with pytest.raises(RuntimeError, match="eval"):
        m.act(obs_t, turn_radius_m=tr, deterministic=True)


def _terminal_obs():
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = Observation(
        active=None,  # terminal: no active object -> active_index < 0, all-False mask
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        unplaced_ids=(),
        steps_this_object=0,
        steps_total=0,
    )
    return encode(obs, empty_hangar(), fleet, EncoderConfig())


def test_act_on_terminal_observation_raises():
    m = _model(seed=2)
    obs_t = _terminal_obs()
    assert obs_t.active_index < 0  # precondition: this really is a terminal obs
    with pytest.raises(ValueError, match="terminal"):
        m.act(obs_t, turn_radius_m=8.0)


# ---------------------------------------------------------------------------
# #752: to_batch discriminator — a trimmed (uint8/3ch) worker obs is rehydrated
# with a cached static block; a full (float32/7ch) obs passes straight through.
# ---------------------------------------------------------------------------


def _obs_full_and_dynamic():
    """The SAME observation, encoded both ways: encode (full 7ch f32) + encode_dynamic
    (3ch uint8). Returns (full, dynamic, hangar, config)."""
    from ml.encoding import encode_dynamic

    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky",
        body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
    )
    obs = Observation(
        active=active,
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        unplaced_ids=("cessna_150",),
        steps_this_object=0,
        steps_total=0,
    )
    h, c = empty_hangar(), EncoderConfig()
    return encode(obs, h, fleet, c), encode_dynamic(obs, h, fleet, c), h, c


def test_to_batch_reassembles_trimmed_with_static_block():
    """A trimmed (3ch uint8) worker obs + the cached static block rehydrates to the SAME
    batched raster as the full (7ch f32) obs — bit-for-bit (the #752 byte-identity seam)."""
    from ml.encoding import static_block

    full, dyn, h, c = _obs_full_and_dynamic()
    sb = static_block(h, c)
    full_batch = to_batch([full, full])
    trimmed_batch = to_batch([dyn, dyn], static_block=sb)
    assert trimmed_batch["raster"].shape == (2, 7, 192, 96)
    assert trimmed_batch["raster"].dtype == torch.float32
    assert torch.equal(trimmed_batch["raster"], full_batch["raster"])
    # the discriminator only touches the raster; the rest is unchanged
    assert torch.equal(trimmed_batch["tokens"], full_batch["tokens"])
    assert torch.equal(trimmed_batch["legal_action_mask"], full_batch["legal_action_mask"])
    assert torch.equal(trimmed_batch["active_index"], full_batch["active_index"])


def test_to_batch_trimmed_without_static_block_raises():
    """A trimmed obs with no static block is a programming error, not a silent wrong path:
    to_batch must raise rather than hand the policy a 3-channel raster."""
    _full, dyn, _h, _c = _obs_full_and_dynamic()
    with pytest.raises((ValueError, RuntimeError)):
        to_batch([dyn])  # uint8/3ch but no static_block supplied


def test_to_batch_full_obs_ignores_static_block():
    """The discriminator keys on the uint8 dtype, so a full float32 obs passes straight
    through even if a static_block is supplied — full obs never need reassembly."""
    from ml.encoding import static_block

    full, _dyn, h, c = _obs_full_and_dynamic()
    sb = static_block(h, c)
    assert torch.equal(to_batch([full], static_block=sb)["raster"], to_batch([full])["raster"])


def test_to_batch_rejects_mixed_full_and_trimmed_rasters():
    """A batch mixing full (float32/7ch) and trimmed (uint8/3ch) obs is a wiring error;
    to_batch must reject it rather than reassemble a full obs into a wrong-shaped raster
    (silent-failure + ml-rl-guard review of #752)."""
    from ml.encoding import static_block

    full, dyn, h, c = _obs_full_and_dynamic()
    sb = static_block(h, c)
    with pytest.raises(ValueError, match="mix"):
        to_batch([dyn, full], static_block=sb)  # obs[0] trimmed, obs[1] full


# ---------------------------------------------------------------------------
# #809: spatial-token cross-attention policy — fixed sin/cos 2D PE helper
# ---------------------------------------------------------------------------


def test_sincos_pos_2d_shape_finite_deterministic():
    pe = _sincos_pos_2d(24, 12, 64)
    assert pe.shape == (24 * 12, 64)
    assert pe.dtype == torch.float32
    assert torch.isfinite(pe).all()
    assert torch.equal(pe, _sincos_pos_2d(24, 12, 64))  # no RNG -> identical


def test_sincos_pos_2d_row_major_distinct_cells():
    pe = _sincos_pos_2d(24, 12, 64)
    # row-major: index = row*w + col. (0,0)=0, (0,1)=1, (1,0)=12 must all differ.
    assert not torch.equal(pe[0], pe[1])
    assert not torch.equal(pe[0], pe[12])


def test_sincos_pos_2d_requires_d_model_div4():
    with pytest.raises((AssertionError, ValueError)):
        _sincos_pos_2d(24, 12, 66)


# ---------------------------------------------------------------------------
# #809: spatial-token ON-branch forward
# ---------------------------------------------------------------------------


def _model_spatial(seed=0, d_model=64):
    torch.manual_seed(seed)
    return HangarFitPolicy(d_model=d_model, n_layers=2, n_heads=4, spatial_tokens=True).eval()


def test_spatial_on_forward_output_shapes():
    out = _model_spatial()(to_batch([_obs(), _obs()]))
    assert isinstance(out, PolicyOutput)
    assert out.kind_gear_logits.shape == (2, 9)
    assert out.magnitude_bin_logits.shape == (2, 5)
    assert out.value.shape == (2,)


def test_spatial_on_forward_deterministic_and_finite():
    m = _model_spatial(seed=5)
    batch = to_batch([_obs()])
    a, b = m(batch), m(batch)
    assert torch.isfinite(a.value).all()
    assert torch.isfinite(a.kind_gear_logits.nan_to_num(neginf=0.0)).all()
    assert torch.equal(a.value, b.value)
    assert torch.equal(a.kind_gear_logits, b.kind_gear_logits)


def test_spatial_on_still_masks_illegal_kinds():
    batch = to_batch([_obs()])
    out = _model_spatial()(batch)
    legal = batch["legal_action_mask"][0]
    probs = out.kind_gear_logits.softmax(-1)[0]
    assert torch.all(probs[~legal] == 0.0)


def test_feat_mean_equals_adaptive_avgpool():
    # The ON branch computes g = cnn_proj(feat.mean((2,3))); this must equal the old
    # AdaptiveAvgPool2d(1)+Flatten so the global pathway is preserved exactly.
    from torch import nn

    feat = torch.randn(2, 64, 24, 12)
    via_mean = feat.mean(dim=(2, 3))
    via_pool = nn.Flatten()(nn.AdaptiveAvgPool2d(1)(feat))
    assert torch.allclose(via_mean, via_pool, atol=1e-6)


# ---------------------------------------------------------------------------
# #809: default-neutrality — spatial_tokens=False is byte-identical to today
# ---------------------------------------------------------------------------


def test_spatial_off_is_byte_identical_to_default():
    # spatial_tokens=False (the default) must reproduce today's net exactly: same params,
    # same module order (same seed -> identical weights), same forward.
    torch.manual_seed(7)
    a = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4, spatial_tokens=False).eval()
    torch.manual_seed(7)
    b = HangarFitPolicy(d_model=64, n_layers=2, n_heads=4).eval()  # default
    sa, sb = a.state_dict(), b.state_dict()
    assert set(sa) == set(sb)
    for k in sa:
        assert torch.equal(sa[k], sb[k]), k
    oa, ob = a(to_batch([_obs()])), b(to_batch([_obs()]))
    assert torch.equal(oa.kind_gear_logits, ob.kind_gear_logits)
    assert torch.equal(oa.magnitude_bin_logits, ob.magnitude_bin_logits)
    assert torch.equal(oa.value, ob.value)


def test_spatial_off_registers_no_spatial_params():
    m = HangarFitPolicy(d_model=64, spatial_tokens=False)
    assert not any("spatial_proj" in k for k in m.state_dict())
    assert m.value_head[0].weight.shape == (64, 128)  # 2*d_model input


def test_spatial_on_adds_spatial_proj_and_widens_value_head():
    m = HangarFitPolicy(d_model=64, spatial_tokens=True)
    assert any("spatial_proj" in k for k in m.state_dict())
    assert m.value_head[0].weight.shape == (64, 192)  # 3*d_model input

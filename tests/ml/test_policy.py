"""Tests for the cold-joint policy network (ml/policy.py). Requires torch."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # whole module skips without the [train] extra

from hangarfit.models import Placement  # noqa: E402
from ml.encoding import EncoderConfig, encode  # noqa: E402
from ml.policy import to_batch  # noqa: E402
from ml.types import ActiveObject, Observation, ParkedObject, Pose  # noqa: E402
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

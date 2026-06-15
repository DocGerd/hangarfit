"""HangarFitEnv — the cold-joint RL environment (spec §4, §9). Plain gym-style class;
no gymnasium/torch dependency (those arrive in the training rung #3)."""

from __future__ import annotations

from collections.abc import Mapping

from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import Pose
from ml.types import (
    ActiveObject,
    DifficultyConfig,
    Observation,
    ParkedObject,
    RewardWeights,
)


class HangarFitEnv:
    """Drive each requested object in from the apron and park it, one at a time."""

    def __init__(
        self,
        *,
        hangar: Hangar,
        fleet: Mapping[str, Aircraft],
        requested_ids: tuple[str, ...],
        ground_objects: Mapping[str, GroundObject] | None = None,
        difficulty: DifficultyConfig | None = None,
        weights: RewardWeights | None = None,
    ) -> None:
        self.hangar = hangar
        self.fleet = dict(fleet)
        self.ground_objects = dict(ground_objects or {})
        self.requested_ids = requested_ids
        self.difficulty = difficulty or DifficultyConfig()
        self.weights = weights or RewardWeights()
        self._reset_state()

    def _reset_state(self) -> None:
        n = self.difficulty.max_objects
        self._queue: list[str] = list(self.requested_ids if n is None else self.requested_ids[:n])
        self._parked: list[Placement] = []
        self._active_id: str | None = None
        self._active_pose: Pose | None = None
        self._prev_gear: int | None = None
        self._steps_this_object = 0
        self._steps_total = 0
        self._prev_potential = 0.0

    def _body(self, object_id: str) -> Aircraft | GroundObject:
        return self.fleet[object_id] if object_id in self.fleet else self.ground_objects[object_id]

    def _on_carts(self, object_id: str) -> bool:
        body = self._body(object_id)
        return getattr(body, "movement_mode", None) == "always_cart" or getattr(
            body, "on_carts", False
        )

    def _spawn(self) -> None:
        """Pop the next queued object and place it on the apron at the door centre."""
        self._active_id = self._queue.pop(0)
        depth = self.hangar.apron_depth_m or 0.0
        self._active_pose = Pose(
            x_m=self.hangar.door.center_x_m,
            y_m=-(depth / 2.0 if depth else 0.0),
            heading_deg=0.0,
        )
        self._prev_gear = None
        self._steps_this_object = 0

    def _layout(self) -> Layout:
        """The scene of FROZEN (parked) objects only — the active one is not yet in it."""
        parked_ids = [p.plane_id for p in self._parked]
        ac = {pid: self.fleet[pid] for pid in parked_ids if pid in self.fleet}
        go_ids = [pid for pid in parked_ids if pid in self.ground_objects]
        return Layout(
            fleet=ac or {next(iter(self.fleet)): next(iter(self.fleet.values()))},
            hangar=self.hangar,
            placements=tuple(p for p in self._parked if p.plane_id in self.fleet),
            ground_objects={pid: self.ground_objects[pid] for pid in go_ids},
            ground_object_placements=tuple(
                p for p in self._parked if p.plane_id in self.ground_objects
            ),
        )

    def _observe(self) -> Observation:
        active = None
        if self._active_id is not None and self._active_pose is not None:
            active = ActiveObject(
                object_id=self._active_id,
                body=self._body(self._active_id),
                pose=self._active_pose,
                on_carts=self._on_carts(self._active_id),
            )
        return Observation(
            active=active,
            parked=tuple(ParkedObject(p.plane_id, p) for p in self._parked),
            unplaced_ids=tuple(self._queue),
            steps_this_object=self._steps_this_object,
            steps_total=self._steps_total,
        )

    def _potential(self) -> float:
        # Temporary stub (Task 10 Step 4); replaced by the real Φ in Task 11.
        return 0.0

    def reset(self) -> Observation:
        self._reset_state()
        self._spawn()
        self._prev_potential = self._potential()
        return self._observe()

"""HangarFitEnv — the cold-joint RL environment (spec §4, §9). Plain gym-style class;
no gymnasium/torch dependency (those arrive in the training rung #3)."""

from __future__ import annotations

from collections.abc import Mapping

from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import Pose
from ml import geometry_oracle as go
from ml.reward import RewardContext, potential, step_reward
from ml.types import (
    Action,
    ActiveObject,
    DifficultyConfig,
    Observation,
    Park,
    ParkedObject,
    RewardWeights,
    StepInfo,
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
        """Whether ``object_id`` moves cart-like (free-swivel, strafe-eligible).

        Handles both body types (``GroundObject`` has no ``movement_mode``/``on_carts``):
        an aircraft is cart-like when ``always_cart`` (or already on carts); a ground
        object is cart-like when it is a free-swivel ``towed`` mover — equivalently a
        ``placed_routed_mover`` with ``effective_turn_radius_m() == 0.0`` (a steerable
        car has a positive turning circle → own-gear fan, no strafe). A fixed obstacle
        never moves, so it is never cart-like.
        """
        body = self._body(object_id)
        if isinstance(body, GroundObject):
            return body.object_class == "placed_routed_mover" and body.motion_mode == "towed"
        return body.movement_mode == "always_cart" or getattr(body, "on_carts", False)

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

    def _active_dist_to_slot_m(self) -> float:
        """Distance from the active object to a valid parking region — approximated
        as how far inside the door it still needs to travel (y from apron to >=0).
        A coarse but monotone signal for shaping; refined in #4."""
        if self._active_pose is None:
            return 0.0
        return max(0.0, -self._active_pose.y_m)

    def _potential(self) -> float:
        layout = self._layout()
        remaining_overlap = go.overlap_area_m2(layout) if self._parked else 0.0
        return potential(
            remaining_overlap_m2=remaining_overlap,
            active_dist_to_slot_m=self._active_dist_to_slot_m(),
            unplaced=len(self._queue) + (1 if self._active_id is not None else 0),
        )

    def reset(self, requested_ids: tuple[str, ...] | None = None) -> Observation:
        if requested_ids is not None:
            if not requested_ids:
                raise ValueError("reset: requested_ids must be non-empty")
            known = set(self.fleet) | set(self.ground_objects)
            unknown = [i for i in requested_ids if i not in known]
            if unknown:
                raise ValueError(f"reset: unknown requested ids {unknown} (known: {sorted(known)})")
            # DifficultyConfig.max_objects caps the requested set: truncate here so the
            # episode size the env actually drives (the queue) matches StepInfo.total /
            # terminal_fraction, which divide by len(self.requested_ids). Without this a
            # caller passing more ids than max_objects would make fraction_placed cap
            # below 1.0 even on a fully-solved episode (silently starving the competency
            # gate). No-op for the curriculum, whose sample_request draws exactly N.
            n = self.difficulty.max_objects
            self.requested_ids = requested_ids if n is None else requested_ids[:n]
        self._reset_state()
        self._spawn()
        self._prev_potential = self._potential()
        return self._observe()

    def step(self, action: Action) -> tuple[Observation, float, bool, StepInfo]:
        assert self._active_id is not None and self._active_pose is not None, "step after done"
        active_id = self._active_id
        active_pose = self._active_pose
        self._steps_total += 1
        self._steps_this_object += 1
        body = self._body(active_id)
        parked_layout = self._layout()
        weights = self.weights

        if isinstance(action, Park):
            # Freeze the active pose; score its parked validity (overlap + bounds).
            pl = Placement(
                plane_id=active_id,
                x_m=active_pose.x_m,
                y_m=active_pose.y_m,
                heading_deg=active_pose.heading_deg,
                on_carts=self._on_carts(active_id),
            )
            self._parked.append(pl)
            placed_layout = self._layout()
            overlap = go.overlap_area_m2(placed_layout)
            intrusion = go.intrusion_area_m2(body, pl, self.hangar)
            egress = go.egress_blocked(placed_layout)
            self._active_id = None
            self._active_pose = None
            done = not self._queue
            terminal_fraction = len(self._parked) / len(self.requested_ids) if done else None
            if not done:
                self._spawn()
            new_phi = self._potential()
            ctx = RewardContext(
                overlap_m2=overlap,
                intrusion_m2=intrusion,
                swept_intrusion_m2=0.0,
                egress_blocked=egress,
                move_cost=0.0,
                min_gap_m=0.0,
                seq_deviation=0.0,
                region_match=0.0,
                prev_potential=self._prev_potential,
                potential=new_phi,
                terminal_fraction=terminal_fraction,
            )
            reward = step_reward(ctx, weights)
            self._prev_potential = new_phi
            return (
                self._observe(),
                reward,
                done,
                self._info(ctx, done, "set complete" if done else ""),
            )

        # A movement primitive: integrate, grade the swept path, advance the pose.
        primitive = action
        end, swept = go.apply_primitive(
            active_pose, primitive, turn_radius_m=body.effective_turn_radius_m()
        )
        swept_intr = go.swept_intrusion_m2(
            body, swept, parked_layout=parked_layout, active_id=active_id
        )
        move_cost = go.movement_cost(
            primitive, prev_gear=self._prev_gear, cusp_penalty=weights.cusp_penalty
        )
        self._active_pose = end
        self._prev_gear = primitive.gear
        new_phi = self._potential()

        # Termination: per-object budget exhausted (unplaceable) or global budget hit.
        # Compute it BEFORE the reward so a budget-driven stop still earns the
        # "best partial" terminal fraction (spec §4.5) — the active object stays
        # unparked, so the fraction is over already-PARKED objects only.
        done, reason = self._check_budget()
        terminal_fraction = len(self._parked) / len(self.requested_ids) if done else None
        ctx = RewardContext(
            overlap_m2=0.0,
            intrusion_m2=0.0,
            swept_intrusion_m2=swept_intr,
            egress_blocked=False,
            move_cost=move_cost,
            min_gap_m=0.0,
            seq_deviation=0.0,
            region_match=0.0,
            prev_potential=self._prev_potential,
            potential=new_phi,
            terminal_fraction=terminal_fraction,
        )
        reward = step_reward(ctx, weights)
        self._prev_potential = new_phi

        return self._observe(), reward, done, self._info(ctx, done, reason)

    def _check_budget(self) -> tuple[bool, str]:
        if self._steps_this_object >= self.difficulty.per_object_step_budget:
            return True, "active object unplaceable (per-object budget)"
        if self._steps_total >= self.difficulty.total_step_budget:
            return True, "global step budget exhausted"
        return False, ""

    def _layout_valid(self) -> bool:
        """Whole-layout validity matching the deterministic checker the prime directive
        enforces: no part overlap, no out-of-bounds / notch / apron (y<0) intrusion by ANY
        parked body, and no Caddy hard-door egress violation. (StepInfo.valid previously
        checked overlap only, leaving the promotion gate looser than the real checker —
        #607 SP#4b review.) Reward terms read ctx, not this, so this is gate/reporting only."""
        layout = self._layout()
        if go.overlap_area_m2(layout) > 0.0:
            return False
        if go.egress_blocked(layout):
            return False
        return all(
            go.intrusion_area_m2(self._body(pl.plane_id), pl, self.hangar) == 0.0
            for pl in self._parked
        )

    def _info(self, ctx: RewardContext, done: bool, reason: str) -> StepInfo:
        return StepInfo(
            terms={
                "hard_overlap": ctx.overlap_m2,
                "hard_swept": ctx.swept_intrusion_m2,
                "hard_intrusion": ctx.intrusion_m2,
                "hard_egress": float(ctx.egress_blocked),
                "move_cost": ctx.move_cost,
                "shaping": ctx.potential - ctx.prev_potential,
                "terminal_fraction": ctx.terminal_fraction or 0.0,
            },
            valid=self._layout_valid(),
            placed=len(self._parked),
            total=len(self.requested_ids),
            reason=reason,
        )

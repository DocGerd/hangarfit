"""HangarFitEnv — the cold-joint RL environment (spec §4, §9). Plain gym-style class;
no gymnasium/torch dependency (those arrive in the training rung #3)."""

from __future__ import annotations

import dataclasses
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


def _lerp_heading_deg(a_deg: float, b_deg: float, t: float) -> float:
    """Interpolate ``a_deg`` -> ``b_deg`` along the SHORTEST arc, ``t`` in [0, 1]
    (t=0 -> a, t=1 -> b). Wraps to [0, 360)."""
    delta = ((b_deg - a_deg + 180.0) % 360.0) - 180.0
    return (a_deg + delta * t) % 360.0


def _backplay_corridor_pose(witness: Placement, door: Pose, phi: float) -> Pose:
    """Pose a fraction ``phi`` along the straight corridor from the witness park-pose
    (phi=0, the valid dense terminal) out to the door spawn (phi=1); heading interpolates on
    the shortest arc. A degenerate but kinematically-agnostic corridor (#821 v1): it realizes
    the reverse-curriculum reachable-state shift without a Reeds-Shepp solve. phi=0 returns the
    witness pose exactly; phi=1 returns the door pose exactly."""
    return Pose(
        x_m=witness.x_m + (door.x_m - witness.x_m) * phi,
        y_m=witness.y_m + (door.y_m - witness.y_m) * phi,
        heading_deg=_lerp_heading_deg(witness.heading_deg, door.heading_deg, phi),
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
        fixed_placements: tuple[Placement, ...] = (),
        anchor_placements: tuple[Placement, ...] = (),
        difficulty: DifficultyConfig | None = None,
        weights: RewardWeights | None = None,
    ) -> None:
        self.hangar = hangar
        self.fleet = dict(fleet)
        self.ground_objects = dict(ground_objects or {})
        self.requested_ids = requested_ids
        # Pre-placed immovable keep-outs (e.g. the fuel trailer): part of the scene
        # from step 0, but NEVER driven and NEVER in ``_parked`` (so terminal_fraction's
        # denominator stays the requested/driven set). Set here (scenario-level) rather
        # than in ``_reset_state`` so it survives ``reset()``.
        self._fixed: list[Placement] = list(fixed_placements)
        # #712 seed-anchor witness poses, keyed by object id. At reset, the first
        # ``difficulty.seed_anchor_k`` requested objects are pre-parked here (a k-prefix of a
        # valid witness layout is provably valid). Empty by default => the anchor mechanism is
        # inert (k stays 0) and reset is byte-identical to the empty-start env.
        self._anchor_by_id: dict[str, Placement] = {p.plane_id: p for p in anchor_placements}
        if len(self._anchor_by_id) != len(anchor_placements):
            ids = [p.plane_id for p in anchor_placements]
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(
                f"anchor_placements has duplicate object ids {dupes} (each anchored id needs "
                f"exactly one witness pose, else the anchor map desyncs from the pool)"
            )
        self.difficulty = difficulty or DifficultyConfig()
        self.weights = weights or RewardWeights()
        self._reset_state()

    def _reset_state(self, *, seed_anchor_k_override: int | None = None) -> None:
        n = self.difficulty.max_objects
        requested = list(self.requested_ids if n is None else self.requested_ids[:n])
        # #712 seed-anchor: pre-park the first ``k`` requested objects at their committed-witness
        # poses and drive only the remaining N-k. A k-prefix of a valid witness layout is provably
        # valid (removing objects cannot create overlap/intrusion/egress conflicts), so the partial
        # start is collision-free with NO runtime search. The per-episode request order is a fresh
        # seeded permutation (curriculum sample_request), so "anchor the prefix" == "anchor a
        # seeded-random k-subset" (#712 Q1). k=0 => _parked empty, _queue full => byte-identical.
        k = (
            self.difficulty.seed_anchor_k
            if seed_anchor_k_override is None
            else seed_anchor_k_override
        )
        if k:
            if k < 0 or k >= len(requested):
                raise ValueError(
                    f"seed_anchor_k={k} must satisfy 0 <= k < the requested set size "
                    f"{len(requested)} (at least one object must be left to drive in)"
                )
            missing = [i for i in requested[:k] if i not in self._anchor_by_id]
            if missing:
                raise ValueError(
                    f"seed_anchor_k={k} but no witness pose for anchored ids {missing} "
                    f"(known witness ids: {sorted(self._anchor_by_id)})"
                )
        self._parked: list[Placement] = [self._anchor_by_id[i] for i in requested[:k]]
        self._queue: list[str] = requested[k:]
        self._parked_version = 0
        self._score_cache: tuple[int, go.LayoutScore] | None = None
        self._obstacles_cache: tuple[int, str, go.ObstaclesT] | None = None
        self._active_id: str | None = None
        self._active_pose: Pose | None = None
        self._prev_gear: int | None = None
        self._steps_this_object = 0
        self._steps_total = 0
        self._prev_potential = 0.0
        # #720 one-shot: flipped True on the first Park that yields a valid layout, so the
        # r_first_valid bonus is paid once per episode. Cleared here every reset.
        self._first_valid_reached = False
        # #821 backplay: the per-episode corridor fraction phi (drawn by the curriculum sampler
        # and passed to reset()). None => no spawn override => byte-identical door spawn.
        self._backplay_phi: float | None = None

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
        """Pop the next queued object and place it on the apron at the door centre.

        #821 backplay: when an episode carries a corridor fraction ``self._backplay_phi`` and the
        spawned object has a witness park-pose, start it a fraction phi along the corridor from
        that pose (phi=0, near-solved) out to the door (phi=1) instead of at the door — but ONLY
        if that start is collision-free; otherwise fall back to the door spawn. The env never
        snaps or auto-parks: it only moves WHERE the episode begins.
        """
        self._active_id = self._queue.pop(0)
        depth = self.hangar.apron_depth_m or 0.0
        door_pose = Pose(
            x_m=self.hangar.door.center_x_m,
            y_m=-(depth / 2.0 if depth else 0.0),
            heading_deg=0.0,
        )
        self._active_pose = door_pose
        phi = self._backplay_phi
        if phi is not None and self._active_id in self._anchor_by_id:
            candidate = _backplay_corridor_pose(self._anchor_by_id[self._active_id], door_pose, phi)
            if self._backplay_admissible(candidate):
                self._active_pose = candidate
        self._prev_gear = None
        self._steps_this_object = 0

    def _backplay_admissible(self, candidate: Pose) -> bool:
        """The active object placed at ``candidate``, added to the frozen scene (parked + fixed),
        must be product-valid (no overlap / intrusion / egress block) — else backplay declines the
        corridor pose and the spawn stays at the door. phi=0 is the witness pose, so with the
        k=N-1 prefix already parked the full witness is valid and phi=0 always admits."""
        active_id = self._active_id
        assert active_id is not None
        pl = Placement(
            plane_id=active_id,
            x_m=candidate.x_m,
            y_m=candidate.y_m,
            heading_deg=candidate.heading_deg,
            on_carts=self._on_carts(active_id),
        )
        frozen = self._layout()
        if active_id in self.fleet:
            combined = dataclasses.replace(
                frozen,
                fleet={**frozen.fleet, active_id: self.fleet[active_id]},
                placements=frozen.placements + (pl,),
            )
        else:
            combined = dataclasses.replace(
                frozen,
                ground_objects={**frozen.ground_objects, active_id: self.ground_objects[active_id]},
                ground_object_placements=frozen.ground_object_placements + (pl,),
            )
        return go.layout_valid(combined)

    def _layout(self) -> Layout:
        """The scene of FROZEN objects: driven-in (parked) PLUS pre-placed fixed obstacles
        (immovable keep-outs). The active object is not yet in it. Fixed obstacles are NOT in
        ``_parked`` (so terminal_fraction is uncorrupted) but ARE in the scene so overlap /
        egress / motion-clearance see them."""
        frozen = self._parked + self._fixed
        frozen_ids = [p.plane_id for p in frozen]
        ac = {pid: self.fleet[pid] for pid in frozen_ids if pid in self.fleet}
        go_ids = [pid for pid in frozen_ids if pid in self.ground_objects]
        return Layout(
            fleet=ac or {next(iter(self.fleet)): next(iter(self.fleet.values()))},
            hangar=self.hangar,
            placements=tuple(p for p in frozen if p.plane_id in self.fleet),
            ground_objects={pid: self.ground_objects[pid] for pid in go_ids},
            ground_object_placements=tuple(p for p in frozen if p.plane_id in self.ground_objects),
        )

    def _parked_score(self) -> go.LayoutScore:
        """Cached score of the frozen ``_layout()`` (parked + fixed). Recomputed only when
        the parked set changes (``_parked_version`` bump). Empty set short-circuits to the
        trivial valid score without calling ``check``."""
        if not (self._parked or self._fixed):
            return go.LayoutScore(0.0, True, False)
        if self._score_cache is not None and self._score_cache[0] == self._parked_version:
            return self._score_cache[1]
        score = go.score_layout(self._layout())
        self._score_cache = (self._parked_version, score)
        return score

    def _parked_obstacles(self, active_id: str) -> go.ObstaclesT:
        """Cached frozen-parked obstacle set for swept-path clearance. Keyed on
        (parked_version, active_id) — the mover is excluded from obstacles, and the
        active object changes per driven body."""
        c = self._obstacles_cache
        if c is not None and c[0] == self._parked_version and c[1] == active_id:
            return c[2]
        obs = go.build_obstacles(self._layout(), active_id)
        self._obstacles_cache = (self._parked_version, active_id, obs)
        return obs

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
            # The observed frozen set is parked (driven-in) PLUS the pre-placed fixed
            # obstacles, so the tensorizer rasters + tokenizes the immovable keep-outs and
            # the policy can PERCEIVE what it is penalized for colliding with. Fixed
            # obstacles stay out of ``_parked``, so info.placed (= len(_parked)) and
            # terminal_fraction (= len(_parked)/len(requested_ids)) are unchanged — the
            # denominator is the driven/requested set only, not the full observed set.
            parked=tuple(ParkedObject(p.plane_id, p) for p in self._parked + self._fixed),
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
        remaining_overlap = self._parked_score().penetration_m2
        misfit = 0.0
        if (
            self.weights.dense_slot_potential
            and self._active_pose is not None
            and self._active_id is not None
        ):
            misfit = go.active_misfit_m2(
                self._body(self._active_id), self._active_pose, layout, self.hangar
            )
        return potential(
            remaining_overlap_m2=remaining_overlap,
            active_dist_to_slot_m=self._active_dist_to_slot_m(),
            unplaced=len(self._queue) + (1 if self._active_id is not None else 0),
            active_misfit_m2=misfit,
        )

    def reset(
        self,
        requested_ids: tuple[str, ...] | None = None,
        *,
        seed_anchor_k: int | None = None,
        backplay_phi: float | None = None,
    ) -> Observation:
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
        self._reset_state(seed_anchor_k_override=seed_anchor_k)
        # Set the per-episode backplay fraction BEFORE _spawn (which consumes it). _reset_state
        # cleared it to None, so a plain reset (backplay_phi=None) is byte-identical.
        self._backplay_phi = backplay_phi
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
            self._parked_version += 1
            score = self._parked_score()  # one check + one egress, cached
            overlap = score.penetration_m2
            egress = score.egress_blocked
            intrusion = go.intrusion_area_m2(body, pl, self.hangar, bay_closed=False)
            park_valid = score.collisions_valid and not score.egress_blocked
            # First Park of the episode to reach a valid layout fires the one-time bonus.
            first_valid_now = park_valid and not self._first_valid_reached
            if first_valid_now:
                self._first_valid_reached = True
            self._active_id = None
            self._active_pose = None
            done = not self._queue
            terminal_fraction = len(self._parked) / len(self.requested_ids) if done else None
            if not done:
                self._spawn()
            # #732: PBRS requires Φ(terminal)=0 so the undiscounted return carries no
            # spurious −Φ(terminal) bias; on the terminal Park the shaping reduces to
            # −Φ(prev). Φ is ~0 on a clean valid completion but nonzero on the non-clean
            # terminals (an object still unplaced, or residual overlap), so this is
            # load-bearing for the invalid/piled completions the curriculum distinguishes.
            new_phi = 0.0 if done else self._potential()
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
                park_valid=park_valid,
                # On the terminal Park, reuse the already-computed whole-layout product-checker
                # bool (== _layout_valid()) for the #714 validity-conditional terminal.
                terminal_valid=park_valid if done else None,
                first_valid_now=first_valid_now,
                # len(_parked) includes the just-appended pose; 0 on an invalid park so the
                # banked coverage credit never pays a pile (#812).
                valid_park_count=len(self._parked) if park_valid else 0,
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
            body,
            swept,
            parked_layout=parked_layout,
            active_id=active_id,
            obstacles=self._parked_obstacles(active_id),
        )
        move_cost = go.movement_cost(
            primitive, prev_gear=self._prev_gear, cusp_penalty=weights.cusp_penalty
        )
        self._active_pose = end
        self._prev_gear = primitive.gear

        # Termination: per-object budget exhausted (unplaceable) or global budget hit.
        # Compute it BEFORE the reward so a budget-driven stop still earns the
        # "best partial" terminal fraction (spec §4.5) — the active object stays
        # unparked, so the fraction is over already-PARKED objects only.
        done, reason = self._check_budget()
        # #732: PBRS requires Φ(terminal)=0 — on a terminal (budget-exhaustion) step the
        # shaping reduces to −Φ(prev), no spurious −Φ(terminal) return bias. Φ(prev) is
        # genuinely nonzero here (an object remains unplaced), so this is load-bearing.
        new_phi = 0.0 if done else self._potential()
        terminal_fraction = len(self._parked) / len(self.requested_ids) if done else None
        # Validity of the already-PARKED set at a budget-exhaustion stop. This branch carried
        # no validity signal, so the #714 validity-conditional terminal would have treated even
        # a valid partial as invalid — wire it from the product checker (cache-warm: a movement
        # primitive does not bump _parked_version). None when not terminal.
        terminal_valid = self._layout_valid() if done else None
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
            terminal_valid=terminal_valid,
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
        """Whole-layout validity == the product checker (the prime directive's final gate),
        via the shared ``geometry_oracle.layout_valid``. Reward terms read ctx, not this, so
        this is gate/reporting only. (Was hand-rolled overlap+intrusion+egress that
        over-enforced the inert maintenance bay — #607 SP#4c-ii / #694.)"""
        s = self._parked_score()
        return s.collisions_valid and not s.egress_blocked

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
                "valid_park_count": float(ctx.valid_park_count),
            },
            valid=self._layout_valid(),
            placed=len(self._parked),
            total=len(self.requested_ids),
            reason=reason,
        )

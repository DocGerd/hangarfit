"""Torch-gated tests for the #710 resume checkpoint (ml/checkpoint.py) + the
ReturnNormalizer state round-trip it depends on.

The contract proven here is CHECKPOINT ROUND-TRIP equivalence: --load restores EXACTLY
what was saved (forward-output identity + optimizer state + normalizer stats + curriculum
position). It deliberately does NOT assert whole-run fresh==resume byte-identity — torch CPU
is nondeterministic across processes, and the resume re-seeds the global RNG stream at a
different point (see the auto-memory note on ml byte-identity)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from ml.checkpoint import load_checkpoint, save_checkpoint  # noqa: E402
from ml.policy import HangarFitPolicy, to_batch  # noqa: E402
from ml.ppo import ReturnNormalizer  # noqa: E402


def _fixed_obs_batch() -> dict:
    """A single deterministic observation batch from the trivial env (for forward-identity)."""
    from ml.encoding import EncoderConfig, encode
    from ml.train import build_trivial_env

    env = build_trivial_env()
    obs = env.reset()
    bodies = {**env.fleet, **env.ground_objects}
    obs_t = encode(obs, env.hangar, bodies, EncoderConfig())
    return to_batch([obs_t])


def _opt_state_equal(a: dict, b: dict) -> bool:
    if a["param_groups"] != b["param_groups"]:
        return False
    sa, sb = a["state"], b["state"]
    if set(sa) != set(sb):
        return False
    for k in sa:
        if set(sa[k]) != set(sb[k]):
            return False
        for name in sa[k]:
            va, vb = sa[k][name], sb[k][name]
            if torch.is_tensor(va):
                if not torch.equal(va, vb):
                    return False
            elif va != vb:
                return False
    return True


# --- Cycle A: ReturnNormalizer state round-trip ----------------------------------


def test_return_normalizer_state_dict_roundtrip():
    rn = ReturnNormalizer(eps=1e-6, warmup=4)
    for _ in range(10):
        rn.normalize(torch.tensor([1.0, 2.0, 3.0]))
    state = rn.state_dict()
    rn2 = ReturnNormalizer()  # different eps/warmup defaults — load must overwrite them
    rn2.load_state_dict(state)
    assert rn2.state_dict() == state
    # Behavioral: identical running stats -> identical scaling AND identical post-state.
    x = torch.tensor([1.5, -2.5, 0.25])
    out1 = rn.normalize(x.clone())
    out2 = rn2.normalize(x.clone())
    assert torch.equal(out1, out2)
    assert rn.state_dict() == rn2.state_dict()


# --- Cycle B: checkpoint save/load round-trip ------------------------------------


def test_save_load_checkpoint_roundtrip(tmp_path):
    torch.manual_seed(0)
    pk = {"d_model": 32, "n_layers": 1, "n_heads": 2}
    policy = HangarFitPolicy(**pk)
    opt = torch.optim.Adam(policy.parameters(), lr=1e-3)
    batch = _fixed_obs_batch()
    out = policy(batch)
    # A finite loss (kind_gear_logits carry -inf masked slots, so exclude them) that
    # populates real Adam state for the shared trunk + value/mag heads.
    opt.zero_grad()
    loss = (out.value**2).sum() + out.magnitude_bin_logits.pow(2).sum()
    loss.backward()
    opt.step()
    rn = ReturnNormalizer()
    rn.normalize(torch.tensor([1.0, 2.0, 3.0, 4.0, 5.0]))

    ckpt_path = tmp_path / "ckpt.pt"
    save_checkpoint(
        ckpt_path,
        policy=policy,
        optimizer=opt,
        normalizer=rn,
        policy_kwargs=pk,
        completed_stages=["t0"],
    )
    loaded = load_checkpoint(ckpt_path)
    assert loaded.policy_kwargs == pk
    assert loaded.completed_stages == ["t0"]

    # Forward-output identity (the handoff's preferred assertion, not a checkpoint hash).
    policy2 = HangarFitPolicy(**loaded.policy_kwargs)
    policy2.load_state_dict(loaded.policy_state)
    policy.eval()
    policy2.eval()
    with torch.no_grad():
        a, b = policy(batch), policy2(batch)
    assert torch.equal(a.value, b.value)
    assert torch.equal(a.magnitude_bin_logits, b.magnitude_bin_logits)
    assert torch.equal(a.kind_gear_logits, b.kind_gear_logits)

    # Optimizer state restoration (Adam moments + step).
    opt2 = torch.optim.Adam(policy2.parameters(), lr=1e-3)
    opt2.load_state_dict(loaded.optimizer_state)
    assert _opt_state_equal(opt.state_dict(), opt2.state_dict())

    # Normalizer stats restoration.
    assert loaded.normalizer_state is not None
    rn2 = ReturnNormalizer()
    rn2.load_state_dict(loaded.normalizer_state)
    assert rn2.state_dict() == rn.state_dict()


def test_save_load_checkpoint_roundtrip_spatial_tokens(tmp_path):
    # The ON (spatial_tokens=True) architecture must survive save -> load -> reconstruct via the
    # persisted policy_kwargs (the #809 "persisted in the checkpoint's policy_kwargs" contract),
    # with forward-output identity. Mirrors test_save_load_checkpoint_roundtrip on the ON branch.
    torch.manual_seed(0)
    pk = {"d_model": 32, "n_layers": 1, "n_heads": 2, "spatial_tokens": True}
    policy = HangarFitPolicy(**pk)
    batch = _fixed_obs_batch()
    ckpt_path = tmp_path / "ckpt_spatial.pt"
    save_checkpoint(
        ckpt_path,
        policy=policy,
        optimizer=torch.optim.Adam(policy.parameters(), lr=1e-3),
        normalizer=ReturnNormalizer(),
        policy_kwargs=pk,
        completed_stages=["t0"],
    )
    loaded = load_checkpoint(ckpt_path)
    assert loaded.policy_kwargs == pk
    assert loaded.policy_kwargs["spatial_tokens"] is True
    policy2 = HangarFitPolicy(**loaded.policy_kwargs)
    policy2.load_state_dict(loaded.policy_state)
    policy.eval()
    policy2.eval()
    with torch.no_grad():
        a, b = policy(batch), policy2(batch)
    assert torch.equal(a.value, b.value)
    assert torch.equal(a.magnitude_bin_logits, b.magnitude_bin_logits)


def test_save_load_checkpoint_normalizer_none(tmp_path):
    # normalize_returns off -> no normalizer -> the checkpoint round-trips None cleanly.
    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=1e-3)
    ckpt_path = tmp_path / "ckpt.pt"
    save_checkpoint(
        ckpt_path,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=[],
    )
    loaded = load_checkpoint(ckpt_path)
    assert loaded.normalizer_state is None
    assert loaded.policy_kwargs == {}
    assert loaded.completed_stages == []


def test_load_checkpoint_version_mismatch_raises(tmp_path):
    ckpt_path = tmp_path / "bad.pt"
    torch.save({"version": 999, "policy_state": {}}, str(ckpt_path))
    with pytest.raises(ValueError, match="version"):
        load_checkpoint(ckpt_path)


def test_load_checkpoint_missing_required_key_raises(tmp_path):
    # A correct-version but incomplete payload (e.g. a truncated/hand-built file) must fail
    # LOUD with a corrupt-checkpoint message, not a bare KeyError deep in a stack trace.
    ckpt_path = tmp_path / "incomplete.pt"
    torch.save({"version": 1, "policy_kwargs": {}}, str(ckpt_path))  # missing the rest
    with pytest.raises(ValueError, match="missing|corrupt"):
        load_checkpoint(ckpt_path)


def test_save_checkpoint_writes_atomically_via_temp(tmp_path, monkeypatch):
    # save_checkpoint must write to a TEMP path then os.replace it onto the target, so a crash
    # mid-write can never corrupt an existing checkpoint. Spy on torch.save to assert it never
    # writes the final path directly, then assert the target exists with no temp left behind.
    import ml.checkpoint as ckpt_mod

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=1e-3)
    target = tmp_path / "ck.pt"
    written: list[str] = []
    real_save = torch.save

    def spy_save(obj, path, *a, **k):
        written.append(str(path))
        return real_save(obj, path, *a, **k)

    monkeypatch.setattr(ckpt_mod.torch, "save", spy_save)
    save_checkpoint(
        target,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=[],
    )
    assert written and written[0] != str(target)  # wrote a temp first, not the target
    assert target.exists()
    assert list(tmp_path.glob("*.tmp")) == []  # temp atomically replaced + cleaned up
    load_checkpoint(target)  # the final file is valid


def test_load_checkpoint_non_dict_payload_raises(tmp_path):
    # A non-dict payload (e.g. a bare tensor/list, or a --save state_dict mix-up) must fail
    # LOUD with a clear message, not an AttributeError on `.get()` (T2).
    bad = tmp_path / "notdict.pt"
    torch.save([1, 2, 3], str(bad))
    with pytest.raises(ValueError, match="checkpoint|resume"):
        load_checkpoint(bad)


def test_save_checkpoint_failed_write_preserves_existing_and_cleans_temp(tmp_path, monkeypatch):
    # The invariant the atomic write EXISTS for (T3/T7): a torch.save that fails mid-write must
    # leave the pre-existing checkpoint intact AND leave no stray temp file behind.
    import ml.checkpoint as ckpt_mod

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=1e-3)
    target = tmp_path / "ck.pt"
    save_checkpoint(
        target,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=["orig"],
    )

    def boom(obj, path, *a, **k):
        with open(path, "w") as f:
            f.write("partial")  # a partial temp file is created...
        raise RuntimeError("disk full mid-write")  # ...then the write fails

    monkeypatch.setattr(ckpt_mod.torch, "save", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        save_checkpoint(
            target,
            policy=policy,
            optimizer=opt,
            normalizer=None,
            policy_kwargs=None,
            completed_stages=["new"],
        )
    # os.replace never ran -> the pre-existing checkpoint is untouched...
    assert load_checkpoint(target).completed_stages == ["orig"]
    # ...and the failed write left no stray temp litter.
    assert list(tmp_path.glob("*.tmp")) == []

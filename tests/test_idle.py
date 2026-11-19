"""M5a idle life: the idle_acts.yaml loader, the picker, and the scheduler (pure, no I/O)."""

from __future__ import annotations

import itertools
import random
from collections import Counter
from pathlib import Path

import pytest

from app.idle import IdleActsError, IdlePicker, IdleScheduler, load_idle_acts

ACTS_YAML = """
version: 1
rotation: {{min_gap_s: {lo}, max_gap_s: {hi}, default_act: breathing, no_repeat: true}}
acts:
  - {{id: breathing, name: Breathing, duration_s: 0, weight: 0}}
  - {{id: a, name: A, duration_s: 4, weight: 3, caption: "Hi! I'm {{{{bot_name}}}}."}}
  - {{id: b, name: B, duration_s: 6, weight: 1}}
  - {{id: spin, name: Spin, duration_s: 3, weight: 2, reduced_motion: skip}}
  - {{id: doze, name: Doze, duration_s: 20, weight: 5, after_idle_s: 600, reduced_motion: gentle}}
  - {{id: parked, name: Parked, duration_s: 5, weight: 9, enabled: false}}
"""


def write(tmp_path: Path, text: str, name: str = "idle_acts.yaml") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def catalog(tmp_path: Path, lo: float = 45, hi: float = 120):
    return load_idle_acts(write(tmp_path, ACTS_YAML.format(lo=lo, hi=hi)), tmp_path, "Ada")


# -- loader ----------------------------------------------------------------------------------


def test_real_file_loads_with_reasons_for_the_acts_that_are_off(repo_root: Path) -> None:
    cat = load_idle_acts(repo_root / "content" / "idle_acts.yaml", repo_root)
    assert "breathing" not in cat.acts  # the default state, not an act
    assert len(cat.acts) >= 15
    # PM 2026-11-18: an act every 1-2 minutes, plus the "bored" acts
    assert cat.rotation.min_gap_s == 60 and cat.rotation.max_gap_s == 120
    bored = (
        "neural_doodle",
        "loading_bar",
        "sneeze",
        "chase_glow",
        "peekaboo",
        "what_am_i",
        "sigh",
    )
    for new in bored:
        assert new in cat.acts, new
    assert "enabled: false" in cat.unavailable["quiz_invite"]
    assert "M5c" in cat.unavailable["event_teaser"]  # needs the lab_docs event reader
    assert "fun_fact" in cat.acts  # the ai_fact card is approved (PM, 2026-11-18)
    assert cat.acts["fun_fact"].captions  # its facts become the captions
    for act in cat.acts.values():  # visual only: nothing here is spoken
        assert act.duration_s > 0 and act.weight > 0
        assert act.caption is None or "{{" not in act.caption


def test_every_playable_real_act_has_a_blob_motion(repo_root: Path) -> None:
    """ui/blob.js must implement every act the server can send."""
    cat = load_idle_acts(repo_root / "content" / "idle_acts.yaml", repo_root)
    js = (repo_root / "ui" / "blob.js").read_text(encoding="utf-8")
    missing = [aid for aid in cat.acts if f"  {aid}: (" not in js]
    assert not missing, f"no motion in ui/blob.js for {missing}"


def test_disabled_and_zero_weight_acts_are_not_playable(tmp_path: Path) -> None:
    cat = catalog(tmp_path)
    assert set(cat.acts) == {"a", "b", "spin", "doze"}
    assert cat.unavailable == {"parked": "enabled: false in idle_acts.yaml"}
    assert cat.acts["a"].caption == "Hi! I'm Ada."


def test_caption_from_needs_an_approved_card(tmp_path: Path) -> None:
    acts = (
        "rotation: {default_act: breathing}\nacts:\n"
        "  - {id: breathing, duration_s: 0, weight: 0}\n"
        "  - {id: fact, duration_s: 8, weight: 2, caption_from: 'content/cards.yaml#ai_fact.facts',"
        " caption_prefix: 'Did you know? '}\n"
    )
    (tmp_path / "content").mkdir()
    cards = tmp_path / "content" / "cards.yaml"
    card = "cards:\n  - id: ai_fact\n    approved: {}\n    facts: ['One.', 'Two.']\n"
    cards.write_text(card.format("TODO(PM)"), encoding="utf-8")
    off = load_idle_acts(write(tmp_path, acts), tmp_path)
    assert "fact" not in off.acts and "not approved" in off.unavailable["fact"]

    cards.write_text(card.format("yes"), encoding="utf-8")  # YAML reads `yes` as True
    on = load_idle_acts(write(tmp_path, acts), tmp_path)
    caption = on.acts["fact"].caption_for(random.Random(1))
    assert caption in ("Did you know? One.", "Did you know? Two.")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("acts: []\n", "default_act"),
        ("rotation: {min_gap_s: 9, max_gap_s: 2}\nacts: []\n", "max_gap_s"),
        (
            "acts:\n  - {id: breathing, weight: 0}\n  - {id: x, duration_s: 1}\n"
            "  - {id: x, duration_s: 1}\n",
            "duplicate",
        ),
        (
            "acts:\n  - {id: breathing, weight: 0}\n"
            "  - {id: x, duration_s: 1, reduced_motion: off}\n",
            "reduced_motion",
        ),
        ("acts:\n  - {id: breathing, weight: 0}\n  - {id: x, weight: -1}\n", "weight"),
        ("acts: nope\n", "acts"),
    ],
)
def test_bad_files_fail_loudly(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(IdleActsError, match=message):
        load_idle_acts(write(tmp_path, text), tmp_path)


# -- picker ----------------------------------------------------------------------------------


def test_never_the_same_act_twice_in_a_row(tmp_path: Path) -> None:
    picker = IdlePicker(catalog(tmp_path), random.Random(7))
    picks = [picker.pick(quiet_for_s=10_000).id for _ in range(2000)]
    assert all(x != y for x, y in itertools.pairwise(picks))
    assert set(picks) == {"a", "b", "spin", "doze"}


def test_weights_shape_the_pick(tmp_path: Path) -> None:
    picker = IdlePicker(catalog(tmp_path), random.Random(3))
    counts = Counter(picker.pick(quiet_for_s=0).id for _ in range(3000))
    assert "doze" not in counts  # after_idle_s 600
    assert counts["a"] > counts["spin"] > counts["b"]  # weights 3 > 2 > 1


def test_after_idle_s_and_reduced_motion_gate_acts(tmp_path: Path) -> None:
    picker = IdlePicker(catalog(tmp_path), random.Random(0))
    assert {a.id for a in picker.eligible(599, reduced=False)} == {"a", "b", "spin"}
    assert "doze" in {a.id for a in picker.eligible(600, reduced=False)}
    reduced = {a.id for a in picker.eligible(600, reduced=True)}
    assert "spin" not in reduced and "doze" in reduced  # skip vs gentle


def test_gap_stays_inside_the_rotation_window(tmp_path: Path) -> None:
    picker = IdlePicker(catalog(tmp_path), random.Random(5))
    gaps = [picker.gap() for _ in range(500)]
    assert min(gaps) >= 45 and max(gaps) <= 120


def test_single_act_catalog_waits_instead_of_repeating(tmp_path: Path) -> None:
    text = "acts:\n  - {id: breathing, weight: 0}\n  - {id: only, duration_s: 2}\n"
    picker = IdlePicker(load_idle_acts(write(tmp_path, text), tmp_path), random.Random(0))
    assert picker.pick(0).id == "only"
    assert picker.pick(0) is None  # no back-to-back repeat, even with one act


# -- scheduler -------------------------------------------------------------------------------


def run(sched: IdleScheduler, start: float, end: float, *, quiet: bool = True, step: float = 0.5):
    """Tick from start to end; return (time, act id) for every act played."""
    out = []
    t = start
    while t <= end:
        cue = sched.step(t, quiet=quiet)
        if cue:
            out.append((t, cue.act.id))
        t += step
    return out


def test_scheduler_waits_a_gap_then_rotates(tmp_path: Path) -> None:
    sched = IdleScheduler(IdlePicker(catalog(tmp_path, lo=10, hi=10), random.Random(1)))
    played = run(sched, 0, 100)
    assert played and played[0][0] == pytest.approx(10)  # first act after one gap, not at once
    for (t1, a1), (t2, a2) in itertools.pairwise(played):
        dur = {"a": 4, "b": 6, "spin": 3}[a1]
        assert t2 - t1 == pytest.approx(dur + 10)  # the act plays out, then a full gap
        assert a1 != a2


def test_interaction_restarts_the_quiet_clock(tmp_path: Path) -> None:
    sched = IdleScheduler(IdlePicker(catalog(tmp_path, lo=10, hi=10), random.Random(1)))
    assert run(sched, 0, 9) == []
    sched.interrupt(9.0)  # a tap at t=9: the act due at t=10 doesn't happen
    assert run(sched, 9.5, 19) == []
    assert run(sched, 19.5, 20)[0][0] == pytest.approx(19.5)  # a new gap from t=9.5


def test_no_acts_while_someone_is_there(tmp_path: Path) -> None:
    sched = IdleScheduler(IdlePicker(catalog(tmp_path, lo=1, hi=1), random.Random(1)))
    assert run(sched, 0, 50, quiet=False) == []
    assert run(sched, 50.5, 51.5)[0][0] == pytest.approx(51.5)  # gap counts from quiet again


def test_cue_event_is_what_the_screen_expects(tmp_path: Path) -> None:
    sched = IdleScheduler(IdlePicker(catalog(tmp_path, lo=0, hi=0), random.Random(0)))
    sched.step(0, quiet=True)
    cue = sched.step(0.1, quiet=True)
    assert cue is not None
    e = cue.event()
    assert e["type"] == "idle_act" and e["id"] in {"a", "b", "spin"}
    assert set(e) == {"type", "id", "duration_s", "caption", "reduced_motion"}

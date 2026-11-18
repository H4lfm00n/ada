"""M5b cards, server half (app/cards.py): catalog, tags, pick, yes/no, check-in loop."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.cards import (
    CardCatalog,
    CardsError,
    CardStage,
    classify_reply,
    load_cards,
    strip_card_tags,
)
from app.config import Settings
from app.guards import build_doc_allowlist

PLACEHOLDER = "https://ai.sfsu.edu/ai-students"


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cards.yaml"
    p.write_text(text, encoding="utf-8")
    return p


QR = """
defaults: {{confirm_after_s: 6, retry_after_s: 10, auto_hide_s: 60, placeholder_url: "{ph}"}}
cards:
  - id: qr_discord
    type: qr
    enabled: true
    approved: yes
    placeholder: true
    placeholder_note: "Discord invite coming soon"
    title: "Join the Discord"
    url: "{url}"
    say_before: "I've put the Discord QR code on the screen."
    confirm: "Did you scan it?"
    on_yes: "Nice, see you in there!"
    on_no: "No rush, take your time."
    on_no_or_timeout: "No rush, it'll stay up for a bit."
    triggers: [discord, join the discord]
    doc_sections: ["faq.md#How do I join"]
  - id: compute_rules
    type: checklist
    enabled: true
    approved: yes
    title: "Computer rules"
    items: ["No remote access"]
    say_before: "Here are the computer rules."
    auto_hide_s: 40
    triggers: [gpu, overnight]
    doc_sections: ["rules.md"]
  - id: campus_help
    type: contact
    enabled: true
    approved: yes
    title: "Get help now"
    items: ["Emergency: call 911"]
    auto_hide_s: 0
    show_with_lines: [emergency, crisis_support]
  - id: parked
    type: fun
    enabled: false
    approved: TODO(PM)
    facts: ["x"]
"""


def catalog(tmp_path: Path, url: str = PLACEHOLDER) -> CardCatalog:
    return load_cards(write(tmp_path, QR.format(ph=PLACEHOLDER, url=url)))


@dataclass
class FakeChunk:
    source_file: str
    heading: str


# -- tags --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "clean", "ids"),
    [
        (
            'I put it on the screen. <card id="qr_discord"/>',
            "I put it on the screen.",
            ["qr_discord"],
        ),
        ("Here you go <card id='map_where_we_are' />.", "Here you go.", ["map_where_we_are"]),
        ("<card id=compute_rules></card>", "", ["compute_rules"]),
        ('Scan it! <card id="qr_discord"', "Scan it!", ["qr_discord"]),  # cut off at the end
        ("No tags here.", "No tags here.", []),
        ('<CARD ID="a"/> and <card id="b"/>', "and", ["a", "b"]),
    ],
)
def test_card_tags_are_stripped(text: str, clean: str, ids: list[str]) -> None:
    assert strip_card_tags(text) == (clean, ids)


# -- yes / no ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["yes", "Yeah!", "got it", "I scanned it", "done thanks", "yep", "ok"]
)
def test_yes(text: str) -> None:
    assert classify_reply(text) == "yes"


@pytest.mark.parametrize("text", ["no", "not yet", "one sec", "hold on", "Nope.", "it didn't work"])
def test_no(text: str) -> None:
    assert classify_reply(text) == "no"


@pytest.mark.parametrize(
    "text",
    [
        "yes, how do I join the AI Commons?",  # a question: new turn, not an answer
        "what equipment do you have",
        "no way, robots dogs are real??",
        "tell me about transformers and how attention works in detail please",
        "",
    ],
)
def test_anything_else_is_a_new_question(text: str) -> None:
    assert classify_reply(text) is None


# -- catalog -----------------------------------------------------------------------------


def test_real_catalog_loads_with_placeholder_qr_cards(repo_root: Path) -> None:
    cat = load_cards(repo_root / "content" / "cards.yaml")
    assert set(cat.cards) == {
        "qr_project_proposal", "qr_discord", "map_where_we_are", "events_this_month",
        "equipment_spotlight", "compute_rules", "campus_help", "ai_fact",
    }  # fmt: skip
    for cid, note in [
        ("qr_discord", "Discord invite coming soon"),
        ("qr_project_proposal", "Proposal form link coming soon"),
    ]:
        d = cat.cards[cid].data
        assert d["url"] == PLACEHOLDER and d["placeholder"] is True
        assert d["placeholder_note"] == note
    # The UI never gets server-only fields.
    for c in cat.cards.values():
        assert not {"say_before", "triggers", "doc_sections", "source", "approved"} & set(c.data)
        assert c.type == "qr" or "url" not in c.data


def test_real_qr_urls_pass_the_docs_allowlist(repo_root: Path) -> None:
    s = Settings(chat_model="x", guard_model="y", embed_model="z")
    load_cards(repo_root / "content" / "cards.yaml").check_urls(build_doc_allowlist(s))


def test_qr_url_not_in_the_docs_is_refused(tmp_path: Path) -> None:
    s = Settings(chat_model="x", guard_model="y", embed_model="z")
    cat = catalog(tmp_path, url="https://free-gpu-credits.example.com/claim")
    with pytest.raises(CardsError, match="not in the lab docs"):
        cat.check_urls(build_doc_allowlist(s))


def test_swapping_the_url_is_one_line_and_hides_the_note(tmp_path: Path) -> None:
    cat = catalog(tmp_path, url="https://ai.sfsu.edu/real-invite")
    d = cat.cards["qr_discord"].data
    assert "placeholder" not in d and "placeholder_note" not in d
    assert any("placeholder: true" in w for w in cat.warnings)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            ("approved: yes\n    placeholder", "approved: TODO(PM)\n    placeholder"),
            "approved: yes",
        ),
        ((f'    url: "{PLACEHOLDER}"', '    url: "http://ai.sfsu.edu/x"'), "https://"),
        ((f'    url: "{PLACEHOLDER}"', '    url: "TODO(PM)"'), "https://"),
        (
            ('    items: ["No remote access"]', '    url: "https://x.edu"\n    items: [a]'),
            "only QR",
        ),
        (('    placeholder_note: "Discord invite coming soon"\n', ""), "placeholder_note"),
        (("type: checklist", "type: banner"), "type must be"),
    ],
)
def test_unsafe_catalogs_are_refused(tmp_path: Path, patch: tuple[str, str], message: str) -> None:
    text = QR.format(ph=PLACEHOLDER, url=PLACEHOLDER)
    assert patch[0] in text, patch[0]
    with pytest.raises(CardsError, match=message):
        load_cards(write(tmp_path, text.replace(patch[0], patch[1], 1)))


def test_prompt_list_names_enabled_cards_only(tmp_path: Path) -> None:
    text = catalog(tmp_path).prompt_list()
    assert "- qr_discord: Join the Discord (QR code)." in text
    assert "campus_help: emergency" in text
    assert "parked" not in text
    assert CardCatalog({}).prompt_list() == "none"


# -- pick --------------------------------------------------------------------------------


def test_a_valid_tag_wins(tmp_path: Path) -> None:
    pick = catalog(tmp_path).pick(["compute_rules"], "how do i join the discord", [], "model")
    assert pick is not None and (pick.card.id, pick.via) == ("compute_rules", "tag")


def test_unknown_or_disabled_tags_show_nothing(tmp_path: Path) -> None:
    cat = catalog(tmp_path)
    docs = [FakeChunk("faq.md", "How do I join the Student AI Commons?")]
    assert cat.pick(["qr_rickroll"], "join the discord", docs, "model") is None
    assert cat.pick(["parked"], "join the discord", docs, "model") is None


def test_backup_trigger_needs_the_phrase_and_the_doc(tmp_path: Path) -> None:
    cat = catalog(tmp_path)
    faq = FakeChunk("lab_docs/faq.md", "How do I join the Student AI Commons?")
    pick = cat.pick([], "How do I join the Discord?", [faq], "model")
    assert pick is not None and (pick.card.id, pick.via) == ("qr_discord", "backup")
    assert cat.pick([], "How do I join the Discord?", [], "model") is None  # no doc
    assert cat.pick([], "what's a transformer?", [faq], "model") is None  # no phrase
    assert cat.pick([], "discordant", [faq], "model") is None  # whole words only


def test_emergency_lines_bring_the_help_card(tmp_path: Path) -> None:
    cat = catalog(tmp_path)
    pick = cat.pick([], "help", [], "line:emergency")
    assert pick is not None and (pick.card.id, pick.via) == ("campus_help", "line")
    assert cat.pick([], "join the discord", [], "line:jailbreak") is None


# -- the check-in loop -------------------------------------------------------------------


def stage_with(tmp_path: Path) -> tuple[CardStage, CardCatalog]:
    return CardStage(random.Random(0)), catalog(tmp_path)


def kinds(events: list[dict]) -> list[str]:
    return [e["type"] if e["type"] != "card_state" else f"state:{e['state']}" for e in events]


def test_full_check_in_yes(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    card = cat.cards["qr_discord"]
    shown = st.show(card, 0.0, say_intro=True)
    assert kinds(shown) == ["show_card", "state:intro", "say"]
    assert shown[0]["data"]["placeholder_note"] == "Discord invite coming soon"
    assert st.step(5.9) == []
    ask = st.step(6.0)
    assert kinds(ask) == ["state:asking", "say"] and ask[1]["text"] == "Did you scan it?"
    assert st.listening
    yes = st.reply(None, "yes", 7.0)
    assert kinds(yes) == ["state:yes", "say"] and yes[1]["text"] == "Nice, see you in there!"
    assert st.current is None


def test_tag_shown_cards_dont_repeat_the_intro(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    assert kinds(st.show(cat.cards["qr_discord"], 0.0, say_intro=False)) == [
        "show_card",
        "state:intro",
    ]


def test_not_yet_waits_then_asks_once_more(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    st.show(cat.cards["qr_discord"], 0.0, say_intro=False)
    st.step(6.0)
    no = st.reply("qr_discord", "no", 8.0)
    assert kinds(no) == ["state:waiting", "say"] and no[0]["seconds"] == 10
    assert st.step(17.9) == []
    assert kinds(st.step(18.0)) == ["state:asking", "say"]  # asked once more
    again = st.reply(None, "no", 20.0)
    assert again[1]["text"] == "No rush, it'll stay up for a bit."
    assert st.step(59) == []  # no third ask
    assert kinds(st.step(60.0)) == ["state:timeout", "hide_card"]


def test_a_new_question_makes_it_mini_and_stops_asking(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    st.show(cat.cards["qr_discord"], 0.0, say_intro=False)
    assert kinds(st.new_question()) == ["card_mini"]
    assert not st.listening
    assert st.step(30) == []  # no ask while mini
    assert st.reply(None, "yes", 31) == []  # a tap on a mini card does nothing
    assert kinds(st.step(60.0)) == ["state:timeout", "hide_card"]


def test_info_cards_just_time_out(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    shown = st.show(cat.cards["compute_rules"], 0.0, say_intro=True)
    assert kinds(shown) == ["show_card", "say"]
    assert not st.listening
    assert kinds(st.step(40.0)) == ["state:timeout", "hide_card"]


def test_help_card_stays_until_reset(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    st.show(cat.cards["campus_help"], 0.0, say_intro=True)  # silent: no say_before
    assert st.step(10_000) == []
    assert kinds(st.clear()) == ["hide_card"] and st.current is None


def test_a_new_card_replaces_the_old_one(tmp_path: Path) -> None:
    st, cat = stage_with(tmp_path)
    st.show(cat.cards["compute_rules"], 0.0, say_intro=False)
    out = st.show(cat.cards["qr_discord"], 1.0, say_intro=False)
    assert kinds(out)[:2] == ["hide_card", "show_card"] and out[0]["id"] == "compute_rules"


def test_fun_card_sends_one_reviewed_fact(repo_root: Path) -> None:
    cat = load_cards(repo_root / "content" / "cards.yaml")
    st = CardStage(random.Random(3))
    d = st.show(cat.cards["ai_fact"], 0.0, say_intro=False)[0]["data"]
    assert d["fact"] in cat.cards["ai_fact"].facts


def test_yaml_boolean_id_gets_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CardsError, match="quote it"):
        load_cards(write(tmp_path, "cards:\n  - {id: off, type: fun, facts: [x]}\n"))

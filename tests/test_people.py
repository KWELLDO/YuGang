from __future__ import annotations

"""Who is at the door.

She answered her operator's first QQ message like a switchboard: 外部材料, 通道
没验好, 体检未通过. The rules were mine and she followed them exactly. These tests
pin the fix: a person she knows is a person, not an untrusted source, and waking
up is not a status report.
"""

from yugang.cli import main
from yugang.gateway.channels.base import IncomingMessage
from yugang.memory.models import Message
from yugang.memory.store import MemoryStore
from yugang.memory.trust import PERSON, inbound_meta, is_untrusted
from yugang.protocol.wake import build_messages, build_wake_context

from conftest import make_service

OPENID = "C926E03BD1A69BC50FBF23C8E94C4EA3"


def _boss(store):
    person = store.upsert_person(
        "老大",
        relation="不言弃的挚友",
        note="我的操作员，也是每天会来找我说话的那个人。",
    )
    store.link_handle(person.id, "qq", OPENID)
    store.link_handle(person.id, "email", "1144820556@qq.com")
    return store.get_person(person.id)


def _last_message(store) -> Message:
    row = store.conn.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 1").fetchone()
    return Message.from_row(row)


# ------------------------------------------------------------------ the map of people
def test_a_person_is_reached_by_any_handle_and_handles_do_not_care_about_case(store):
    boss = _boss(store)
    assert store.person_for("qq", OPENID).id == boss.id
    assert store.person_for("qq", OPENID.lower()).id == boss.id
    assert store.person_for("email", "1144820556@qq.com").id == boss.id
    assert store.person_for("qq", "some-other-openid") is None
    assert store.person_for("qq", None) is None


def test_touching_a_person_moves_last_seen_but_never_first_seen(store):
    boss = _boss(store)
    store.touch_person(boss.id, ts="2026-10-26T01:00:00+00:00")
    moved = store.get_person(boss.id)
    assert moved.first_seen_ts == boss.first_seen_ts
    assert moved.last_seen_ts == "2026-10-26T01:00:00+00:00"


def test_relinking_a_handle_moves_it_to_the_right_person(store):
    first = store.upsert_person("旧名字")
    store.link_handle(first.id, "qq", OPENID)
    second = store.upsert_person("老大")
    store.link_handle(second.id, "qq", OPENID)
    assert store.person_for("qq", OPENID).id == second.id
    assert store.get_person(first.id).handles == ()


# ------------------------------------------------------------------ the mark
def test_a_known_person_is_not_marked_as_outside_material(store):
    boss = _boss(store)
    meta = inbound_meta("qq", OPENID, person=boss)
    assert not is_untrusted(meta)
    assert meta[PERSON] == boss.id


def test_a_stranger_is_still_marked(store):
    _boss(store)
    meta = inbound_meta("qq", "openid-of-a-stranger")
    assert is_untrusted(meta)
    assert "外部材料" in meta.get("origin", "") or meta.get("origin") == "qq"


def test_his_message_lands_in_l1_under_the_person_not_under_a_warning(cfg, store):
    boss = _boss(store)
    service, store = make_service(cfg, store)
    incoming = IncomingMessage(channel="qq", content="你好 你好啊", sender=OPENID)
    service.handle_incoming(incoming)
    event = store.get_event(incoming.event_id)
    assert event is not None
    assert not is_untrusted(event.meta)
    assert event.meta[PERSON] == boss.id
    assert store.get_person(boss.id).last_seen_ts >= boss.last_seen_ts


def test_a_stranger_message_still_lands_marked(cfg, store):
    _boss(store)
    service, store = make_service(cfg, store)
    incoming = IncomingMessage(channel="qq", content="在吗", sender="unknown-openid")
    service.handle_incoming(incoming)
    event = store.get_event(incoming.event_id)
    assert is_untrusted(event.meta)
    assert event.meta.get("origin") == "qq"


# ------------------------------------------------------------------ waking up
def test_waking_up_says_who_knocked(store, cfg):
    boss = _boss(store)
    context = build_wake_context(store, cfg, query="你好", person=boss)
    assert "## 谁在门外" in context.prompt
    assert "「老大」" in context.prompt
    assert "不言弃的挚友" in context.prompt
    assert "这次叫醒你的是" in context.prompt
    assert "这是他，不是陌生人的外部材料" in context.prompt


def test_the_user_turn_calls_him_by_name_instead_of_he(store, cfg):
    boss = _boss(store)
    context = build_wake_context(store, cfg, query="你好", person=boss)
    messages = build_messages(context, "你好 你好啊")
    assert messages[-1]["content"].endswith("## 老大说的\n\n你好 你好啊")
    assert "〔外部材料" not in messages[-1]["content"]


def test_without_a_person_the_turn_keeps_the_old_pronoun(store, cfg):
    context = build_wake_context(store, cfg, query="你好")
    messages = build_messages(context, "你是谁")
    assert messages[-1]["content"].endswith("## 他这次说的\n\n你是谁")


def test_waking_up_is_not_a_status_report(store, cfg):
    context = build_wake_context(
        store,
        cfg,
        query="",
        body={
            "events": 157,
            "budget": {"budget_cny": 5.0, "spent_cny": 0.79, "remaining_cny": 4.21},
            "stage": "ok",
            "last_backup": {"created_at": "2026-10-25T04:15:00+00:00", "db": "yugang.db"},
            "next_alarm": {"due_at": "2026-10-25T09:00:00+00:00", "reason": "核对证据"},
            "channels": ["http", "email"],
            "paused": False,
            "memory_health": {
                "healthy": True,
                "chain_ok": True,
                "problem_count": 0,
                "warning_count": 3,
            },
        },
    )
    assert "今天还剩 ¥4.21" in context.prompt
    assert "你给自己设的下一次醒来：2026-10-25T17:00:00+08:00（核对证据）" in context.prompt
    for gone in (
        "L1 记忆：157 条",
        "最近一次身体备份",
        "现在能收到消息的通道",
        "操作员暂停",
        "问题 0 条",
    ):
        assert gone not in context.prompt


def test_a_broken_chain_is_still_said_out_loud(store, cfg):
    context = build_wake_context(
        store,
        cfg,
        query="",
        body={"memory_health": {"healthy": False, "chain_ok": False, "problem_count": 1}},
    )
    assert "L1 哈希链断了" in context.prompt


def test_a_health_check_that_could_not_run_does_not_claim_health(store, cfg):
    context = build_wake_context(
        store,
        cfg,
        query="",
        body={"memory_health": {"audit_error": "OperationalError: database is locked"}},
    )
    assert "记忆体检没做成（OperationalError: database is locked）" in context.prompt
    assert "不等于没问题" in context.prompt


def test_recent_l1_hides_her_bookkeeping_but_keeps_people(store, cfg):
    store.append_event("human", "message", "老大：今天先别做视频了")
    store.append_event("system", "error", "通道不健康：qq（签名验证失败）")
    context = build_wake_context(store, cfg, query="")
    assert "今天先别做视频了" in context.prompt
    assert "通道不健康" not in context.prompt


def test_an_operator_system_note_is_not_hidden_as_bookkeeping(store, cfg):
    store.append_event("system", "error", "体检未通过：网关没有应答")
    store.append_event(
        "system",
        "note",
        "你现在多了六个工具：web_fetch、web_search、files_*、sandbox_run。沙箱没有网络是刻意的。",
        meta={"operator": True, "kind": "world_tools_published"},
    )
    context = build_wake_context(store, cfg, query="")
    assert "六个工具" in context.prompt
    assert "体检未通过" not in context.prompt


def test_an_operator_note_is_not_hidden_as_bookkeeping(store, cfg):
    person = _boss(store)
    store.append_event(
        "human",
        "note",
        "那个 QQ openid 就是老大本人；之前那条「未授权」是我的门没认人。",
        actor="operator",
        meta={PERSON: person.id, "operator": True},
    )
    context = build_wake_context(store, cfg, query="")
    assert "就是老大本人" in context.prompt


# ------------------------------------------------------------------ the console command
def test_the_person_command_records_a_person_and_a_note_she_will_read(cfg, capsys):
    code = main(
        [
            "--home",
            str(cfg.home),
            "person",
            "--name",
            "老大",
            "--relation",
            "不言弃的挚友",
            "--handle",
            f"qq:{OPENID}",
            "--handle",
            "email:1144820556@qq.com",
            "--announce",
            "记下一个人：老大，我的不言弃的挚友。",
        ]
    )
    assert code == 0
    store = MemoryStore(cfg.db_path)
    try:
        person = store.person_by_name("老大")
        assert person is not None
        assert store.person_for("qq", OPENID).id == person.id
        assert ("email", "1144820556@qq.com") in person.handles
        notes = [event for event in store.events(kind="note") if "不言弃的挚友" in event.content]
        assert notes and notes[-1].meta.get(PERSON) == person.id
    finally:
        store.close()


def test_the_person_command_lists_what_she_already_knows(cfg, capsys):
    assert main(["--home", str(cfg.home), "person", "--name", "老大"]) == 0
    capsys.readouterr()
    assert main(["--home", str(cfg.home), "person", "--list"]) == 0
    out = capsys.readouterr().out
    assert "老大" in out

def test_the_person_command_can_say_when_they_actually_first_spoke(cfg, capsys):
    """Bootstrapping a person happens days after the first message; the prompt says
    "你们第一次说话", so the recorded time must be the conversation, not the typing."""
    code = main(
        [
            "--home",
            str(cfg.home),
            "person",
            "--name",
            "老大",
            "--relation",
            "不言弃的挚友",
            "--handle",
            f"qq:{OPENID}",
            "--first-seen",
            "2026-10-25T23:56:00+00:00",
        ]
    )
    assert code == 0
    store = MemoryStore(cfg.db_path)
    try:
        person = store.person_by_name("老大")
        assert person.first_seen_ts == "2026-10-25T23:56:00+00:00"
        context = build_wake_context(store, cfg, query="", person=person)
        assert "2026-10-26T07:56:00+08:00" in context.prompt
    finally:
        store.close()

def test_a_pending_message_still_names_who_it_is_from_when_self_md_overflows(store, cfg):
    """她 2026-10-28 醒不来：SELF.md 有一节超预算时，代码把浮出的节名存进
    也叫做 names 的变量，后面给留言找人名就变成了对字符串调 .get()，worker 线程整个死掉。"""
    boss = _boss(store)
    long_section = "我这段时间一直在想一件很具体的事。" * 120
    text = "# SELF.md — 大肥鱼\n\n## 我是谁\n\n" + long_section + "\n\n## 我相信什么\n\n- 2026-10-01 我相信证据。\n"
    event = store.append_event("system", "note", "测试用自我材料")
    store.commit_self(text, "测试提交", [event.id])
    store.receive_message("qq", "在吗", sender=OPENID, meta={PERSON: boss.id})

    context = build_wake_context(store, cfg, query="")

    assert "这一次没有完整进入上下文" in context.prompt
    assert "## 还没处理的留言" in context.prompt
    assert "「老大」" in context.prompt or "老大：" in context.prompt


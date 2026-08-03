"""到工統計名冊（data/attendance_roster.json）的 schema 守門測試。

名冊是 Worker 唯一的人員真實來源，且是人工手改的檔案 —— 一個打錯的
userId 會讓那個人每天都被列成「未回報」，而且不會有任何錯誤訊息。
在 CI 擋下來比事後從 LINE 訊息回推便宜得多。
"""
from pathlib import Path

from io_utils import load_json

ROSTER_PATH = Path(__file__).resolve().parent.parent / "data" / "attendance_roster.json"

_SENTINEL = object()


def _roster():
    data = load_json(ROSTER_PATH, _SENTINEL, label="attendance_roster.json")
    assert data is not _SENTINEL, f"{ROSTER_PATH} 不存在或無法解析"
    return data


def test_roster_has_members_list():
    roster = _roster()
    assert isinstance(roster, dict)
    assert isinstance(roster.get("members"), list)
    assert roster["members"], "名冊不可為空，否則統計清冊會是空的"


def test_every_member_has_a_nonempty_name():
    for i, member in enumerate(_roster()["members"]):
        assert isinstance(member, dict), f"members[{i}] 不是物件"
        name = member.get("name")
        assert isinstance(name, str) and name.strip(), f"members[{i}] 缺少 name"
        assert name == name.strip(), f"members[{i}] 的 name 前後有空白：{name!r}"


def test_names_are_unique():
    # 姓名重複會讓代報（葉維展-在部）指向不確定的人
    names = [m["name"] for m in _roster()["members"]]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"名冊姓名重複：{sorted(dupes)}"


def test_user_ids_are_well_formed_when_present():
    # LINE userId 是 'U' + 32 位十六進位字元；留 null 代表尚未建檔（合法）
    for member in _roster()["members"]:
        user_id = member.get("userId")
        if user_id is None:
            continue
        assert isinstance(user_id, str), f"{member['name']} 的 userId 型別不對"
        assert user_id.startswith("U"), f"{member['name']} 的 userId 應以 U 開頭"
        assert len(user_id) == 33, (
            f"{member['name']} 的 userId 長度應為 33（U + 32 字），實得 {len(user_id)}"
        )


def test_user_ids_are_unique():
    # 兩個人共用同一個 userId → 其中一人永遠是「未回報」
    ids = [m["userId"] for m in _roster()["members"] if m.get("userId")]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"名冊 userId 重複：{sorted(dupes)}"


def test_aliases_are_lists_of_strings_and_do_not_collide():
    roster = _roster()
    names = {m["name"] for m in roster["members"]}
    seen_aliases = {}
    for member in roster["members"]:
        aliases = member.get("aliases", [])
        assert isinstance(aliases, list), f"{member['name']} 的 aliases 不是陣列"
        for alias in aliases:
            assert isinstance(alias, str) and alias.strip(), (
                f"{member['name']} 的 aliases 含空值"
            )
            assert alias not in names, f"別名 {alias!r} 與其他人的姓名相同"
            assert alias not in seen_aliases, (
                f"別名 {alias!r} 同時屬於 {seen_aliases[alias]} 與 {member['name']}"
            )
            seen_aliases[alias] = member["name"]

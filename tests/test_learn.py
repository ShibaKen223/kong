import json

from kong.learn import BATCH, Learner, parse_terms
from kong.polish import prompt


def make(tmp_path):
    g = tmp_path / "glossary.txt"
    g.write_text("# 說明\n王小明\n", encoding="utf-8")
    return Learner(g, tmp_path / "learn.json"), g


def fake_ask(answer):
    calls = []

    def ask(system, message, timeout):
        calls.append(message)
        return answer

    ask.calls = calls
    return ask


def test_parse_terms_tolerates_chatter():
    assert parse_terms('好的：\n["GitHub", "Kong", 3, "a\\nb", ""]\n以上') == ["GitHub", "Kong"]
    assert parse_terms("沒有") == [] and parse_terms("[oops") == []


def test_learns_after_a_batch_and_feeds_the_glossary(tmp_path):
    lr, g = make(tmp_path)
    due = [lr.add("這個鉤我修了你再破一次", "這個 bug 我修了，你再 pull 一次。") for _ in range(BATCH)]
    assert due[-1] and not any(due[:-1])
    ask = fake_ask('["pull", "bug", "Kubernetes"]')
    new = lr.learn_pending(ask)
    # Kubernetes was never said: Claude's guess is not taken.
    assert new == ["pull", "bug"]
    assert "<raw>這個鉤我修了你再破一次</raw>" in ask.calls[0]
    assert lr.pending == []
    words = prompt.load_glossary(g)
    assert words[0] == "王小明" and "pull" in words and "bug" in words  # the user's own words first


def test_counts_grow_and_removed_words_never_return(tmp_path):
    lr, g = make(tmp_path)
    ask = fake_ask('["GitHub"]')
    for _ in range(2):
        lr.learn_text(["我們把程式推到 GitHub 上。"], ask)
    assert lr.terms() == [("GitHub", 2)]
    lr.remove("GitHub")
    lr.learn_text(["我們把程式推到 GitHub 上。"], ask)
    assert lr.terms() == [] and "GitHub" not in prompt.load_glossary(g)
    state = json.loads((tmp_path / "learn.json").read_text(encoding="utf-8"))
    assert state["blocked"] == ["GitHub"]
    assert Learner(g, tmp_path / "learn.json").blocked == {"GitHub"}  # survives a restart


def test_words_the_user_already_has_are_not_repeated(tmp_path):
    lr, g = make(tmp_path)
    lr.learn_text(["王小明明天會來。"], fake_ask('["王小明"]'))
    assert lr.terms() == []


def test_a_failed_pass_says_why(tmp_path):
    lr, _ = make(tmp_path)

    def broken(*_):
        raise RuntimeError("Claude Code 超過 90 秒沒有回應")

    assert lr.learn_text(["測試"], broken) == []
    assert "90 秒" in lr.last_note


def test_long_meetings_are_sampled_not_truncated(tmp_path):
    lr, _ = make(tmp_path)
    lines = [f"第{i}句提到 Project{i}" for i in range(3000)]
    ask = fake_ask("[]")
    lr.learn_text(lines, ask)
    msg = ask.calls[0]
    assert len(msg) < 20000 and "Project0" in msg and "Project2" in msg.split("Project0")[-1]

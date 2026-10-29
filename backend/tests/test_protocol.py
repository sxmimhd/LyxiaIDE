"""The agent speaks a plain-text THOUGHT / ACTION / ARGS format (small local models are poor at native tool calling)."""
import pytest

from app.agent.protocol import ProtocolError, parse_reply


def test_standard_reply():
    a = parse_reply('THOUGHT: look around\nACTION: grep\nARGS: {"pattern": "login"}')
    assert (a.thought, a.name, a.args) == ("look around", "grep", {"pattern": "login"})


def test_multiline_json_arguments():
    assert parse_reply('ACTION: grep\nARGS: {\n  "pattern": "a",\n  "max_results": 5\n}').args == {"pattern": "a", "max_results": 5}


def test_arguments_inside_a_code_fence():
    assert parse_reply('ACTION: grep\nARGS:\n```json\n{"pattern": "x"}\n```').args == {"pattern": "x"}


def test_lowercase_labels_bullets_and_backticked_name():
    assert parse_reply("- thought: hm\n- action: `read_file`\n- args: {\"path\": \"a.py\"}").name == "read_file"


@pytest.mark.parametrize("text", ["ACTION: git_status", "ACTION: git_status\nARGS: none"])
def test_missing_arguments_mean_no_arguments(text):
    assert parse_reply(text).args == {}


def test_only_the_first_action_counts():
    assert parse_reply('ACTION: grep\nARGS: {"pattern": "a"}\nACTION: finish\nARGS: {}').name == "grep"


def test_prose_before_the_format_is_ignored():
    assert parse_reply('Sure! Here is my plan.\nTHOUGHT: x\nACTION: finish\nARGS: {"summary": "ok"}').args == {"summary": "ok"}


def test_multiline_thought():
    assert parse_reply("THOUGHT: line one\nline two\nACTION: finish").thought == "line one\nline two"


@pytest.mark.parametrize("text", [
    "I will just fix it.",                      # no ACTION line
    "ACTION: grep\nARGS: {'pattern': 'x'}",     # single-quoted JSON
    "ACTION: grep\nARGS: [1, 2]",               # arguments are not an object
    'ACTION: grep\nARGS: {"pattern": ',         # truncated JSON
])
def test_unusable_replies_raise_a_helpful_error(text):
    with pytest.raises(ProtocolError):
        parse_reply(text)
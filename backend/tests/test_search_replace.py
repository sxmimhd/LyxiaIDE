"""The edit engine applies LLM-written SEARCH/REPLACE blocks. A bug here corrupts user code, so it is tested hard."""
from app.editing.search_replace import apply_blocks, parse_blocks


def block(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"


def run(content: str, reply: str, selection=None):
    blocks, explanation, incomplete = parse_blocks(reply)
    new, results = apply_blocks(content, blocks, selection)
    return new, [r.status for r in results], explanation, incomplete


SRC = "import os\n\ndef add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"


def test_exact_replace_touches_nothing_else():
    new, status, why, _ = run(SRC, "Adds hints.\n" + block("def add(a, b):\n    return a + b", "def add(a: int, b: int) -> int:\n    return a + b"))
    assert status == ["applied"] and why == "Adds hints."
    assert new == SRC.replace("def add(a, b):", "def add(a: int, b: int) -> int:")


def test_blocks_apply_in_order():
    new, status, *_ = run(SRC, block("def add(a, b):", "def plus(a, b):") + block("def plus(a, b):\n    return a + b", "def plus(a, b):\n    return b + a"))
    assert status == ["applied", "applied"] and "return b + a" in new


def test_model_that_drops_indentation_is_re_indented():
    cls = "class A:\n    def run(self):\n        x = 1\n        return x\n"
    new, status, *_ = run(cls, block("def run(self):\n    x = 1\n    return x", "def run(self):\n    x = 2\n    return x"))
    assert status == ["applied"] and "        x = 2" in new and new.count("def run") == 1


def test_tabs_versus_spaces_is_tolerated():
    new, status, *_ = run("def f():\n\treturn 1\n", block("def f():\n    return 1", "def f():\n    return 2"))
    assert status == ["applied"] and "return 2" in new


def test_ambiguous_match_is_refused_without_a_selection():
    dup = "a = 1\nprint(a)\n\nb = 2\nprint(a)\n"
    new, status, *_ = run(dup, block("print(a)", "print(a, 'x')"))
    assert status == ["ambiguous"] and new == dup


def test_selection_disambiguates():
    dup = "a = 1\nprint(a)\n\nb = 2\nprint(a)\n"
    new, status, *_ = run(dup, block("print(a)", "print(a, 'x')"), selection=(5, 5))
    assert status == ["applied"] and new == "a = 1\nprint(a)\n\nb = 2\nprint(a, 'x')\n"


def test_unknown_code_is_reported_and_file_untouched():
    new, status, *_ = run(SRC, block("def nothing():\n    pass", "x"))
    assert status == ["not_found"] and new == SRC


def test_delete_leaves_no_blank_line_hole():
    new, status, *_ = run(SRC, block("def sub(a, b):\n    return a - b", ""))
    assert status == ["applied"] and new == "import os\n\ndef add(a, b):\n    return a + b\n\n"


def test_insertion_by_repeating_an_anchor_line():
    new, _, *_ = run(SRC, block("import os", "import os\nimport sys"))
    assert new.startswith("import os\nimport sys\n\ndef add")


def test_crlf_files_stay_crlf():
    new, status, *_ = run("x = 1\r\ny = 2\r\nz = 3\r\n", block("y = 2", "y = 20"))
    assert status == ["applied"] and new == "x = 1\r\ny = 20\r\nz = 3\r\n"


def test_empty_file_can_be_filled():
    new, status, *_ = run("", "<<<<<<< SEARCH\n=======\nprint('hi')\n>>>>>>> REPLACE\n")
    assert status == ["applied"] and new == "print('hi')"


def test_empty_search_never_clobbers_a_non_empty_file():
    new, status, *_ = run(SRC, "<<<<<<< SEARCH\n=======\nprint('oops')\n>>>>>>> REPLACE\n")
    assert status == ["empty_search"] and new == SRC


def test_reply_cut_off_mid_block_is_detected():
    blocks, _, incomplete = parse_blocks("Fixing it.\n<<<<<<< SEARCH\nfoo\n=======\nbar\n")
    assert blocks == [] and incomplete


def test_blocks_wrapped_in_a_code_fence_still_parse():
    new, status, *_ = run("x = 1\n", "```python\n<<<<<<< SEARCH\nx = 1\n=======\nx = 5\n>>>>>>> REPLACE\n```\nDone.")
    assert status == ["applied"] and new == "x = 5\n"


def test_blank_lines_around_search_are_ignored():
    new, status, *_ = run(SRC, "<<<<<<< SEARCH\n\n\ndef add(a, b):\n    return a + b\n\n=======\ndef add(a, b):\n    return a * b\n>>>>>>> REPLACE\n")
    assert status == ["applied"] and "return a * b" in new


def test_marker_length_variations():
    new, status, *_ = run(SRC, "<<<<<<<<< SEARCH\nimport os\n=========\nimport os, sys\n>>>>>>>>> REPLACE\n")
    assert status == ["applied"] and new.startswith("import os, sys")


def test_prose_only_reply_has_no_blocks():
    assert parse_blocks("I cannot do that.")[0] == []


def test_multiline_search_picks_the_right_of_two_similar_spots():
    tricky = "d = {}\nif x:\n    d['a'] = 1\nif y:\n    d['a'] = 1\n"
    new, status, *_ = run(tricky, block("if y:\n    d['a'] = 1", "if y:\n    d['a'] = 2"))
    assert status == ["applied"] and new.endswith("if y:\n    d['a'] = 2\n") and "if x:\n    d['a'] = 1" in new
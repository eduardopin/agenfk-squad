#!/usr/bin/env python3
"""Every text-mode file read or write in tests/*.py says which encoding it uses.

Self-checking: the exit code is the verdict. Without `encoding=`, Python decodes with the platform's locale
(cp1252 on Windows), so a suite that passes on Linux breaks the day a file gains a non-ASCII character. The
check parses each tests/*.py and flags, as file:line, any `open(...)` in a text mode, `.read_text(...)` or
`.write_text(...)` that passes no encoding. Binary modes ("rb", "wb", ...) are exempt, and so is a call that
forwards `**kwargs` (the encoding may be in there). `subprocess.run(text=True)` is a different question.

    python3 tests/test_test_encoding.py
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def missing_encoding(tree):
    """The Call nodes in `tree` that do text-mode file I/O without an encoding."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn, args, kws = node.func, node.args, {k.arg: k for k in node.keywords}
        if None in kws or any(isinstance(a, ast.Starred) for a in args):
            continue  # **kwargs / *args: the encoding may travel in them
        if isinstance(fn, ast.Name) and fn.id == "open":
            mode = args[1] if len(args) > 1 else kws["mode"].value if "mode" in kws else None
            if mode is not None and _const_str(mode) is None:
                found.append(node)  # a computed mode cannot be shown binary
                continue
            if mode is not None and "b" in _const_str(mode):
                continue
            has_encoding = "encoding" in kws or len(args) > 3  # open(file, mode, buffering, encoding)
        elif isinstance(fn, ast.Attribute) and fn.attr == "read_text":
            has_encoding = "encoding" in kws or len(args) > 0
        elif isinstance(fn, ast.Attribute) and fn.attr == "write_text":
            has_encoding = "encoding" in kws or len(args) > 1
        else:
            continue
        if not has_encoding:
            found.append(node)
    return found


def violations(source, name):
    return [f"{name}:{n.lineno}: {ast.get_source_segment(source, n.func)}() without encoding="
            for n in missing_encoding(ast.parse(source, filename=name))]


def test_the_scan_flags_a_text_open_and_the_path_helpers():
    src = "open('a')\nopen('a', 'w')\nopen(p, mode='r')\np.read_text()\np.write_text('x')\n"
    assert len(violations(src, "x.py")) == 5, violations(src, "x.py")
    assert violations("open('a')", "x.py") == ["x.py:1: open() without encoding="]


def test_the_scan_leaves_alone_what_is_already_safe():
    ok = ("open('a', encoding='utf-8')\nopen('a', 'rb')\nopen('a', mode='wb')\nopen('a', 'r', -1, 'utf-8')\n"
          "p.read_text(encoding='utf-8')\np.read_text('utf-8')\np.write_text('x', encoding='utf-8')\n"
          "p.write_text('x', 'utf-8')\nopen(*a)\nopen('a', **kw)\nos.open('a', 0)\nz.read()\n")
    assert violations(ok, "x.py") == [], violations(ok, "x.py")


def test_every_tests_file_states_its_encoding():
    bad = []
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                bad += violations(fh.read(), f"tests/{name}")
    assert not bad, "text-mode file I/O without encoding= (breaks on Windows cp1252):\n  " + "\n  ".join(bad)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)

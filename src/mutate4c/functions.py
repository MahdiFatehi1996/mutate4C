"""Bind mutation sites to the C functions crap4c reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mutate4c.crapper_link import ensure_crapper
from mutate4c.model import Site
from mutate4c.sites import discover_raw


def _lines(source: str) -> list[str]:
    return source.splitlines()


def digest(source: str, start: int, end: int) -> str:
    chunk = "\n".join(line.rstrip() for line in _lines(source)[start - 1 : end])
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def _declarator_name(node, data: bytes) -> str | None:
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None:
        return None
    core = declarator.child_by_field_name("declarator")
    if core is None:
        return None
    stack = [core]
    while stack:
        item = stack.pop()
        if item.type == "identifier":
            return data[item.start_byte : item.end_byte].decode("utf-8")
        stack.extend(reversed(item.children))
    return None


def _has_static_storage(node) -> bool:
    """A `static` storage class of this function, not of a parameter array."""

    for child in node.children:
        if child.type != "storage_class_specifier":
            continue
        if any(token.type == "static" for token in child.children):
            return True
    return False


def _static_definitions(source: str) -> dict[tuple[int, int], bool]:
    """Each function definition's byte span and whether it declares `static`."""

    crapper = ensure_crapper()
    _data, tree = crapper.languages.treesitter.parse(source, "c")
    found: dict[tuple[int, int], bool] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            found[(node.start_byte, node.end_byte)] = _has_static_storage(node)
        stack.extend(reversed(node.children))
    return found


def _privacy(source: str, functions) -> dict[int, bool]:
    if not functions:
        return {}
    static = _static_definitions(source)
    return {
        id(fn): static.get((getattr(fn, "start_byte", -1), getattr(fn, "end_byte", -1)), False)
        for fn in functions
    }


def _line_end(node) -> int:
    row, col = node.end_point
    if col == 0:
        return max(node.start_point[0] + 1, row)
    return row + 1


def _find_definition(root, data: bytes, name: str, start: int, end: int):
    """The definition that begins on `start` line.

    An exact end-line match wins. Callers that only know the declaration's
    first line still find the one definition with that start and name.
    """

    fallback = None
    found = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "function_definition" and node.start_point[0] + 1 == start:
            declared = _declarator_name(node, data)
            if name is None or declared is None or declared == name:
                if _line_end(node) == end:
                    return node
                fallback = node
                found += 1
        stack.extend(reversed(node.children))
    return fallback if found == 1 else None


def is_private(language: str, name: str, source: str, start: int, end: int) -> bool:
    """True when the C function definition declares `static` storage.

    The declaration's AST decides, so a `static` in a parameter array
    declarator (`int f(int a[static 10])`) is not the function's storage
    class.
    """

    if language != "c":
        return False
    crapper = ensure_crapper()
    data, tree = crapper.languages.treesitter.parse(source, "c")
    node = _find_definition(tree.root_node, data, name, start, end)
    return node is not None and _has_static_storage(node)


def form_id(name: str, private: bool) -> str:
    """The id uml-viewer splits into an operation name.

    `defn/place` is public. `defn-/hide` is private. The viewer checks the
    private prefix first.
    """

    prefix = "defn-" if private else "defn"
    return f"{prefix}/{name}"


def mutation_id(
    file: str, namespace: str, form: str, start: int, end: int, original: str, mutant: str
) -> str:
    return json.dumps(
        [file, namespace, form, start, end, original, mutant], separators=(",", ":")
    )


def _mutation_field(mutation: str, index: int) -> str | None:
    try:
        value = json.loads(mutation)
    except json.JSONDecodeError:
        return None
    if isinstance(value, list) and len(value) > index and isinstance(value[index], str):
        return value[index]
    return None


def mutation_file(mutation: str) -> str | None:
    return _mutation_field(mutation, 0)


def mutation_namespace(mutation: str) -> str | None:
    return _mutation_field(mutation, 1)


def project_functions(source: str, path: Path, root: Path):
    crapper = ensure_crapper()
    language = crapper.discover.language_of(path)
    if language is None:
        return []
    return crapper.languages.functions_in_file(
        language,
        source,
        path.resolve().as_posix(),
        root.resolve().as_posix(),
    )


def _contains(fn, line: int, byte: int | None) -> bool:
    if fn.start_line > line or fn.end_line < line:
        return False
    start = getattr(fn, "start_byte", -1)
    end = getattr(fn, "end_byte", -1)
    if byte is None or start < 0 or end < 0:
        return True
    return start <= byte < end


def _tightness(fn) -> int:
    start = getattr(fn, "start_byte", -1)
    end = getattr(fn, "end_byte", -1)
    if start >= 0 and end >= start:
        return end - start
    return (fn.end_line - fn.start_line) * 1_000_000


def _owner(functions, line: int, byte: int | None = None):
    matches = [fn for fn in functions if _contains(fn, line, byte)]
    if not matches:
        return None
    return min(matches, key=lambda fn: (_tightness(fn), fn.start_line))


def sites_in_file(source: str, path: Path, root: Path, file_key: str) -> list[Site]:
    crapper = ensure_crapper()
    language = crapper.discover.language_of(path)
    if language is None:
        return []
    functions = project_functions(source, path, root)
    privacy = _privacy(source, functions)
    found: list[Site] = []
    for raw in discover_raw(source, path.as_posix()):
        owner = _owner(functions, raw.line, raw.start)
        if owner is None:
            continue
        private = privacy[id(owner)]
        form = form_id(owner.name, private)
        found.append(
            Site(
                file=file_key,
                namespace=owner.namespace,
                form_id=form,
                name=owner.name,
                line=raw.line,
                start=raw.start,
                end=raw.end,
                original=raw.original,
                mutant=raw.mutant,
                category=raw.category,
                mutation_id=mutation_id(
                    file_key, owner.namespace, form, raw.start, raw.end, raw.original, raw.mutant
                ),
                anchor_line=raw.anchor_line,
            )
        )
    return found


def form_digests(source: str, path: Path, root: Path) -> dict[tuple[str, str], str]:
    """Map `(namespace, form id)` to a hash of the function text."""

    crapper = ensure_crapper()
    language = crapper.discover.language_of(path)
    if language is None:
        return {}
    grouped: dict[tuple[str, str], list] = {}
    functions = project_functions(source, path, root)
    privacy = _privacy(source, functions)
    for fn in functions:
        private = privacy[id(fn)]
        key = (fn.namespace, form_id(fn.name, private))
        grouped.setdefault(key, []).append(fn)
    digests = {}
    for key, fns in grouped.items():
        ordered = sorted(fns, key=lambda fn: (fn.start_line, fn.end_line))
        parts = [digest(source, fn.start_line, fn.end_line) for fn in ordered]
        digests[key] = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()
    return digests


def form_spans(source: str, path: Path, root: Path) -> dict[tuple[str, str], tuple[int, int, str, bool]]:
    """`(namespace, form id)` -> start line, end line, name, private."""

    spans = {}
    functions = project_functions(source, path, root)
    privacy = _privacy(source, functions)
    for fn in functions:
        private = privacy[id(fn)]
        key = (fn.namespace, form_id(fn.name, private))
        if key not in spans:
            spans[key] = (fn.start_line, fn.end_line, fn.name, private)
            continue
        start, end, name, private_flag = spans[key]
        spans[key] = (min(start, fn.start_line), max(end, fn.end_line), name, private_flag)
    return spans

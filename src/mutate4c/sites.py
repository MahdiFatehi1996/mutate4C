"""Find mutation sites in C source.

The operators are the Java decisions spelled in C: `+`/`-`, `*`/`/`,
`>`/`>=`, `<`/`<=`, `==`/`!=`, `&&`/`||`, `0`/`1`, `true`/`false`,
removal of unary `!`, and removal of unary `-`. Bitwise and shift operators
are not mutated. A `*` or `&` that is a pointer operation belongs to a
`pointer_expression`, not a `unary_expression`, so it is never a site.
"""

from __future__ import annotations

from mutate4c.crapper_link import ensure_crapper

_BINARY = {
    "+": "-",
    "-": "+",
    "*": "/",
    "/": "*",
    ">": ">=",
    ">=": ">",
    "<": "<=",
    "<=": "<",
    "==": "!=",
    "!=": "==",
    "&&": "||",
    "||": "&&",
}

_BINARY_PARENTS = {"binary_expression"}
_UNARY_PARENTS = {"unary_expression"}
_BOOLEANS = {"true": "false", "false": "true"}
_BOOLEAN_NODES = {"true", "false"}
_NUMBERS = {"number_literal"}

# A literal operator spelling in one of these is text, not code.
_SKIP_SUBTREES = {"comment", "string_literal", "char_literal"}

# A preprocessor condition is an expression the compiler evaluates, not code
# the tests execute. Its body statements are still mutation sites.
_PREPROC_NODES = {
    "preproc_if",
    "preproc_elif",
    "preproc_ifdef",
    "preproc_else",
    "preproc_elifdef",
}
_PREPROC_SKIP_FIELDS = ("condition", "name")


class RawSite:
    def __init__(
        self, line: int, start: int, end: int, original: str, mutant: str, category: str, anchor_line: int = 0
    ):
        self.line = line
        # The line its statement starts on. LCOV marks only that line of a multi-line statement.
        self.anchor_line = anchor_line or line
        self.start = start
        self.end = end
        self.original = original
        self.mutant = mutant
        self.category = category


def _grammar(language: str, path: str) -> str:
    """The tree-sitter grammar for the only language this tool mutates."""

    if language != "c":
        raise ValueError(f"mutate4c only mutates C, not {language!r} ({path})")
    return "c"


def _text(data: bytes, node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def _category(token: str) -> str:
    if token in {"+", "-", "*", "/"}:
        return "arithmetic"
    if token in {">", ">=", "<", "<="}:
        return "comparison"
    if token in {"==", "!="}:
        return "equality"
    if token in {"&&", "||"}:
        return "logical"
    return "arithmetic"


def _append(
    found: list[RawSite],
    line: int,
    start: int,
    end: int,
    original: str,
    mutant: str,
    category: str,
    anchor_line: int,
) -> None:
    if mutant == original:
        return
    found.append(
        RawSite(
            line=line,
            start=start,
            end=end,
            original=original,
            mutant=mutant,
            category=category,
            anchor_line=anchor_line,
        )
    )


def _add(found: list[RawSite], node, original: str, mutant: str, category: str) -> None:
    _append(
        found,
        node.start_point[0] + 1,
        node.start_byte,
        node.end_byte,
        original,
        mutant,
        category,
        _statement_line(node),
    )


def _is_statement(node_type: str) -> bool:
    """A node whose first line is the one LCOV marks for a multi-line body.

    C calls a local variable declaration plain `declaration`, and a
    `compound_statement` is the block that contains statements rather than a
    statement of its own. Anchoring to either would attribute a site to the
    function's opening brace.
    """

    if node_type == "declaration":
        return True
    if node_type == "compound_statement":
        return False
    return node_type.endswith("_statement") or node_type.endswith("_declaration")


def _statement_line(node) -> int:
    current = node.parent
    while current is not None:
        if _is_statement(current.type):
            return current.start_point[0] + 1
        current = current.parent
    return node.start_point[0] + 1


def _flip01(text: str) -> str:
    return "1" if text == "0" else "0"


def _parent_type(node) -> str:
    if node.parent is None:
        return ""
    return node.parent.type


def _visit_binary(node, token: str, parent: str, found: list[RawSite]) -> None:
    if parent not in _BINARY_PARENTS:
        return
    mutant = _BINARY.get(token)
    if mutant is not None:
        _add(found, node, token, mutant, _category(token))


def _visit_not(node, token: str, parent: str, found: list[RawSite]) -> None:
    if token == "!" and parent in _UNARY_PARENTS:
        _add(found, node, token, "", "unary")


def _visit_negation(node, token: str, parent: str, found: list[RawSite]) -> None:
    if token == "-" and parent in _UNARY_PARENTS:
        _add(found, node, token, "", "unary")


def _visit_boolean(node, token: str, found: list[RawSite]) -> None:
    if node.type in _BOOLEAN_NODES and token in _BOOLEANS:
        _add(found, node, token, _BOOLEANS[token], "boolean")


def _visit_constant(node, token: str, found: list[RawSite]) -> None:
    if node.type in _NUMBERS and token in {"0", "1"}:
        _add(found, node, token, _flip01(token), "constant")


def _visit_signed_constant(node, token: str, found: list[RawSite]) -> None:
    """Split a folded negative literal the way the Java AST splits it.

    tree-sitter-c folds `-1` into one `number_literal`. Removing the sign is
    one unary mutation; the remaining `0` or `1`, when it is exactly that
    token, is a second constant mutation. `-1.0`, `-0x1`, and `-10` keep only
    the unary removal, because their value is not the literal `0` or `1`.
    """

    if node.type not in _NUMBERS or not token.startswith("-"):
        return
    anchor = _statement_line(node)
    _append(
        found,
        node.start_point[0] + 1,
        node.start_byte,
        node.start_byte + 1,
        "-",
        "",
        "unary",
        anchor,
    )
    digits = token[1:]
    if digits in {"0", "1"}:
        _append(
            found,
            node.start_point[0] + 1,
            node.start_byte + 1,
            node.end_byte,
            digits,
            _flip01(digits),
            "constant",
            anchor,
        )


def _visit_leaf(node, data: bytes, found: list[RawSite]) -> None:
    # A literal sits inside the same expression as an operator, so these
    # checks are independent. `0` is a child of `a > 0`, not an operator.
    token = _text(data, node)
    parent = _parent_type(node)
    _visit_binary(node, token, parent, found)
    _visit_not(node, token, parent, found)
    _visit_negation(node, token, parent, found)
    _visit_boolean(node, token, found)
    _visit_constant(node, token, found)
    _visit_signed_constant(node, token, found)


def _visit(node, data: bytes, found: list[RawSite]) -> None:
    # An explicit stack: a 1,200-term expression must not raise RecursionError.
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _SKIP_SUBTREES:
            continue
        if current.type in _PREPROC_NODES:
            skipped = set()
            for field in _PREPROC_SKIP_FIELDS:
                child = current.child_by_field_name(field)
                if child is not None:
                    skipped.add(child.id)
            stack.extend(
                child for child in reversed(current.children) if child.id not in skipped
            )
            continue
        if current.child_count == 0:
            _visit_leaf(current, data, found)
            continue
        stack.extend(reversed(current.children))


def discover_raw(source: str, path: str) -> list[RawSite]:
    """Mutation sites in source order. Offsets are UTF-8 byte offsets."""

    crapper = ensure_crapper()
    data = source.encode("utf-8")
    grammar = _grammar("c", path)
    _data, tree = crapper.languages.treesitter.parse(source, grammar)
    found: list[RawSite] = []
    _visit(tree.root_node, data, found)
    found.sort(key=lambda site: (site.line, site.start, site.end))
    return found


def apply_site(source: str, start: int, end: int, mutant: str) -> str:
    data = source.encode("utf-8")
    updated = data[:start] + mutant.encode("utf-8") + data[end:]
    return updated.decode("utf-8")

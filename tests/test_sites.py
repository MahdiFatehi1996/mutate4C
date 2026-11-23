from pathlib import Path

import pytest

from mutate4c.functions import form_id, is_private, project_functions, sites_in_file
from mutate4c.sites import apply_site, discover_raw


def _sites(tmp_path: Path, relative: str, source: str):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return sites_in_file(source, path, tmp_path, relative)


def _pairs(sites):
    return {(site.original, site.mutant) for site in sites}


def test_c_binary_operators_follow_the_java_decisions(tmp_path):
    source = """int calc(int a, int b) {
    int r = a + b - a * b / 2;
    if (a > b && b >= a || a < b && b <= a) {
        r = (a == b) != (a != b);
    }
    return r;
}
"""
    pairs = _pairs(_sites(tmp_path, "src/calc.c", source))
    for pair in (
        ("+", "-"),
        ("-", "+"),
        ("*", "/"),
        ("/", "*"),
        (">", ">="),
        (">=", ">"),
        ("<", "<="),
        ("<=", "<"),
        ("==", "!="),
        ("!=", "=="),
        ("&&", "||"),
        ("||", "&&"),
    ):
        assert pair in pairs


def test_c_bitwise_and_shift_operators_are_left_alone(tmp_path):
    source = """int bits(int a, int b) {
    int c = a & b;
    int d = a | b;
    int e = a ^ b;
    int f = ~a;
    return (a << 1) + (b >> 1) + c + d + e + f;
}
"""
    pairs = _pairs(_sites(tmp_path, "src/bits.c", source))
    assert pairs == {
        ("1", "0"),
        ("1", "0"),
        ("+", "-"),
        ("+", "-"),
        ("+", "-"),
    }


def test_c_pointer_operators_are_not_unary_sites(tmp_path):
    source = """int deref(int *p) {
    *p = 1;
    int *q = &p;
    return -(*p) + *q;
}
"""
    sites = _sites(tmp_path, "src/deref.c", source)
    pairs = _pairs(sites)
    assert ("1", "0") in pairs
    assert ("-", "") in pairs
    assert ("+", "-") in pairs
    assert ("*", "") not in pairs
    assert ("&", "") not in pairs
    # The negated dereference is the only `-`; the second spelling is `+`.
    assert [site.original for site in sites].count("-") == 1


def test_c_unary_and_binary_minus_are_distinguished(tmp_path):
    sites = _sites(tmp_path, "src/minus.c", "int f(int x) { return -x - x; }\n")
    described = [(site.original, site.mutant) for site in sites]
    assert described.count(("-", "")) == 1
    assert described.count(("-", "+")) == 1


def test_c_strings_char_literals_and_comments_are_not_sites(tmp_path):
    source = """int f(void) {
    /* a > b && c == d, x + 1 */
    // x + 1
    const char *s = "x >= 0 && y != 1";
    char arrow = '>';
    char zero = '0';
    return 2;
}
"""
    assert _sites(tmp_path, "src/text.c", source) == []


def test_c_only_the_exact_tokens_zero_and_one_are_constants(tmp_path):
    source = """int f(void) {
    int a = 0;
    int b = 1;
    int c = 10;
    int d = 0x0;
    int e = 01;
    double g = 1.0;
    return 2;
}
"""
    sites = _sites(tmp_path, "src/numbers.c", source)
    assert [(site.line, site.original, site.mutant) for site in sites] == [
        (2, "0", "1"),
        (3, "1", "0"),
    ]


def test_c_a_folded_negative_literal_splits_into_unary_and_constant_sites(tmp_path):
    source = "int f(void) { return -1; }\n"
    sites = _sites(tmp_path, "src/negative.c", source)
    assert [(site.original, site.mutant, site.category) for site in sites] == [
        ("-", "", "unary"),
        ("1", "0", "constant"),
    ]
    data = source.encode("utf-8")
    assert data[sites[0].start : sites[0].end] == b"-"
    assert data[sites[1].start : sites[1].end] == b"1"
    assert sites[1].start == sites[0].start + 1
    mutated = apply_site(source, sites[0].start, sites[0].end, sites[0].mutant)
    assert mutated == "int f(void) { return 1; }\n"
    mutated = apply_site(source, sites[1].start, sites[1].end, sites[1].mutant)
    assert mutated == "int f(void) { return -0; }\n"


def test_c_minus_zero_splits_the_same_way(tmp_path):
    source = "int f(void) { return -0; }\n"
    sites = _sites(tmp_path, "src/negative_zero.c", source)
    assert [(site.original, site.mutant, site.category) for site in sites] == [
        ("-", "", "unary"),
        ("0", "1", "constant"),
    ]


@pytest.mark.parametrize("literal", ["-10", "-1.0", "-0x1", "-1u"])
def test_c_other_negative_literals_only_remove_the_sign(tmp_path, literal):
    source = f"int f(void) {{ return {literal}; }}\n"
    sites = _sites(tmp_path, "src/negative.c", source)
    assert [(site.original, site.mutant, site.category) for site in sites] == [
        ("-", "", "unary"),
    ]


def test_c_binary_minus_and_a_folded_negative_literal_are_distinct(tmp_path):
    source = "int f(void) { return 1 - -1; }\n"
    sites = _sites(tmp_path, "src/mixed.c", source)
    assert [(site.original, site.mutant) for site in sites] == [
        ("1", "0"),
        ("-", "+"),
        ("-", ""),
        ("1", "0"),
    ]


def test_c_nested_calls_and_initializers_are_walked(tmp_path):
    source = """int outer(int x);
int inner(int x);

int f(int x) {
    int values[] = { 0, 1, 2 };
    return outer(inner(x + 1) - 0);
}
"""
    sites = _sites(tmp_path, "src/calls.c", source)
    described = sorted((site.line, site.original, site.mutant) for site in sites)
    assert described == [
        (5, "0", "1"),
        (5, "1", "0"),
        (6, "+", "-"),
        (6, "-", "+"),
        (6, "0", "1"),
        (6, "1", "0"),
    ]
    anchors = {(site.line, site.original, site.anchor_line) for site in sites}
    assert (5, "0", 5) in anchors
    assert (5, "1", 5) in anchors
    assert (6, "+", 6) in anchors
    assert (6, "0", 6) in anchors


def test_c_sites_carry_exact_byte_offsets_and_sort_by_position(tmp_path):
    source = """int f(int a, int b) {
    int c = a > 0;
    return a && b;
}
"""
    sites = _sites(tmp_path, "src/offsets.c", source)
    assert sites
    positions = [(site.start, site.end) for site in sites]
    assert positions == sorted(positions)
    data = source.encode("utf-8")
    for site in sites:
        assert data[site.start : site.end].decode("utf-8") == site.original
        mutated = apply_site(source, site.start, site.end, site.mutant).encode("utf-8")
        expected = data[: site.start] + site.mutant.encode("utf-8") + data[site.end :]
        assert mutated == expected


def test_c_preprocessor_macros_and_top_level_conditions_are_not_sites(tmp_path):
    source = """#define ADD(a, b) ((a) + (b))
#define FLAG (1 > 0)
#if LEVEL >= 2
int helper(void) { return 1; }
#endif
"""
    sites = _sites(tmp_path, "src/macros.c", source)
    assert [(site.line, site.original, site.mutant) for site in sites] == [(4, "1", "0")]


def test_c_preprocessor_conditions_are_not_sites_inside_a_function(tmp_path):
    source = """int f(void) {
#if 0
    return 0;
#endif
#if LEVEL >= 2
    return 1;
#elif LEVEL == 1
    return 2;
#else
    return -1;
#endif
#if !defined(F)
    return 3;
#endif
#ifdef F
    return 0;
#endif
#ifndef F
    return 1;
#endif
}
"""
    sites = _sites(tmp_path, "src/conditions.c", source)
    assert [(site.line, site.original, site.mutant) for site in sites] == [
        (3, "0", "1"),
        (6, "1", "0"),
        (10, "-", ""),
        (10, "1", "0"),
        (16, "0", "1"),
        (19, "1", "0"),
    ]


def test_c_static_function_uses_the_private_form_id(tmp_path):
    source = """static int hide(void) { return 0; }
int show(void) { return 1; }
"""
    sites = _sites(tmp_path, "src/private.c", source)
    assert {site.form_id for site in sites} == {"defn-/hide", "defn/show"}
    assert form_id("hide", True) == "defn-/hide"
    assert is_private("c", "hide", source, 1, 1) is True
    assert is_private("c", "show", source, 2, 2) is False
    assert is_private("cobol", "show", source, 1, 1) is False


def test_c_a_static_local_does_not_make_a_public_function_private(tmp_path):
    source = """int show(void) {
    static int n = 0;
    return n + 1;
}

static int hide(void) {
    return 0;
}
"""
    sites = _sites(tmp_path, "src/static_local.c", source)
    assert {site.form_id for site in sites} == {"defn/show", "defn-/hide"}
    assert is_private("c", "show", source, 1, 4) is False
    assert is_private("c", "hide", source, 6, 8) is True


def test_c_a_multiline_static_function_stays_private(tmp_path):
    source = """static
int
hide(void)
{
    static int n = 0;
    return n;
}
"""
    sites = _sites(tmp_path, "src/static_multiline.c", source)
    assert {site.form_id for site in sites} == {"defn-/hide"}
    assert is_private("c", "hide", source, 1, 8) is True


def test_c_a_brace_in_a_header_comment_does_not_hide_the_storage_class(tmp_path):
    hidden = "/* { */ static int hide(void) { return 0; }\n"
    assert is_private("c", "hide", hidden, 1, 1) is True
    public = "/* { */ int show(void) { return 0; }\n"
    assert is_private("c", "show", public, 1, 1) is False


def test_c_a_static_parameter_array_does_not_make_the_function_private(tmp_path):
    source = """int f(int a[static 10]) {
    return a[0] + 1;
}
static int hide(int a[static 10]) {
    return a[0] + 0;
}
"""
    sites = _sites(tmp_path, "src/static_parameter.c", source)
    assert {site.form_id for site in sites} == {"defn/f", "defn-/hide"}
    assert is_private("c", "f", source, 1, 3) is False
    assert is_private("c", "hide", source, 4, 6) is True


def test_c_a_storage_class_after_the_return_type_is_private(tmp_path):
    source = "int static f(void) { return 0; }\n"
    assert is_private("c", "f", source, 1, 1) is True
    assert {site.form_id for site in _sites(tmp_path, "src/static_after.c", source)} == {"defn-/f"}


def test_c_namespace_is_the_dotted_source_path_without_src(tmp_path):
    source = "int f(void) { return 0; }\n"
    sites = _sites(tmp_path, "src/app/ui/page.c", source)
    assert {site.namespace for site in sites} == {"app.ui.page"}
    functions = project_functions(source, tmp_path / "src/app/ui/page.c", tmp_path)
    assert {fn.namespace for fn in functions} == {"app.ui.page"}


def test_c_multiline_statement_remembers_its_first_line(tmp_path):
    source = """int f(int a, int b) {
    return (a
            + b);
}

int g(int a, int b) {
    int sum = a
            + b;
    return sum;
}
"""
    anchors = {(site.line, site.anchor_line, site.original) for site in _sites(tmp_path, "src/lines.c", source)}
    assert (3, 2, "+") in anchors
    assert (8, 7, "+") in anchors


def test_c_casts_do_not_mutate_pointer_declarators(tmp_path):
    source = """int f(void *p, unsigned x) {
    int *q = (int *) p;
    unsigned u = (unsigned) -x;
    return *q + (int) u;
}
"""
    sites = _sites(tmp_path, "src/casts.c", source)
    pairs = _pairs(sites)
    assert ("-", "") in pairs
    assert ("+", "-") in pairs
    assert ("*", "") not in pairs


def test_c_offsets_are_utf8_bytes_not_characters(tmp_path):
    source = "/* café */\nint f(int a) { return a > 0; }\n"
    sites = _sites(tmp_path, "src/utf8.c", source)
    site = next(item for item in sites if item.original == ">")
    data = source.encode("utf-8")
    assert data[site.start : site.end] == b">"
    assert site.start == len(source[: source.index(">")].encode("utf-8"))
    assert site.start != source.index(">")


def test_a_deep_c_expression_is_walked():
    expr = " + ".join(["1"] * 1200)
    source = f"int f(void) {{ return {expr}; }}\n"
    assert discover_raw(source, "src/deep.c")

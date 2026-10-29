"""check_standards.py - code-standards rules for the Python files of a task.

Usage: python3 -I check_standards.py <file.py> [<file.py> ...]

Standard library only, Python 3.9 syntax. Prints one finding per line as
`file:line rule message` and exits 1 when there is any finding, 0 when clean.

Rules:
  one-model-per-file  a file in a models/ directory defines more than one Odoo
                      model class (a class with _name or _inherit)
  docstring           a function or method has no docstring
  nesting             control flow nested other than for-in-for or if-in-for, or
                      nested deeper than two levels (while counts as for; elif
                      is not nesting; try and with are transparent)
  max-loc             a function has more than 25 logical lines, docstring excluded
  complexity          a function has cyclomatic complexity above 5
"""
import ast
import os
import sys

MAX_LOC = 25
MAX_COMPLEXITY = 5

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
CONTROL = (ast.If,) + LOOPS


def docstring_node(func):
    """Return the docstring expression statement of func, or None."""
    first = func.body[0] if func.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return first
    return None


def own_nodes(func):
    """Yield every node of func, without entering nested functions or classes."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, FUNCS + (ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def logical_lines(func):
    """Count the statements of func, docstring excluded."""
    doc = docstring_node(func)
    count = 0
    for node in own_nodes(func):
        if isinstance(node, ast.stmt) and node is not doc:
            count += 1
    return count


def complexity(func):
    """Cyclomatic complexity: 1 plus the decision points of func."""
    total = 1
    for node in own_nodes(func):
        if isinstance(node, (ast.If, ast.IfExp, ast.ExceptHandler) + LOOPS):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += 1 + len(node.ifs)
    return total


def nesting_findings(func):
    """Yield (line, message) for each bad nesting of if and loops in func."""
    def walk(nodes, stack):
        for node in nodes:
            if isinstance(node, FUNCS + (ast.ClassDef,)):
                continue
            if isinstance(node, CONTROL):
                for item in check(node, stack):
                    yield item
                for item in walk_control(node, stack):
                    yield item
            else:
                for item in walk(ast.iter_child_nodes(node), stack):
                    yield item

    def check(node, stack):
        if not stack:
            return
        kind = type(node).__name__.lower()
        if len(stack) >= 2:
            yield node.lineno, "%s nested three levels deep" % kind
        elif not isinstance(stack[-1], LOOPS):
            yield node.lineno, "%s nested inside an if" % kind

    def walk_control(node, stack):
        inner = stack + [node]
        for item in walk(node.body, inner):
            yield item
        # An if in the orelse of an if is an elif: same level, not nesting.
        if isinstance(node, ast.If):
            for item in walk(node.orelse, stack):
                yield item
        else:
            for item in walk(node.orelse, inner):
                yield item

    return walk(func.body, [])


def model_classes(tree):
    """Return the class nodes of tree that define _name or _inherit."""
    found = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            targets = []
            if isinstance(stmt, ast.Assign):
                targets = stmt.targets
            elif isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target]
            if any(isinstance(t, ast.Name) and t.id in ("_name", "_inherit")
                   for t in targets):
                found.append(node)
                break
    return found


def in_models_dir(path):
    """True when path has a models directory and is not an __init__.py."""
    parts = os.path.normpath(path).split(os.sep)
    return "models" in parts[:-1] and parts[-1] != "__init__.py"


def function_findings(func):
    """Yield (line, rule, message) for the per-function rules."""
    if docstring_node(func) is None:
        yield func.lineno, "docstring", "%s has no docstring" % func.name
    loc = logical_lines(func)
    if loc > MAX_LOC:
        yield (func.lineno, "max-loc",
               "%s has %d logical lines, limit %d" % (func.name, loc, MAX_LOC))
    cc = complexity(func)
    if cc > MAX_COMPLEXITY:
        yield (func.lineno, "complexity",
               "%s has complexity %d, limit %d" % (func.name, cc, MAX_COMPLEXITY))
    for line, message in nesting_findings(func):
        yield line, "nesting", "%s: %s" % (func.name, message)


def check_file(path):
    """Return the sorted list of (line, rule, message) findings of one file."""
    with open(path, "rb") as handle:
        source = handle.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [(exc.lineno or 1, "syntax", "cannot parse: %s" % exc.msg)]
    findings = []
    if in_models_dir(path):
        models = model_classes(tree)
        for extra in models[1:]:
            findings.append((extra.lineno, "one-model-per-file",
                             "class %s is model %d in this file" % (
                                 extra.name, models.index(extra) + 1)))
    for node in ast.walk(tree):
        if isinstance(node, FUNCS):
            findings.extend(function_findings(node))
    return sorted(findings)


def main(argv):
    """Check every file in argv and print the findings."""
    bad = 0
    for path in argv:
        for line, rule, message in check_file(path):
            print("%s:%d %s %s" % (path, line, rule, message))
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Unit tests for the CI mutation kill-rate gate (``tools/mutation_gate.py``).

These exercise the pure logic that the mutation workflow relies on: kill-rate
computation (reused from ``tools/report.py``), the pass/fail decision against
the 90% floor, surviving-mutant extraction, and issue-body assembly.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "mutation_gate", TOOLS_DIR / "mutation_gate.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _mutant(outcome, module="src/odoo_sdk/x.py", operator="core/ReplaceOp", occ=0):
    return {
        "test_outcome": outcome,
        "module": module,
        "operator": operator,
        "occurrence": occ,
    }


class TestComputeKillRate(unittest.TestCase):
    def test_all_killed_is_100(self):
        mutants = [_mutant("killed"), _mutant("killed")]
        self.assertEqual(gate.compute_kill_rate(mutants), 100.0)

    def test_half_killed_is_50(self):
        mutants = [_mutant("killed"), _mutant("survived")]
        self.assertEqual(gate.compute_kill_rate(mutants), 50.0)

    def test_pending_mutants_are_excluded_from_denominator(self):
        # 1 killed / 1 completed = 100%; the pending (None) mutant is ignored.
        mutants = [_mutant("killed"), _mutant(None)]
        self.assertEqual(gate.compute_kill_rate(mutants), 100.0)

    def test_no_completed_mutants_is_zero(self):
        self.assertEqual(gate.compute_kill_rate([_mutant(None)]), 0.0)

    def test_empty_is_zero(self):
        self.assertEqual(gate.compute_kill_rate([]), 0.0)

    def test_incompetent_counts_as_survived(self):
        # Any non-"killed" completed outcome is a survivor.
        mutants = [_mutant("killed"), _mutant("incompetent")]
        self.assertEqual(gate.compute_kill_rate(mutants), 50.0)


class TestSurvivingMutants(unittest.TestCase):
    def test_extracts_only_completed_non_killed(self):
        mutants = [
            _mutant("killed"),
            _mutant("survived", module="a.py"),
            _mutant(None, module="pending.py"),
            _mutant("incompetent", module="b.py"),
        ]
        survivors = gate.surviving_mutants(mutants)
        modules = {m["module"] for m in survivors}
        self.assertEqual(modules, {"a.py", "b.py"})


class TestFormatSurvivors(unittest.TestCase):
    def test_empty_message(self):
        self.assertIn("No surviving", gate.format_survivors([]))

    def test_lists_each_survivor(self):
        out = gate.format_survivors([_mutant("survived", module="m.py")])
        self.assertIn("`m.py`", out)
        self.assertIn("core/ReplaceOp", out)

    def test_truncates_beyond_limit(self):
        survivors = [_mutant("survived", occ=i) for i in range(60)]
        out = gate.format_survivors(survivors, limit=50)
        self.assertIn("and 10 more", out)


class TestEvaluate(unittest.TestCase):
    def _write(self, mutants):
        tmp = Path(tempfile.mkdtemp()) / "mutation.json"
        tmp.write_text(json.dumps(mutants))
        return tmp

    def test_passes_at_floor(self):
        # 9 killed / 10 completed = 90.0, exactly the floor → pass.
        mutants = [_mutant("killed") for _ in range(9)] + [_mutant("survived")]
        result = gate.evaluate(self._write(mutants))
        self.assertEqual(result["kill_rate"], 90.0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["survived"], 1)
        self.assertEqual(result["total"], 10)

    def test_fails_below_floor(self):
        # 8 killed / 10 completed = 80.0 → fail.
        mutants = [_mutant("killed") for _ in range(8)] + [
            _mutant("survived") for _ in range(2)
        ]
        result = gate.evaluate(self._write(mutants))
        self.assertEqual(result["kill_rate"], 80.0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["survived"], 2)

    def test_rejects_non_list_json(self):
        tmp = Path(tempfile.mkdtemp()) / "mutation.json"
        tmp.write_text(json.dumps({"not": "a list"}))
        with self.assertRaises(ValueError):
            gate.evaluate(tmp)


class TestMainExitCode(unittest.TestCase):
    def _write(self, mutants):
        tmp = Path(tempfile.mkdtemp()) / "mutation.json"
        tmp.write_text(json.dumps(mutants))
        return tmp

    def test_exit_zero_when_passing(self):
        path = self._write([_mutant("killed")])
        self.assertEqual(gate.main(["mutation_gate.py", str(path)]), 0)

    def test_exit_one_when_failing(self):
        path = self._write([_mutant("survived")])
        self.assertEqual(gate.main(["mutation_gate.py", str(path)]), 1)

    def test_usage_error_on_bad_args(self):
        self.assertEqual(gate.main(["mutation_gate.py"]), 2)

    def test_survivors_mode_exits_zero_even_when_failing(self):
        path = self._write([_mutant("survived")])
        self.assertEqual(
            gate.main(["mutation_gate.py", "--survivors", str(path)]), 0
        )


if __name__ == "__main__":
    unittest.main()

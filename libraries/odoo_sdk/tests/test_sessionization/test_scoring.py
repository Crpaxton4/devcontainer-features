import unittest

from odoo_sdk.sessionization import score_day, score_gap

from ._helpers import one_day_config

HOUR = 3600.0


class TestScoreGapContinuity(unittest.TestCase):
    def test_anchor_at_b_low_equals_s_low(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0, s_low=0.0, s_high=1.0)
        self.assertAlmostEqual(score_gap(8 * HOUR, 1, cfg), cfg.s_low, places=9)

    def test_anchor_at_b_high_equals_s_high(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0, s_low=0.0, s_high=1.0)
        self.assertAlmostEqual(score_gap(10 * HOUR, 1, cfg), cfg.s_high, places=9)

    def test_below_band_penalised(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0)
        self.assertLess(score_gap(4 * HOUR, 1, cfg), cfg.s_low)

    def test_above_band_penalised(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0, s_high=1.0)
        self.assertLess(score_gap(14 * HOUR, 1, cfg), cfg.s_high)

    def test_optimal_band_monotonic(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0)
        self.assertLess(
            score_gap(8.5 * HOUR, 1, cfg), score_gap(9.5 * HOUR, 1, cfg)
        )

    def test_optimal_band_linear_fallback_when_denom_zero(self):
        # k2 -> 0 makes expm1(k2 * span) == 0, exercising the linear fallback.
        cfg = one_day_config(b_low=8.0, b_high=10.0, s_low=0.0, s_high=1.0, k2=0.0)
        mid = score_gap(9 * HOUR, 1, cfg)
        self.assertAlmostEqual(mid, 0.5, places=6)

    def test_num_days_averaging(self):
        cfg = one_day_config(b_low=8.0, b_high=10.0)
        # 16 total hours over 2 days == 8 h/day == b_low anchor.
        self.assertAlmostEqual(score_gap(16 * HOUR, 2, cfg), cfg.s_low, places=6)

    def test_score_day_is_one_day_gap(self):
        cfg = one_day_config()
        self.assertEqual(score_day(9 * HOUR, cfg), score_gap(9 * HOUR, 1, cfg))


if __name__ == "__main__":
    unittest.main()

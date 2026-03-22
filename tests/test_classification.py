"""Tests for classification and trajectory detection."""

import pytest

from trend_spotter.classification import classify, detect_trajectory


class TestClassify:
    def test_compounding(self):
        assert classify(80, 80) == "Compounding"

    def test_durable_slow(self):
        assert classify(80, 40) == "Durable/Slow"

    def test_flash_trend(self):
        assert classify(40, 80) == "Flash Trend"

    def test_ignore(self):
        assert classify(40, 40) == "Ignore"

    def test_threshold_boundary_high(self):
        # Exactly at threshold (65) counts as high
        assert classify(65, 65) == "Compounding"

    def test_threshold_boundary_low(self):
        assert classify(64, 64) == "Ignore"

    def test_mixed_boundary(self):
        assert classify(65, 64) == "Durable/Slow"
        assert classify(64, 65) == "Flash Trend"


class TestTrajectory:
    def test_no_previous_is_stable(self):
        assert detect_trajectory(0.5, None) == "stable"

    def test_rising(self):
        assert detect_trajectory(1.0, 0.5) == "rising"

    def test_declining(self):
        assert detect_trajectory(0.5, 1.0) == "declining"

    def test_stable_within_threshold(self):
        assert detect_trajectory(0.5, 0.45) == "stable"

    def test_exactly_at_delta_is_stable(self):
        # Delta of exactly 0.1 is not > 0.1, so stable
        assert detect_trajectory(0.6, 0.5) == "stable"

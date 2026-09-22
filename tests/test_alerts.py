#!/usr/bin/env python3
"""
=============================================================================
 Unit Tests — Alert Engine (Threshold, Hysteresis, Debounce)
=============================================================================
 Tests:
   - State transition: NORMAL -> WARNING after debounce count
   - State transition: WARNING -> CRITICAL after debounce count
   - Hysteresis band prevention of premature clear
   - Return to NORMAL only when below (warning - hysteresis)
   - Anti-flapping (intermittent spikes do not trigger alert)
   - Change-of-Value (CoV) deadband detection
=============================================================================
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway_python")))

from alerts import AlertEngine, AlertLevel, ThresholdConfig, TagState


@pytest.fixture
def alert_engine():
    config = {
        "voltage": {
            "warning": 245.0,
            "critical": 253.0,
            "hysteresis": 5.0,
            "debounce_count": 3,
        },
        "temperature": {
            "warning": 60.0,
            "critical": 75.0,
            "hysteresis": 5.0,
            "debounce_count": 2,
        },
    }
    return AlertEngine(config)


class TestAlertEngine:
    """Tests for AlertEngine state transitions."""

    def test_debounce_blocks_single_spike(self, alert_engine):
        """A single transient spike above warning threshold must not fire an alert."""
        # Baseline normal
        event = alert_engine.evaluate_tag("meter_01", "voltage", 230.0, "V")
        assert event is None

        # 1st spike (debounce requires 3)
        event = alert_engine.evaluate_tag("meter_01", "voltage", 246.0, "V")
        assert event is None

        # Drops back to normal
        event = alert_engine.evaluate_tag("meter_01", "voltage", 230.0, "V")
        assert event is None

    def test_transition_to_warning(self, alert_engine):
        """3 consecutive samples >= 245.0 must trigger a WARNING alert."""
        # 1st sample
        assert alert_engine.evaluate_tag("meter_01", "voltage", 246.0, "V") is None
        # 2nd sample
        assert alert_engine.evaluate_tag("meter_01", "voltage", 247.0, "V") is None
        # 3rd sample -> Triggers transition
        event = alert_engine.evaluate_tag("meter_01", "voltage", 246.5, "V")
        assert event is not None
        assert event.level == AlertLevel.WARNING
        assert event.tag == "voltage"
        assert event.device_id == "meter_01"
        assert event.threshold == 245.0

    def test_transition_to_critical(self, alert_engine):
        """Debounced samples >= 253.0 must transition to CRITICAL."""
        # Force into warning first
        for _ in range(3):
            alert_engine.evaluate_tag("meter_01", "voltage", 248.0, "V")

        # Now push 3 samples >= 253.0
        assert alert_engine.evaluate_tag("meter_01", "voltage", 254.0, "V") is None
        assert alert_engine.evaluate_tag("meter_01", "voltage", 255.0, "V") is None
        event = alert_engine.evaluate_tag("meter_01", "voltage", 254.5, "V")

        assert event is not None
        assert event.level == AlertLevel.CRITICAL
        assert event.threshold == 253.0

    def test_hysteresis_band_prevents_premature_clear(self, alert_engine):
        """
        Warning is at 245V, hysteresis is 5V.
        Dropping to 242V should NOT clear the alert (242 >= 245 - 5 = 240).
        Only dropping below 240V for 3 samples clears to NORMAL.
        """
        # Trigger warning
        for _ in range(3):
            alert_engine.evaluate_tag("meter_01", "voltage", 246.0, "V")

        # Drop to 242V (inside hysteresis band [240V, 245V])
        for _ in range(5):
            event = alert_engine.evaluate_tag("meter_01", "voltage", 242.0, "V")
            assert event is None  # Should stay WARNING, no clear event

        # Drop below 240V (238V)
        assert alert_engine.evaluate_tag("meter_01", "voltage", 238.0, "V") is None
        assert alert_engine.evaluate_tag("meter_01", "voltage", 238.0, "V") is None
        clear_event = alert_engine.evaluate_tag("meter_01", "voltage", 238.0, "V")

        assert clear_event is not None
        assert clear_event.level == AlertLevel.NORMAL
        assert clear_event.threshold == 240.0

    def test_change_of_value_deadband(self):
        """TagState.has_changed() respects deadband threshold."""
        cfg = ThresholdConfig(warning=50.0, critical=70.0, hysteresis=2.0, debounce_count=1)
        tag_state = TagState("test_tag", cfg)
        tag_state.cov_deadband = 1.0

        # Initial value always returns True
        assert tag_state.has_changed(10.0) is True
        tag_state.mark_published(10.0)

        # Minor fluctuation within deadband (0.4 < 1.0)
        assert tag_state.has_changed(10.4) is False
        assert tag_state.has_changed(9.6) is False

        # Exceeding deadband (1.2 >= 1.0)
        assert tag_state.has_changed(11.2) is True

#!/usr/bin/env python3
"""
=============================================================================
 Alert Engine — Threshold Evaluation with Hysteresis and Debounce
=============================================================================
 Evaluates each telemetry reading against configurable thresholds and
 generates alerts when values cross warning or critical levels.

 Anti-flapping measures:
   1. Hysteresis band: an alert fires at the threshold but clears only
      when the value drops below (threshold − hysteresis).
   2. Debounce counter: the value must exceed the threshold for N
      consecutive samples before the alert fires.
   3. Change-of-value (CoV): if the value hasn't changed significantly
      since the last publish, no message is sent (reduces bandwidth).

 Alert states per tag:
   NORMAL → WARNING → CRITICAL  (and back with hysteresis)
=============================================================================
"""

import enum
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("AlertEngine")


class AlertLevel(enum.Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class ThresholdConfig:
    """Threshold definition for a single tag."""
    warning: float
    critical: float
    hysteresis: float
    debounce_count: int


@dataclass
class AlertEvent:
    """An alert generated when a threshold is crossed."""
    device_id: str
    tag: str
    level: AlertLevel
    value: float
    threshold: float
    unit: str
    message: str
    timestamp: str


class TagState:
    """Tracks the alert state for a single measurement tag."""

    def __init__(self, tag: str, threshold: ThresholdConfig):
        self.tag = tag
        self.threshold = threshold
        self.current_level = AlertLevel.NORMAL
        self.consecutive_warning = 0
        self.consecutive_critical = 0
        self.consecutive_normal = 0
        self.last_published_value: Optional[float] = None
        self.cov_deadband = 0.5  # minimum change to republish (engineering units)

    def evaluate(self, value: float, device_id: str, unit: str,
                 timestamp: str) -> Optional[AlertEvent]:
        """
        Evaluate a new reading against thresholds.

        Returns an AlertEvent if a state transition occurred, else None.
        """
        previous_level = self.current_level

        # --- Count consecutive samples in each zone ---
        if value >= self.threshold.critical:
            self.consecutive_critical += 1
            self.consecutive_warning = 0
            self.consecutive_normal = 0
        elif value >= self.threshold.warning:
            self.consecutive_warning += 1
            self.consecutive_critical = 0
            self.consecutive_normal = 0
        elif value < (self.threshold.warning - self.threshold.hysteresis):
            # Below warning clear point
            self.consecutive_normal += 1
            self.consecutive_warning = 0
            self.consecutive_critical = 0
        else:
            # In hysteresis band — maintain current state, reset counters
            self.consecutive_normal = 0
            self.consecutive_warning = 0
            self.consecutive_critical = 0

        # --- Apply debounce and determine new level ---
        new_level = self.current_level

        if self.consecutive_critical >= self.threshold.debounce_count:
            new_level = AlertLevel.CRITICAL
        elif self.consecutive_warning >= self.threshold.debounce_count:
            new_level = AlertLevel.WARNING
        elif self.consecutive_normal >= self.threshold.debounce_count:
            new_level = AlertLevel.NORMAL

        # --- Check for state transition ---
        if new_level != previous_level:
            self.current_level = new_level

            if new_level == AlertLevel.CRITICAL:
                msg = (f"{self.tag}={value:.2f}{unit} exceeds CRITICAL "
                       f"threshold {self.threshold.critical}{unit}")
                threshold_val = self.threshold.critical
            elif new_level == AlertLevel.WARNING:
                msg = (f"{self.tag}={value:.2f}{unit} exceeds WARNING "
                       f"threshold {self.threshold.warning}{unit}")
                threshold_val = self.threshold.warning
            else:
                msg = (f"{self.tag}={value:.2f}{unit} returned to NORMAL "
                       f"(below {self.threshold.warning - self.threshold.hysteresis}{unit})")
                threshold_val = self.threshold.warning - self.threshold.hysteresis

            log.info("[%s] %s: %s → %s — %s",
                     device_id, self.tag, previous_level.value,
                     new_level.value, msg)

            return AlertEvent(
                device_id=device_id,
                tag=self.tag,
                level=new_level,
                value=value,
                threshold=threshold_val,
                unit=unit,
                message=msg,
                timestamp=timestamp,
            )

        return None

    def has_changed(self, value: float) -> bool:
        """
        Change-of-value detection: returns True if the value has changed
        enough from the last published value to warrant re-publishing.
        """
        if self.last_published_value is None:
            return True
        return abs(value - self.last_published_value) >= self.cov_deadband

    def mark_published(self, value: float):
        """Record the last published value for CoV comparison."""
        self.last_published_value = value


class AlertEngine:
    """
    Evaluates all incoming readings against thresholds and produces
    AlertEvent objects for any state transitions.
    """

    def __init__(self, threshold_config: dict):
        """
        Args:
            threshold_config: The 'thresholds' section of the YAML config.
                Format: { "voltage": { "warning": 245, "critical": 253, ... }, ... }
        """
        self._tag_states: dict[str, TagState] = {}
        for tag, cfg in threshold_config.items():
            tc = ThresholdConfig(
                warning=cfg["warning"],
                critical=cfg["critical"],
                hysteresis=cfg.get("hysteresis", 0.0),
                debounce_count=cfg.get("debounce_count", 1),
            )
            self._tag_states[tag] = TagState(tag, tc)
            log.info("Threshold: %s warn=%.1f crit=%.1f hyst=%.1f debounce=%d",
                     tag, tc.warning, tc.critical, tc.hysteresis, tc.debounce_count)

    def evaluate(self, device_id: str, tag: str, value: float,
                 unit: str, timestamp: str = "") -> Optional[AlertEvent]:
        """
        Check a reading against its threshold. Returns AlertEvent on
        state transition, None otherwise.
        """
        if not timestamp:
            from datetime import datetime, timezone
            timestamp = datetime.now(timezone.utc).isoformat()

        state = self._tag_states.get(tag)
        if state is None:
            return None  # no threshold configured for this tag
        return state.evaluate(value, device_id, unit, timestamp)

    evaluate_tag = evaluate

    def has_changed(self, tag: str, value: float) -> bool:
        """Check if a value has changed enough for CoV publishing."""
        state = self._tag_states.get(tag)
        if state is None:
            return True  # no threshold config → always publish
        return state.has_changed(value)

    def mark_published(self, tag: str, value: float):
        """Record that a value was published (for CoV tracking)."""
        state = self._tag_states.get(tag)
        if state:
            state.mark_published(value)

    def get_all_states(self) -> dict[str, str]:
        """Return current alert level for all tracked tags."""
        return {tag: s.current_level.value for tag, s in self._tag_states.items()}

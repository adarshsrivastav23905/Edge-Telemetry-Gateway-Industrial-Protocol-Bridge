#pragma once
// ===========================================================================
//  AlertEngine — Threshold Evaluation with Hysteresis and Debounce (C++)
// ===========================================================================

#ifndef ALERT_ENGINE_HPP
#define ALERT_ENGINE_HPP

#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "Config.hpp"

enum class AlertLevel {
    NORMAL,
    WARNING,
    CRITICAL
};

inline const char* alert_level_str(AlertLevel level) {
    switch (level) {
        case AlertLevel::NORMAL:   return "NORMAL";
        case AlertLevel::WARNING:  return "WARNING";
        case AlertLevel::CRITICAL: return "CRITICAL";
    }
    return "UNKNOWN";
}

/// An alert event generated on state transition.
struct AlertEvent {
    std::string device_id;
    std::string tag;
    AlertLevel  level;
    double      value;
    double      threshold;
    std::string unit;
    std::string message;
    std::string timestamp;
};

/// Per-tag state tracker.
struct TagState {
    AlertLevel  current_level  = AlertLevel::NORMAL;
    int         consec_warning = 0;
    int         consec_critical= 0;
    int         consec_normal  = 0;
    double      last_published_value = std::numeric_limits<double>::quiet_NaN();
    double      cov_deadband   = 0.5;
};

class AlertEngine {
public:
    explicit AlertEngine(const std::vector<ThresholdDef>& thresholds);

    /// Evaluate a reading. Returns AlertEvent on state transition.
    std::optional<AlertEvent> evaluate(const std::string& device_id,
                                       const std::string& tag,
                                       double value,
                                       const std::string& unit,
                                       const std::string& timestamp);

    /// Change-of-value check: has the value changed enough to re-publish?
    bool has_changed(const std::string& tag, double value) const;

    /// Mark a value as published (for CoV tracking).
    void mark_published(const std::string& tag, double value);

private:
    std::unordered_map<std::string, ThresholdDef> thresholds_;
    std::unordered_map<std::string, TagState>     tag_states_;
};

#endif // ALERT_ENGINE_HPP

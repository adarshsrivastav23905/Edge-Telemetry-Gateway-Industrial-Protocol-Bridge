// ===========================================================================
//  AlertEngine.cpp — Threshold Evaluation Implementation
// ===========================================================================

#include "AlertEngine.hpp"

#include <cmath>
#include <iostream>
#include <sstream>

AlertEngine::AlertEngine(const std::vector<ThresholdDef>& thresholds) {
    for (const auto& td : thresholds) {
        thresholds_[td.tag] = td;
        tag_states_[td.tag] = TagState{};
        std::cout << "[AlertEngine] Threshold: " << td.tag
                  << " warn=" << td.warning
                  << " crit=" << td.critical
                  << " hyst=" << td.hysteresis
                  << " debounce=" << td.debounce_count << std::endl;
    }
}

std::optional<AlertEvent> AlertEngine::evaluate(
    const std::string& device_id,
    const std::string& tag,
    double value,
    const std::string& unit,
    const std::string& timestamp)
{
    auto th_it = thresholds_.find(tag);
    if (th_it == thresholds_.end()) {
        return std::nullopt;  // no threshold configured
    }

    const ThresholdDef& td = th_it->second;
    TagState& state = tag_states_[tag];
    AlertLevel previous = state.current_level;

    // --- Count consecutive samples in each zone ---
    if (value >= td.critical) {
        state.consec_critical++;
        state.consec_warning = 0;
        state.consec_normal  = 0;
    } else if (value >= td.warning) {
        state.consec_warning++;
        state.consec_critical = 0;
        state.consec_normal   = 0;
    } else if (value < (td.warning - td.hysteresis)) {
        state.consec_normal++;
        state.consec_warning  = 0;
        state.consec_critical = 0;
    } else {
        // In hysteresis band — maintain state
        state.consec_normal   = 0;
        state.consec_warning  = 0;
        state.consec_critical = 0;
    }

    // --- Apply debounce ---
    AlertLevel new_level = state.current_level;

    if (state.consec_critical >= td.debounce_count) {
        new_level = AlertLevel::CRITICAL;
    } else if (state.consec_warning >= td.debounce_count) {
        new_level = AlertLevel::WARNING;
    } else if (state.consec_normal >= td.debounce_count) {
        new_level = AlertLevel::NORMAL;
    }

    // --- State transition? ---
    if (new_level != previous) {
        state.current_level = new_level;

        std::ostringstream msg;
        double threshold_val = 0.0;

        if (new_level == AlertLevel::CRITICAL) {
            msg << tag << "=" << value << unit
                << " exceeds CRITICAL threshold " << td.critical << unit;
            threshold_val = td.critical;
        } else if (new_level == AlertLevel::WARNING) {
            msg << tag << "=" << value << unit
                << " exceeds WARNING threshold " << td.warning << unit;
            threshold_val = td.warning;
        } else {
            msg << tag << "=" << value << unit
                << " returned to NORMAL (below "
                << (td.warning - td.hysteresis) << unit << ")";
            threshold_val = td.warning - td.hysteresis;
        }

        std::cout << "[AlertEngine] " << device_id << "/" << tag
                  << ": " << alert_level_str(previous)
                  << " → " << alert_level_str(new_level)
                  << " — " << msg.str() << std::endl;

        return AlertEvent{
            device_id, tag, new_level, value,
            threshold_val, unit, msg.str(), timestamp
        };
    }

    return std::nullopt;
}

bool AlertEngine::has_changed(const std::string& tag, double value) const {
    auto it = tag_states_.find(tag);
    if (it == tag_states_.end()) return true;
    if (std::isnan(it->second.last_published_value)) return true;
    return std::abs(value - it->second.last_published_value) >= it->second.cov_deadband;
}

void AlertEngine::mark_published(const std::string& tag, double value) {
    auto it = tag_states_.find(tag);
    if (it != tag_states_.end()) {
        it->second.last_published_value = value;
    }
}

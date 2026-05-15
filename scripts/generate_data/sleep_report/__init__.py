"""睡眠报告新实现包：按模块从 0 迁入，与旧流水线对齐后再切换入口。"""

from .time_utils import (
    calculate_duration,
    format_time_to_hhmm,
    generate_iso_date,
    minutes_between_datetimes,
    utc_to_local,
)

from .shared import (
    collected_at_to_local_naive_dt,
    format_sleep_event_local_timestamp,
    format_time,
    local_naive_dt_to_utc_iso_z,
    parse_sleep_event_timestamp_to_dt,
    parse_time,
    session_anchor_event_local_dt,
)

from .sleep_helpers import (
    build_sleep_events_index,
    night_wake_episodes_for_prompts,
    night_wake_minutes_for_prompts,
    probability_zero_night_awakenings,
)

from .sleep_score import (
    GOOD_SLEEP_PERSONALITIES,
    HIGH_SENS_HIGH_ACTIVE_PERSONALITIES,
    LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES,
    LOW_SENS_LOW_ACTIVE_PERSONALITIES,
    PERSONALITY_SLEEP_STAGE_RATIO_RANGES,
    POOR_SLEEP_PERSONALITIES,
    calculate_rule_based_sleep_score,
    calculate_sleep_report_structure_score,
    distribute_sleep_stage_minutes,
    four_stage_minutes_on_spt,
    idf_all_awake_minutes,
    load_environment_rows_for_record_date,
    normalize_four_spt_stage_percents,
    recalculate_rule_based_sleep_scores_in_health,
    sleep_report_score_from_sleep_data,
    sleep_report_structure_minutes_and_percents,
    spt_minutes_from_raw_sleep_window,
)

from .body_battery import (
    generate_body_battery,
    get_body_battery_status,
)

from .sleep_calendar import (
    apply_sleep_outlier_mode,
    build_sleep_outlier_mode_by_date,
    pick_non_consecutive_day_indices,
    strip_sleep_calendar_flags_from_health_records,
)

from .auditory import (
    backfill_auditory_audio_times_from_window_events,
    backfill_auditory_event_durations_from_report_audios,
    build_apnea_auditory_target_title,
    build_audios_from_auditory_sleep_events,
    build_auditory_snore_module,
    build_snoring_analysis_data_points,
    collect_auditory_sleep_events_in_window,
    generate_auditory,
    get_sleep_talk_data,
    index_environment_noise_rows_by_record_date,
    plan_auditory_snore_talk_cough_counts,
    rebuild_auditory_audios_and_snoring_data_points,
    resolve_auditory_audio_local_dt,
    sleep_data_shallow_from_report_for_auditory,
    sleep_local_window_bounds_from_sleep_data,
)

from .environment_summary import (
    generate_default_environment_summary,
    generate_environment_summary,
)

from .notice import (
    build_notice_yesterday_sleep_block,
    generate_ai_evidence,
    generate_notice,
    get_image_url_by_name,
)

from .title_summary import (
    build_main_local_summary,
    get_main_title_image_url,
    pick_main_title,
)

from .environment_metrics import (
    _build_noise_category_plan,
    _idf_awake_windows_minutes,
    _raw_awake_windows_minutes,
    _rebalance_environment_noise_daily_after_feedback,
    _sample_environment_metrics_sequence,
)

from .generate import (
    generate_sleep_report,
    generate_sleep_reports,
    check_and_process_sleep_talk,
)

__all__ = [
    # time_utils
    "calculate_duration",
    "utc_to_local",
    "format_time_to_hhmm",
    "generate_iso_date",
    "minutes_between_datetimes",
    # shared
    "collected_at_to_local_naive_dt",
    "format_sleep_event_local_timestamp",
    "format_time",
    "local_naive_dt_to_utc_iso_z",
    "parse_sleep_event_timestamp_to_dt",
    "parse_time",
    "session_anchor_event_local_dt",
    # sleep_helpers
    "build_sleep_events_index",
    "night_wake_episodes_for_prompts",
    "night_wake_minutes_for_prompts",
    "probability_zero_night_awakenings",
    # sleep_score
    "GOOD_SLEEP_PERSONALITIES",
    "HIGH_SENS_HIGH_ACTIVE_PERSONALITIES",
    "LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES",
    "LOW_SENS_LOW_ACTIVE_PERSONALITIES",
    "PERSONALITY_SLEEP_STAGE_RATIO_RANGES",
    "POOR_SLEEP_PERSONALITIES",
    "calculate_rule_based_sleep_score",
    "calculate_sleep_report_structure_score",
    "distribute_sleep_stage_minutes",
    "four_stage_minutes_on_spt",
    "idf_all_awake_minutes",
    "load_environment_rows_for_record_date",
    "normalize_four_spt_stage_percents",
    "recalculate_rule_based_sleep_scores_in_health",
    "sleep_report_score_from_sleep_data",
    "sleep_report_structure_minutes_and_percents",
    "spt_minutes_from_raw_sleep_window",
    # body_battery
    "generate_body_battery",
    "get_body_battery_status",
    # sleep_calendar
    "apply_sleep_outlier_mode",
    "build_sleep_outlier_mode_by_date",
    "pick_non_consecutive_day_indices",
    "strip_sleep_calendar_flags_from_health_records",
    # auditory
    "backfill_auditory_audio_times_from_window_events",
    "backfill_auditory_event_durations_from_report_audios",
    "build_apnea_auditory_target_title",
    "build_audios_from_auditory_sleep_events",
    "build_auditory_snore_module",
    "build_snoring_analysis_data_points",
    "collect_auditory_sleep_events_in_window",
    "generate_auditory",
    "get_sleep_talk_data",
    "index_environment_noise_rows_by_record_date",
    "plan_auditory_snore_talk_cough_counts",
    "rebuild_auditory_audios_and_snoring_data_points",
    "resolve_auditory_audio_local_dt",
    "sleep_data_shallow_from_report_for_auditory",
    "sleep_local_window_bounds_from_sleep_data",
    # environment_summary
    "generate_default_environment_summary",
    "generate_environment_summary",
    # notice
    "build_notice_yesterday_sleep_block",
    "generate_ai_evidence",
    "generate_notice",
    "get_image_url_by_name",
    # title_summary
    "build_main_local_summary",
    "get_main_title_image_url",
    "pick_main_title",
    # environment_metrics
    "_build_noise_category_plan",
    "_idf_awake_windows_minutes",
    "_raw_awake_windows_minutes",
    "_rebalance_environment_noise_daily_after_feedback",
    "_sample_environment_metrics_sequence",
    # generate
    "generate_sleep_report",
    "generate_sleep_reports",
    "check_and_process_sleep_talk",
]

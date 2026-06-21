import os

try:
    from hypothesis import HealthCheck, settings

    # unittest discovery imports the tests package, so load the active profile here.
    ACTIVE_HYPOTHESIS_PROFILE = os.getenv("HYPOTHESIS_PROFILE", "default")
    PROFILE_MAX_EXAMPLES = {
        "default": 25,
        "ci": 100,
    }
    _BASE_PROFILE = {
        "deadline": None,
        "print_blob": True,
        "suppress_health_check": (HealthCheck.too_slow,),
    }

    for profile_name, max_examples in PROFILE_MAX_EXAMPLES.items():
        settings.register_profile(
            profile_name,
            max_examples=max_examples,
            derandomize=profile_name == "ci",
            **_BASE_PROFILE,
        )

    settings.load_profile(ACTIVE_HYPOTHESIS_PROFILE)
except Exception:
    # Hypothesis not available in this environment; tests that depend on it
    # should avoid importing it. Continue without configuring Hypothesis.
    pass

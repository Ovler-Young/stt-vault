import sys


def current_rss_mb() -> float | None:
    try:
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None

    return _rss_value_to_mb(value, sys.platform)


def _rss_value_to_mb(value: float, platform: str) -> float:
    # Linux reports KiB; macOS reports bytes.
    divisor = 1024 * 1024 if platform == "darwin" else 1024
    return round(value / divisor, 1)

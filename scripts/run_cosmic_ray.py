import asyncio
import sys

from cosmic_ray.cli import main


def ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    ensure_event_loop()
    raise SystemExit(main(sys.argv[1:]))
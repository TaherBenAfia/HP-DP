"""Consumer script for the data sharing package.

This script is intentionally a lightweight placeholder used by the
integration tests to verify the expected consumer entrypoint exists.
"""

from __future__ import annotations

import argparse


def main() -> None:
    """Run the consumer script."""
    parser = argparse.ArgumentParser(description="Data sharing consumer")
    parser.add_argument("--version", action="store_true", help="Print the consumer version")
    args = parser.parse_args()

    if args.version:
        print("consumer 0.1.0")
    else:
        print("Consumer is ready")


if __name__ == "__main__":
    main()

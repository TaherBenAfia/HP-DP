"""Consumer script for data sharing tests.

This file exists to satisfy the integration test that verifies the
presence of a consumer script at sharing/consumer/consumer.py.

It provides a minimal CLI entrypoint for manual testing.
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Consumer script placeholder")
    parser.add_argument("--version", action="store_true", help="Print version")
    args = parser.parse_args()

    if args.version:
        print("consumer: 0.1.0")
    else:
        print("Consumer script placeholder - no action")


if __name__ == "__main__":
    main()

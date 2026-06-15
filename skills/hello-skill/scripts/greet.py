#!/usr/bin/env python3
"""Print a friendly greeting. Used by the `hello-skill` example skill."""
import sys


def greet(name: str) -> str:
    return f"Hello, {name}! -- from Laiye Agent Skills."


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "World"
    print(greet(name))


if __name__ == "__main__":
    main()

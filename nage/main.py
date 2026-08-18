"""Compatibility entry point for the Nage command."""

from .cli import cli

__all__ = ["cli"]


if __name__ == "__main__":
    cli()

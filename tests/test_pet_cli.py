"""CLI wiring tests that do not require opening a window."""
from __future__ import annotations

from photopet.cli import build_parser


def test_pet_subcommand_is_available():
    parser = build_parser()
    args = parser.parse_args(["pet", "--assets", "examples/demo_character"])
    assert args.command == "pet"
    assert args.assets == "examples/demo_character"
    assert args.func.__name__ == "_cmd_pet"


def test_pet_subcommand_accepts_diagnostics_and_smoke():
    parser = build_parser()
    args = parser.parse_args(
        ["pet", "--diagnostics", "--smoke-test", "2", "--smoke-report", "report.json"]
    )
    assert args.diagnostics is True
    assert args.smoke_test == 2.0
    assert args.smoke_report == "report.json"


def test_existing_build_subcommand_is_unchanged():
    parser = build_parser()
    args = parser.parse_args(["build", "examples/mannequin/project.json", "--no-zip"])
    assert args.command == "build"
    assert args.no_zip is True

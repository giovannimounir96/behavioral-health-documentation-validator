"""Command-line interface for the validation pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from collections.abc import Sequence

from .exceptions import ValidatorError
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bh-validate",
        description=(
            "Validate synthetic behavioral-health appointment and documentation CSVs."
        ),
    )
    parser.add_argument("--appointments", type=Path, required=True)
    parser.add_argument("--documentation", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        result = run_pipeline(
            appointments_path=args.appointments,
            documentation_path=args.documentation,
            config_path=args.config,
            output_dir=args.output_dir,
        )
    except (ValidatorError, OSError) as exc:
        logging.getLogger(__name__).error("Validation failed: %s", exc)
        return 1

    print(f"Validated {result.appointment_count} appointments.")
    print(f"Valid documentation: {result.valid_count}")
    print(f"Exceptions: {result.exception_count}")
    print(f"Detailed report: {result.detailed_report}")
    print(f"Provider summary: {result.provider_summary}")
    print(f"Exceptions report: {result.exceptions_report}")
    return 0


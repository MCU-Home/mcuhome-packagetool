#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Verify an MCUHome package source — the normative reference implementation.

The command line over :mod:`mcuhome.packagetool.verify`, which holds the
rules themselves. This file decides nothing: it parses arguments, hands
a source and an anchor to :func:`~mcuhome.packagetool.verify.verify_source`,
and turns the verdict into an exit status and a line of output.

**Why the rules moved into the package.** Other MCUHome tools — the
workbench above all — have to reach the same verdicts as this command,
and they reach them by importing the same functions rather than by
transcribing them. A second implementation of the signature, freshness
and hashing rules would be a second thing to get right, and the two
would drift apart quietly. The price is that this file is no longer a
single copyable script: run it from a checkout whose virtual
environment has this repository installed, exactly as the README
describes.

The public names of the verifier are re-exported here, so a reader who
starts at this file and an importer who starts at the module see one
surface.

Usage::

    verify.py <source-dir> --anchor <anchor.json> [--client-generation N]
              [--now <ISO>] [--state <file>]

Exit status: 0 when every check passes, 1 on the first refusal, 2 on a
usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mcuhome.packagetool.verify import (
    CLIENT_GENERATION,
    INDEX_FILE,
    KEYS_FILE,
    MIRRORS_FILE,
    SIGNATURE_SUFFIX,
    KeySet,
    Refused,
    _stamp,
    all_entries,
    canonical_json,
    check_header,
    check_meta_entry,
    check_signatures,
    load_anchor,
    verify_entries,
    verify_keys,
    verify_parts,
    verify_signed_by_publisher,
    verify_source,
)

__all__ = [
    "CLIENT_GENERATION",
    "INDEX_FILE",
    "KEYS_FILE",
    "MIRRORS_FILE",
    "SIGNATURE_SUFFIX",
    "KeySet",
    "Refused",
    "all_entries",
    "canonical_json",
    "check_header",
    "check_meta_entry",
    "check_signatures",
    "load_anchor",
    "main",
    "verify_entries",
    "verify_keys",
    "verify_parts",
    "verify_signed_by_publisher",
    "verify_source",
]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the source directory to verify")
    parser.add_argument("--anchor", type=Path, required=True, help="the root key set to trust")
    parser.add_argument("--client-generation", type=int, default=CLIENT_GENERATION)
    parser.add_argument("--now", help="ISO timestamp to verify as of, instead of the clock")
    parser.add_argument("--state", type=Path, help="anti-rollback state; read and updated")
    arguments = parser.parse_args(argv)

    state = {}
    if arguments.state and arguments.state.is_file():
        state = json.loads(arguments.state.read_text(encoding="utf-8"))

    try:
        report = verify_source(
            arguments.source,
            load_anchor(arguments.anchor),
            now=_stamp(arguments.now, "--now") if arguments.now else None,
            generation=arguments.client_generation,
            state=state,
        )
    except Refused as refusal:
        print(f"REFUSED  {refusal}", file=sys.stderr)
        return 1

    if arguments.state:
        arguments.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(
        f"OK  {arguments.source}: {report['threshold']}-of-{report['roots']} roots, "
        f"{report['publishers']} publisher(s), {report['revoked']} revoked, "
        f"{report['mirrors']} mirror(s), {report['parts']} part(s), "
        f"{report['entries']} package entr{'y' if report['entries'] == 1 else 'ies'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))

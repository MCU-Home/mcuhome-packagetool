# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The reference verifier against the committed corpus.

Every case states the verdict ADR 0025 §9 requires and a fragment of the
reason a user would read, so a refusal that starts happening for the
wrong reason is a failure too — "it refused" is not the same as "it
refused because the part hash did not match".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from make_vectors import NOW, VECTORS

from verify import KEYS_FILE, KeySet, Refused, load_anchor, verify_source

ANCHOR = load_anchor(VECTORS / "anchor.json")

#: The same moment as ``NOW``, spelled the way ``--now`` takes it.
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")

ACCEPTED = [
    ("valid", "a complete, current source"),
    ("revoked-retired", "a retired key's earlier signatures stay valid"),
    ("overlap-dual-signed", "outgoing and incoming publisher both sign"),
    ("with-parts", "entries in a part, hashed by the head"),
    ("rotated-chain", "two rotations past the anchor, reached via previous"),
]

REFUSED = [
    ("expired-index", "expired"),
    ("wrong-key", "usable signature"),
    ("revoked-compromised", "revoked"),
    ("tampered-index", "does not verify"),
    ("tampered-part", "hashes to"),
    ("below-threshold", "required"),
    ("min-client-too-high", "update the tool"),
    ("malformed-meta-file", "needs file, sha256 and size"),
]


@pytest.mark.parametrize(("case", "why"), ACCEPTED)
def test_accepted(case: str, why: str) -> None:
    report = verify_source(VECTORS / case, ANCHOR, now=NOW)
    assert report["threshold"] == 2, why


@pytest.mark.parametrize(("case", "fragment"), REFUSED)
def test_refused(case: str, fragment: str) -> None:
    with pytest.raises(Refused) as refusal:
        verify_source(VECTORS / case, ANCHOR, now=NOW)
    assert fragment in str(refusal.value)


def test_parts_and_entries_are_counted() -> None:
    report = verify_source(VECTORS / "with-parts", ANCHOR, now=NOW)
    assert report["parts"] == 1
    # One version in the head is none: both live in the part, because the
    # part covers their range and placement follows `covers`.
    assert report["entries"] == 2


def test_the_corpus_carries_a_meta_file_a_client_can_follow() -> None:
    """The whole path a chain is resolved along, in the vector itself.

    The index names the sidecar by hash and size and says nothing about
    its content; a client fetches those bytes, checks them against the
    record, and reads ``requires`` out of them. One document per stage,
    whatever the index grows to.
    """
    import hashlib

    source = VECTORS / "valid"
    index = json.loads((source / "index.json").read_text())
    entry = index["packages"]["mcuhome-sdk"]["2.4.0"]
    record = entry["meta_file"]
    assert not {"requires", "inputs_sha256", "contents"} & set(entry)

    payload = (source / record["file"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == record["sha256"]
    assert len(payload) == record["size"]
    assert json.loads(payload)["requires"] == {"mcuhome-build-workspace": "~=2.4.0"}


def test_rollback_is_refused_and_state_advances() -> None:
    """A mirror may withhold a newer document; it may not replay an older one."""
    state: dict = {}
    verify_source(VECTORS / "valid", ANCHOR, now=NOW, state=state)
    assert state["index.json"].startswith("2026-01-15")

    ahead = {"index.json": "2026-01-20T00:00:00Z"}
    with pytest.raises(Refused, match="not newer"):
        verify_source(VECTORS / "valid", ANCHOR, now=NOW, state=ahead)


def test_min_client_gate_opens_for_a_newer_client() -> None:
    with pytest.raises(Refused, match="update the tool"):
        verify_source(VECTORS / "min-client-too-high", ANCHOR, now=NOW, generation=1)
    assert verify_source(VECTORS / "min-client-too-high", ANCHOR, now=NOW, generation=2)


def test_a_foreign_anchor_verifies_nothing() -> None:
    """The anchor is what decides; a source cannot talk its way past it."""
    rotated = json.loads((VECTORS / "rotated-chain" / KEYS_FILE).read_text())
    foreign = KeySet(
        keys={entry["keyid"]: entry["public"] for entry in rotated["roots"]["keys"]},
        threshold=rotated["roots"]["threshold"],
    )
    with pytest.raises(Refused, match="names no predecessor"):
        verify_source(VECTORS / "valid", foreign, now=NOW)


def test_missing_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="keys.json is missing"):
        verify_source(tmp_path, ANCHOR, now=NOW)


def test_the_command_line_re_exports_the_module_it_runs() -> None:
    """One implementation, two front ends — never two implementations.

    The rules live in ``mcuhome.packagetool.verify`` so that other tools
    can import them; ``verify.py`` is the command line over that module.
    Identity, not equality: a re-export that had drifted into a copy
    would still compare equal by name and would no longer be the code
    the tests above exercise.
    """
    import verify as command_line
    from mcuhome.packagetool import verify as module

    shared = [name for name in module.__all__ if hasattr(command_line, name)]
    assert set(shared) == set(module.__all__)
    for name in shared:
        assert getattr(command_line, name) is getattr(module, name), name


def test_main_reports_a_verified_source(capsys) -> None:
    from verify import main

    assert (
        main([str(VECTORS / "valid"), "--anchor", str(VECTORS / "anchor.json"), "--now", NOW_ISO])
        == 0
    )
    assert "OK  " in capsys.readouterr().out


def test_main_reports_a_refusal_on_stderr(capsys) -> None:
    from verify import main

    argv = [
        str(VECTORS / "tampered-index"),
        "--anchor",
        str(VECTORS / "anchor.json"),
        "--now",
        NOW_ISO,
    ]
    assert main(argv) == 1
    assert "REFUSED  " in capsys.readouterr().err


def test_all_entries_merges_head_and_parts() -> None:
    """The seam the workbench resolves a package through.

    A source whose index has outgrown one file keeps most of its entries
    in the parts, so a client that read only the head would resolve
    against a fraction of the source and call the rest absent.
    """
    from mcuhome.packagetool.verify import all_entries

    source = VECTORS / "with-parts"
    index = json.loads((source / "index.json").read_text())
    assert not index.get("packages")
    merged = all_entries(source, index)
    assert sum(len(versions) for versions in merged.values()) == 2

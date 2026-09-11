# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The package meta file — ``<archive>.meta.json`` — checked before it is recorded.

A package archive may be published with a second sidecar beside its
``.sha256``: one JSON document saying what the package *is* and what it
*requires* of the next package in the chain it belongs to. The registry
serves it next to the archive and records it in the index by file name,
hash and size — never its content.

That split is the whole point of the file. A client resolves "the newest
version satisfying this constraint" from the version list the index
already carries, and only then fetches exactly one meta file to learn
what that version requires. An index that carried the content instead
would grow with every release and be downloaded in full by everyone, to
answer a question about one version.

Four members, three of which are this tool's business:

``schema``         the generation of the format; only 1 exists
``package``        name, version and architecture — held against the
                   archive actually being recorded
``requires``       package name to PEP 440 specifier, both parsed;
                   absent where a package requires nothing
``inputs_sha256``  the hash of the inputs the package was built from

The fourth, ``contents``, is **opaque here and passed through
untouched**. What a workspace package records about its project
revisions, or a tools package about its tool versions, is the producing
side's vocabulary; a registry that validated it would need changing
whenever a producer learned a new word, and it has no way of judging
whether what it reads is true. The registry's job is that the file
belongs to the archive it is served beside, and that its bytes are the
bytes the index names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

__all__ = [
    "NAME",
    "SCHEMA",
    "SUFFIX",
    "check",
    "parse",
    "path_beside",
]

#: The generation of this format. One exists, and a document declaring
#: anything else is refused rather than guessed at: every member below is
#: read by somebody, so a file from a format nobody here knows is not
#: half-understood, it is not understood.
SCHEMA = 1

#: What the file is called: the archive's own file name plus this.
SUFFIX = ".meta.json"

#: A package name: lowercase alphanumerics and ``-``, optionally followed
#: by an architecture suffix introduced by ``_``. The first ``_`` splits
#: the family from the platform it was built for, so a reader that wants
#: the family takes what is in front of it and one that wants the exact
#: package takes the whole name.
NAME = re.compile(r"[a-z0-9][a-z0-9-]*(?:_[a-z0-9][a-z0-9-]*)?")

#: One part of such a name, which is also what an architecture looks like.
PART = re.compile(r"[a-z0-9][a-z0-9-]*")

#: The registry host a requirement may be prefixed with: a DNS name,
#: optionally with a port. A requirement without one means the host the
#: package stating it was itself published on.
HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::\d+)?")

#: 64 lowercase hex digits, and nothing else.
SHA256 = re.compile(r"[0-9a-f]{64}")


def path_beside(archive: Path) -> Path:
    """Where a package's meta file sits: beside the archive, named after it.

    Derived rather than stated, because "beside the archive" is what the
    served layout guarantees and what a client resolves against: a meta
    file somewhere else would be one the index could name and a mirror
    could miss.
    """
    return archive.with_name(archive.name + SUFFIX)


def parse(payload: bytes, *, what: str) -> object:
    """*payload* as a document, or a refusal naming the file it came from.

    Takes bytes rather than a path because the caller hashes the same
    bytes into the index: reading once is what makes the recorded hash
    and the checked content provably the same file.
    """
    try:
        return json.loads(payload)
    except ValueError as broken:
        raise SystemExit(f"{what} is not JSON: {broken}") from broken


def check(document: object, *, name: str, version: str, what: str) -> None:
    """Refuse *document* unless it is a schema-1 meta file for *name* *version*.

    *name* and *version* are the package being recorded, so this is also
    the check that a meta file cannot be published beside an archive it
    does not describe — the one mistake a hash cannot catch, because the
    file would be exactly the bytes somebody meant to publish, next to
    the wrong package.
    """
    if not isinstance(document, dict):
        raise SystemExit(f"{what} is not a JSON object")

    schema = document.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        raise SystemExit(
            f"{what} declares schema {schema!r}; this tool records meta files of schema {SCHEMA}"
        )

    _check_package(document.get("package"), name=name, version=version, what=what)
    _check_requires(document.get("requires"), what=what)

    inputs = document.get("inputs_sha256")
    if not isinstance(inputs, str) or not SHA256.fullmatch(inputs):
        raise SystemExit(
            f"{what}: inputs_sha256 is {inputs!r}, and it has to be 64 lowercase hex digits"
        )

    contents = document.get("contents")
    if contents is not None and not isinstance(contents, dict):
        raise SystemExit(
            f"{what}: contents is {type(contents).__name__}, and it has to be an object — "
            "its members are the producer's own and are recorded nowhere here"
        )


def _check_package(package: object, *, name: str, version: str, what: str) -> None:
    """The ``package`` object, and that it is about the archive being added."""
    if not isinstance(package, dict):
        raise SystemExit(f"{what} carries no package object")

    declared = package.get("name")
    if not isinstance(declared, str) or not NAME.fullmatch(declared):
        raise SystemExit(f"{what}: package.name is {declared!r}, which is not a package name")

    stated = package.get("version")
    if not isinstance(stated, str):
        raise SystemExit(f"{what}: package.version is {stated!r}, which is not a version")
    try:
        Version(stated)
    except InvalidVersion as broken:
        raise SystemExit(
            f"{what}: package.version {stated!r} is not a PEP 440 version ({broken})"
        ) from broken
    if stated != version:
        raise SystemExit(
            f"{what} describes version {stated}, and {name} {version} is being recorded — "
            "the index is resolved by the version it records, so the two have to be the "
            "same string"
        )

    if "architecture" not in package:
        raise SystemExit(
            f"{what}: package.architecture is missing — state null where a package is the "
            "same on every platform"
        )
    architecture = package["architecture"]
    if architecture is not None and (
        not isinstance(architecture, str) or not PART.fullmatch(architecture)
    ):
        raise SystemExit(
            f"{what}: package.architecture is {architecture!r}; it is null or a platform "
            "such as 'linux-amd64'"
        )

    _check_name(declared, architecture, added=name, what=what)


def _check_name(declared: str, architecture: str | None, *, added: str, what: str) -> None:
    """Which recorded package name a declaration may belong to.

    A package built per platform is published as ``<family>_<platform>``,
    and its meta file may name either the family plus the architecture or
    the concrete package — the two compose into one name, so both say the
    same thing. What neither may do is disagree with the archive.
    """
    acceptable = {declared}
    if architecture is not None:
        acceptable.add(f"{declared}_{architecture}")
    if added not in acceptable:
        raise SystemExit(
            f"{what} describes the package {declared!r}, and the archive being recorded is "
            f"{added!r} — a meta file belongs to the archive it is published beside"
        )

    _, separator, suffix = added.partition("_")
    if architecture is None:
        if separator:
            raise SystemExit(
                f"{what} declares no architecture, and {added!r} carries the platform suffix "
                f"{suffix!r} — a package built for one platform states which one"
            )
    elif suffix != architecture:
        raise SystemExit(
            f"{what} declares the architecture {architecture!r}, and the archive is recorded "
            f"as {added!r} — a package built for one platform is published as "
            f"{declared}_{architecture}"
        )


def _check_requires(requires: object, *, what: str) -> None:
    """The constraint on the next package: names and specifiers, both parsed.

    Only the shape is checked, never the policy. Whether a constraint is
    a sensible one to have published, and which version satisfies it, is
    the resolving client's question — the registry neither resolves nor
    has an opinion.
    """
    if requires is None:
        return
    if not isinstance(requires, dict):
        raise SystemExit(
            f"{what}: requires is {type(requires).__name__}, and it has to be an object of "
            "package name to constraint"
        )
    for reference, specifier in requires.items():
        host, separator, package = str(reference).rpartition("/")
        if separator and not HOST.fullmatch(host):
            raise SystemExit(
                f"{what}: requires names {reference!r}, whose host part {host!r} is not a "
                "registry host"
            )
        if not NAME.fullmatch(package):
            raise SystemExit(
                f"{what}: requires names {reference!r}, which is not a package name, "
                "optionally prefixed with the registry host it is published on"
            )
        if not isinstance(specifier, str):
            raise SystemExit(
                f"{what}: the constraint on {reference} is {specifier!r}, and it has to be a "
                "PEP 440 specifier"
            )
        try:
            SpecifierSet(specifier)
        except InvalidSpecifier as broken:
            raise SystemExit(
                f"{what}: the constraint {specifier!r} on {reference} is not a PEP 440 "
                f"specifier ({broken})"
            ) from broken

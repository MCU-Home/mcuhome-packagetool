# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Building and maintaining one source: its key set, mirror list and index.

A **source** is one self-contained directory: everything needed to use
it lives at or below it, and nothing in it says anything about any other
source. This module is the write side of that — the read side, and the
authority on what a client must check, is ``verify.py`` at the
repository root.

Three operations matter and each is small:

``init``      lay a source down: ``keys.json`` (root-signed),
              ``mirrors.json`` and an empty ``index.json``
``add``       record one package — and, where one is published beside it,
              the ``<archive>.meta.json`` sidecar that says what the
              package requires (:mod:`mcuhome.packagetool.metafile`)
``add-meta``  record a *meta* package: one name standing for a set of
              concrete packages, one per architecture
``refresh``   renew the two publisher-signed documents before they expire

What deliberately has no operation here is *deleting* anything.
Superseded part files are the sole prunable artefact, and
:func:`unreferenced_parts` only reports them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from packaging.version import InvalidVersion, Version

from mcuhome.packagetool import metafile
from mcuhome.packagetool.documents import (
    INDEX_EXPIRY_DAYS,
    KEYS_EXPIRY_DAYS,
    MIRRORS_EXPIRY_DAYS,
    SIGNATURE_SUFFIX,
    assert_advances,
    dump,
    header,
    write_signed,
)
from mcuhome.packagetool.keys import SigningKey

__all__ = [
    "INDEX_FILE",
    "KEYS_FILE",
    "META_FILE_KEY",
    "META_KEY",
    "MIRRORS_FILE",
    "PublicKey",
    "add_meta_package",
    "add_package",
    "canonical_json",
    "covering_part",
    "expand_meta",
    "init_source",
    "meta_sha256",
    "part_filename",
    "read_document",
    "refresh",
    "unreferenced_parts",
    "write_keys",
]

KEYS_FILE = "keys.json"
MIRRORS_FILE = "mirrors.json"
INDEX_FILE = "index.json"
KEYS_ARCHIVE = "keys"

#: What makes an index entry a *meta* entry: it names packages instead of
#: bytes, so it carries this member and neither ``file`` nor ``size``.
META_KEY = "meta"

#: Where an ordinary entry records the package's meta file, by file name,
#: hash and size — the same three members the archive itself is recorded
#: by, and nothing of its content.
#:
#: Deliberately *not* ``meta``: that member is what makes an entry a meta
#: package, and every reader of an index — this tool, the reference
#: verifier, the workbench, the browse page — decides which kind of entry
#: it is holding by asking whether it is there. A sidecar recorded under
#: that name would turn every ordinary package into a malformed meta one.
META_FILE_KEY = "meta_file"


@dataclass(frozen=True)
class PublicKey:
    """A public key as a document records it, with its validity window."""

    keyid: str
    public: str
    not_before: str
    not_after: str

    def entry(self) -> dict[str, str]:
        return {
            "keyid": self.keyid,
            "public": self.public,
            "not_before": self.not_before,
            "not_after": self.not_after,
        }


def read_document(path: Path) -> dict:
    """One JSON document, or a refusal naming the file."""
    try:
        loaded = json.loads(path.read_bytes())
    except (OSError, ValueError) as failure:
        raise SystemExit(f"{path}: {failure}") from failure
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path} is not a JSON object")
    return loaded


# --------------------------------------------------------------------------- keys


def write_keys(
    source: Path,
    *,
    roots: Sequence[PublicKey],
    threshold: int,
    publishers: Sequence[PublicKey],
    revoked: Sequence[dict[str, str]] = (),
    issued: datetime,
    signers: Sequence[SigningKey],
) -> None:
    """Write (or replace) the source's ``keys.json``, root-signed.

    The predecessor is archived under ``keys/<issued>.json`` before it is
    replaced and is named by the new document's ``previous``, so a client
    that has been offline across rotations can walk backwards to a key
    set it still trusts and then verify forward. Archived key sets are
    never removed.
    """
    path = source / KEYS_FILE
    previous_link: str | None = None
    if path.exists():
        old = read_document(path)
        assert_advances(old, issued, KEYS_FILE)
        stamped = str(old.get("issued", "")).replace(":", "").replace("-", "")
        archive = source / KEYS_ARCHIVE / f"{stamped}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            archive.write_bytes(path.read_bytes())
            signature = path.with_name(path.name + SIGNATURE_SUFFIX)
            if signature.exists():
                archive.with_name(archive.name + SIGNATURE_SUFFIX).write_bytes(
                    signature.read_bytes()
                )
        previous_link = f"{KEYS_ARCHIVE}/{archive.name}"

    if threshold > len(roots):
        raise SystemExit(f"threshold {threshold} exceeds the {len(roots)} root keys given")
    if len(signers) < threshold:
        raise SystemExit(f"{len(signers)} signing keys given, threshold is {threshold}")

    document = {
        **header(issued=issued, expires_days=KEYS_EXPIRY_DAYS),
        "previous": previous_link,
        "roots": {"threshold": threshold, "keys": [key.entry() for key in roots]},
        "publishers": [key.entry() for key in publishers],
        "revoked": list(revoked),
    }
    write_signed(path, document, signers)


# --------------------------------------------------------------------------- index


def part_filename(covers: dict, payload: bytes) -> str:
    """What a part file is called: a readable shard label plus its content hash.

    Immutability is the service's guarantee rather than every client's
    discipline, and it is this name that gives it: the same content
    always yields the same name, changed content always yields a
    different one, so no cache at any layer can serve a stale part under
    a current name.
    """
    digest = hashlib.sha256(payload).hexdigest()[:16]
    label = "part"
    for kind in ("versions", "names"):
        span = covers.get(kind) if isinstance(covers, dict) else None
        if isinstance(span, dict) and span.get("min") is not None:
            label = f"{span.get('min')}-{span.get('max')}"
            break
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "part"
    return f"index-{label}-{digest}.json"


def _version_in(covers: dict, version: str) -> bool:
    """Whether *version* belongs in a part declaring *covers*."""
    span = covers.get("versions") if isinstance(covers, dict) else None
    if not isinstance(span, dict):
        return False
    try:
        candidate = Version(version)
        low = Version(str(span["min"]))
        high = Version(str(span["max"]))
    except (InvalidVersion, KeyError, TypeError):
        return False
    return low <= candidate <= high


def covering_part(parts: Sequence[dict], version: str) -> dict | None:
    """The part a new *version* belongs in, or ``None`` for the head.

    Placement is a publishing decision, not a consequence of age: the
    publisher declares what a part covers and every entry goes where it
    is covered.
    """
    for part in parts:
        if _version_in(part.get("covers") or {}, version):
            return part
    return None


def _entries_of_part(source: Path, part: dict) -> dict:
    """A part's packages, after checking it against the hash in the head."""
    path = source / str(part["file"])
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != part.get("sha256"):
        raise SystemExit(f"{path} does not match the sha256 the index head records")
    document = json.loads(payload)
    packages = document.get("packages")
    return packages if isinstance(packages, dict) else {}


def _entry_of(source: Path, index: dict, name: str, version: str) -> dict | None:
    """One package version's entry, wherever in the source it is recorded."""
    found = (index.get("packages") or {}).get(name, {}).get(version)
    if isinstance(found, dict):
        return found
    for part in index.get("parts") or []:
        found = _entries_of_part(source, part).get(name, {}).get(version)
        if isinstance(found, dict):
            return found
    return None


def _meta_file_entry(
    source: Path, *, name: str, version: str, file: str, required: bool
) -> dict | None:
    """The index record of this package's meta file, read from beside the archive.

    Where the sidecar is there it is checked against the package being
    recorded and then reduced to three members — file name, hash, size —
    exactly as the archive is. Nothing of its content reaches the index:
    the index answers "which versions exist", the meta file answers "what
    does this one require", and keeping the second out of the first is
    what lets a client resolve a chain by fetching one small document per
    stage instead of an index that grew with every release.

    The bytes are read once and both hashed and parsed from that copy, so
    what was checked and what the index names cannot come apart.
    """
    path = source / (file + metafile.SUFFIX)
    if not path.is_file():
        if required:
            raise SystemExit(
                f"{name} {version} has no {path.name} beside its archive, and this source "
                "records a meta file for every package it carries — publish the sidecar with "
                "the release and record the version again"
            )
        return None
    payload = path.read_bytes()
    metafile.check(
        metafile.parse(payload, what=str(path)), name=name, version=version, what=str(path)
    )
    return {
        "file": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _stated_differently(published: dict, offered: dict) -> str:
    """What a refused second record states differently, for the refusal to name.

    A second attempt at a published version is refused either way — the
    point is that the operator reading the journal can tell a re-run that
    changed nothing from a release that was re-cut under a number it had
    already used.
    """
    labels = {
        "file": "the archive",
        "sha256": "its hash",
        "size": "its size",
        META_FILE_KEY: "its meta file",
    }
    differences = [
        f"{label} is {_rendered(published.get(member))} there and "
        f"{_rendered(offered.get(member))} here"
        for member, label in labels.items()
        if published.get(member) != offered.get(member)
    ]
    return f" ({'; '.join(differences)})" if differences else ""


def _rendered(value: object) -> str:
    if isinstance(value, dict):
        return f"{value.get('file')} ({value.get('sha256')})"
    return "absent" if value is None else str(value)


def add_package(
    source: Path,
    *,
    name: str,
    version: str,
    file: str,
    sha256: str,
    size: int,
    require_meta: bool = False,
    issued: datetime,
    signers: Sequence[SigningKey],
) -> str:
    """Record one package. Returns where it landed, for the workflow log.

    The package's meta file is looked for beside the archive, under the
    archive's name plus ``.meta.json``, and recorded with it where it is
    there. *require_meta* turns its absence into a refusal, which is how
    a source whose packages all carry one — the build environment's three
    — keeps one from being published without it.

    Refuses if the version is already recorded anywhere in the source: a
    published version is immutable, meta file included, and a second
    entry under the same number is the one thing the whole
    content-addressing exists to prevent.
    """
    path = source / INDEX_FILE
    index = read_document(path)
    assert_advances(index, issued, INDEX_FILE)

    entry = {"file": file, "sha256": sha256, "size": size}
    record = _meta_file_entry(source, name=name, version=version, file=file, required=require_meta)
    if record is not None:
        entry[META_FILE_KEY] = record

    published = _entry_of(source, index, name, version)
    if published is not None:
        raise SystemExit(
            f"{name} {version} is already published in this source — a published version is "
            f"never replaced{_stated_differently(published, entry)}"
        )

    parts = list(index.get("parts") or [])
    target = covering_part(parts, version)

    if target is None:
        packages = dict(index.get("packages") or {})
        versions = dict(packages.get(name) or {})
        versions[version] = entry
        packages[name] = versions
        index["packages"] = packages
        landed = INDEX_FILE
    else:
        entries = _entries_of_part(source, target)
        versions = dict(entries.get(name) or {})
        versions[version] = entry
        rebuilt = {**entries, name: versions}
        payload = dump({"packages": rebuilt})
        filename = part_filename(target.get("covers") or {}, payload)
        (source / filename).write_bytes(payload)
        target["file"] = filename
        target["sha256"] = hashlib.sha256(payload).hexdigest()
        index["parts"] = parts
        landed = filename

    index.update(header(issued=issued, expires_days=INDEX_EXPIRY_DAYS))
    index.setdefault("parts", parts)
    write_signed(path, index, signers)
    return landed


# --------------------------------------------------------------------------- meta packages


def canonical_json(document: object) -> bytes:
    """RFC 8785 canonical JSON of a document of objects and strings.

    Written out rather than handed to :func:`json.dumps`, for one reason:
    RFC 8785 sorts object members by their **UTF-16 code units** and Python
    sorts strings by code point. The two agree for every name anybody would
    give a package and disagree above the basic multilingual plane, and a
    hash rule that is *usually* the standard is not the standard.

    Only objects and strings are accepted. Numbers are where canonical JSON
    gets hard (RFC 8785 pins them to ECMAScript's number-to-string), and no
    document hashed here needs one — refusing is cheaper than being subtly
    wrong.
    """
    if isinstance(document, str):
        return json.dumps(document, ensure_ascii=False).encode("utf-8")
    if isinstance(document, dict):
        members = sorted(document.items(), key=lambda item: str(item[0]).encode("utf-16-be"))
        body = b",".join(
            canonical_json(str(name)) + b":" + canonical_json(value) for name, value in members
        )
        return b"{" + body + b"}"
    raise SystemExit(
        f"canonical JSON here covers objects and strings; {type(document).__name__} is neither"
    )


def meta_sha256(expanded: dict) -> str:
    """The hash of a meta package: SHA-256 of its expanded document.

    The document is the entry's ``meta`` object with every leaf — a package
    name — replaced by that package's ``{"name", "sha256"}``, so the hash
    covers *which* packages the meta stands for **and** their bytes. Change
    one member's name, one member's content, or the shape of the map, and
    the hash changes.

    Both sides can compute it from the index alone: the publisher when it
    writes the entry, and a client when it reads one — which is why
    ``verify.py`` recomputes rather than believes it.
    """
    return hashlib.sha256(canonical_json(expanded)).hexdigest()


def expand_meta(source: Path, index: dict, meta: dict, version: str) -> dict:
    """*meta* with every member name replaced by its ``{name, sha256}``.

    This is where the version invariant is enforced: **a meta package at
    version V exists exactly when every one of its members exists at V, and
    the meta version is its members' version.** A member that is not
    published at this version yet is a refusal naming it, not a meta entry
    with a hole in it — the point of the meta package is that pinning it
    pins every platform's bytes.
    """
    expanded: dict[str, dict[str, dict[str, str]]] = {}
    for dimension, members in sorted(meta.items()):
        if not isinstance(members, dict) or not members:
            raise SystemExit(f"the meta dimension {dimension!r} names no packages")
        resolved: dict[str, dict[str, str]] = {}
        for key, package in sorted(members.items()):
            entry = _entry_of(source, index, str(package), version)
            if entry is None:
                raise SystemExit(
                    f"{package} {version} is not published in this source — a meta package "
                    f"is written once every member exists at its version ({dimension}={key})"
                )
            if META_KEY in entry:
                raise SystemExit(
                    f"{package} {version} is itself a meta package; a meta package names "
                    "concrete packages, so that resolving it is one step and never a search"
                )
            digest = entry.get("sha256")
            if not isinstance(digest, str):
                raise SystemExit(f"{package} {version} records no sha256")
            resolved[str(key)] = {"name": str(package), "sha256": digest}
        expanded[str(dimension)] = resolved
    if not expanded:
        raise SystemExit("a meta package needs at least one dimension")
    return expanded


def add_meta_package(
    source: Path,
    *,
    name: str,
    version: str,
    meta: dict,
    issued: datetime,
    signers: Sequence[SigningKey],
) -> str:
    """Record (or confirm) one meta package. Returns where it landed.

    A meta entry carries ``meta`` and ``sha256`` and deliberately no
    ``file`` and no ``size``: it names packages, not bytes, and a client
    resolves it to a member before it fetches anything.

    Writing it again is allowed where :func:`add_package` refuses, and only
    because it cannot mean anything different: the members are immutable, so
    a recomputation of an existing entry either produces the identical
    document — in which case nothing is written and nothing was replaced —
    or it proves that something which cannot change did. Both are useful
    answers, and the second is a refusal.
    """
    path = source / INDEX_FILE
    index = read_document(path)
    entry = {META_KEY: meta, "sha256": meta_sha256(expand_meta(source, index, meta, version))}

    published = _entry_of(source, index, name, version)
    if published is not None:
        if published != entry:
            raise SystemExit(
                f"{name} {version} is already published in this source and states something "
                "else — a published version is never replaced"
            )
        return f"{INDEX_FILE} (unchanged)"

    assert_advances(index, issued, INDEX_FILE)
    parts = list(index.get("parts") or [])
    target = covering_part(parts, version)

    if target is None:
        packages = dict(index.get("packages") or {})
        packages[name] = {**(packages.get(name) or {}), version: entry}
        index["packages"] = packages
        landed = INDEX_FILE
    else:
        entries = _entries_of_part(source, target)
        rebuilt = {**entries, name: {**(entries.get(name) or {}), version: entry}}
        payload = dump({"packages": rebuilt})
        filename = part_filename(target.get("covers") or {}, payload)
        (source / filename).write_bytes(payload)
        target["file"] = filename
        target["sha256"] = hashlib.sha256(payload).hexdigest()
        index["parts"] = parts
        landed = filename

    index.update(header(issued=issued, expires_days=INDEX_EXPIRY_DAYS))
    index.setdefault("parts", parts)
    write_signed(path, index, signers)
    return landed


def unreferenced_parts(source: Path) -> list[Path]:
    """Part files no longer named by the head — prunable once the grace period is over."""
    index = read_document(source / INDEX_FILE)
    referenced = {str(part.get("file")) for part in index.get("parts") or []}
    return sorted(
        candidate
        for candidate in source.glob("index-*.json")
        if candidate.name not in referenced and not candidate.name.endswith(SIGNATURE_SUFFIX)
    )


# --------------------------------------------------------------------------- mirrors


def write_mirrors(
    source: Path,
    *,
    mirrors: Sequence[dict[str, object]],
    issued: datetime,
    signers: Sequence[SigningKey],
) -> None:
    """Write the source's mirror list — where *this* source's data may be fetched."""
    path = source / MIRRORS_FILE
    if path.exists():
        assert_advances(read_document(path), issued, MIRRORS_FILE)
    for mirror in mirrors:
        url = str(mirror.get("url", ""))
        if not url.startswith("https://") or not url.endswith("/"):
            raise SystemExit(f"mirror {url!r} must be an https:// base URL ending in '/'")
    document = {
        **header(issued=issued, expires_days=MIRRORS_EXPIRY_DAYS),
        "mirrors": list(mirrors),
    }
    write_signed(path, document, signers)


# --------------------------------------------------------------------------- lifecycle


def init_source(
    source: Path,
    *,
    roots: Sequence[PublicKey],
    threshold: int,
    publishers: Sequence[PublicKey],
    mirrors: Sequence[dict[str, object]],
    issued: datetime,
    root_signers: Sequence[SigningKey],
    publisher_signers: Sequence[SigningKey],
) -> None:
    """Lay down a complete, empty source."""
    write_keys(
        source,
        roots=roots,
        threshold=threshold,
        publishers=publishers,
        issued=issued,
        signers=root_signers,
    )
    write_mirrors(source, mirrors=mirrors, issued=issued, signers=publisher_signers)
    index = {**header(issued=issued, expires_days=INDEX_EXPIRY_DAYS), "parts": [], "packages": {}}
    write_signed(source / INDEX_FILE, index, publisher_signers)


def refresh(source: Path, *, issued: datetime, signers: Sequence[SigningKey]) -> list[str]:
    """Renew the two publisher-signed documents. Returns what was rewritten.

    ``keys.json`` is deliberately untouched: it is root-signed, root keys
    are offline, and a refresh path that could re-sign it would mean the
    roots were not offline after all.
    """
    rewritten = []
    for filename, days in ((INDEX_FILE, INDEX_EXPIRY_DAYS), (MIRRORS_FILE, MIRRORS_EXPIRY_DAYS)):
        path = source / filename
        if not path.exists():
            continue
        document = read_document(path)
        assert_advances(document, issued, filename)
        document.update(header(issued=issued, expires_days=days))
        write_signed(path, document, signers)
        rewritten.append(filename)
    return rewritten

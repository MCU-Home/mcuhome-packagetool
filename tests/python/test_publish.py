# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The publishing side: what it writes, and what it refuses to write.

The refusals matter more than the writes. A published version is
immutable and eternal, and every guard here exists so that the one
irreversible mistake — replacing something already published — cannot be
made by a workflow at three in the morning.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from make_vectors import BASE, NOW, VECTORS, key, public

from mcuhome.packagetool.documents import parse_stamp, write_signed
from mcuhome.packagetool.source import (
    INDEX_FILE,
    KEYS_FILE,
    MIRRORS_FILE,
    add_meta_package,
    add_package,
    canonical_json,
    covering_part,
    init_source,
    meta_sha256,
    read_document,
    refresh,
    unreferenced_parts,
    write_keys,
    write_mirrors,
)
from verify import Refused, load_anchor, verify_source

ANCHOR = load_anchor(VECTORS / "anchor.json")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    roots = [key(name) for name in ("root-a", "root-b", "root-c")]
    publisher = key("publisher-1")
    place = tmp_path / "sdk"
    init_source(
        place,
        roots=[public(signer) for signer in roots],
        threshold=2,
        publishers=[public(publisher, years=1)],
        mirrors=[{"url": "https://packages.example.org/sdk/"}],
        issued=BASE,
        root_signers=roots,
        publisher_signers=[publisher],
    )
    return place


def test_a_fresh_source_verifies(source: Path) -> None:
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 0


def test_a_published_version_is_never_replaced(source: Path) -> None:
    common = {"name": "mcuhome-sdk", "file": "p.tar.zst", "sha256": "a" * 64, "size": 10}
    add_package(
        source,
        version="2.4.0",
        issued=BASE + timedelta(days=1),
        signers=[key("publisher-1")],
        **common,
    )
    with pytest.raises(SystemExit, match="already published"):
        add_package(
            source,
            version="2.4.0",
            issued=BASE + timedelta(days=2),
            signers=[key("publisher-1")],
            **{**common, "sha256": "b" * 64},
        )


def test_a_document_must_advance(source: Path) -> None:
    """The one weakness a timestamp has against a counter, closed when writing."""
    signers = [key("publisher-1")]
    add_package(
        source,
        name="mcuhome-sdk",
        version="2.4.0",
        file="p.tar.zst",
        sha256="a" * 64,
        size=10,
        issued=BASE + timedelta(days=2),
        signers=signers,
    )
    with pytest.raises(SystemExit, match="does not advance"):
        add_package(
            source,
            name="mcuhome-sdk",
            version="2.5.0",
            file="q.tar.zst",
            sha256="b" * 64,
            size=10,
            issued=BASE + timedelta(days=1),
            signers=signers,
        )


def test_an_entry_lands_in_the_part_that_covers_it(source: Path) -> None:
    from hashlib import sha256

    from mcuhome.packagetool.documents import dump, write_signed
    from mcuhome.packagetool.source import part_filename

    covers = {"versions": {"min": "1.0.0", "max": "1.9999.0"}}
    payload = dump({"packages": {}})
    filename = part_filename(covers, payload)
    (source / filename).write_bytes(payload)
    index = read_document(source / INDEX_FILE)
    index["parts"] = [{"file": filename, "sha256": sha256(payload).hexdigest(), "covers": covers}]
    write_signed(source / INDEX_FILE, index, [key("publisher-1")])

    landed = add_package(
        source,
        name="mcuhome-sdk",
        version="1.5.0",
        file="old.tar.zst",
        sha256="c" * 64,
        size=10,
        issued=BASE + timedelta(days=1),
        signers=[key("publisher-1")],
    )
    assert landed != INDEX_FILE
    # The part file was replaced by one named after its new content, so no
    # cache anywhere can serve the old bytes under the current name.
    assert landed != filename
    assert read_document(source / INDEX_FILE)["packages"] == {}
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1
    assert [path.name for path in unreferenced_parts(source)] == [filename]


def test_a_version_outside_every_part_lands_in_the_head(source: Path) -> None:
    parts = [{"file": "x", "sha256": "y", "covers": {"versions": {"min": "1.0", "max": "1.9"}}}]
    assert covering_part(parts, "2.4.0") is None
    assert covering_part(parts, "1.5.0") is parts[0]


def test_refresh_renews_the_publisher_documents_and_leaves_keys_alone(source: Path) -> None:
    before = read_document(source / KEYS_FILE)
    later = BASE + timedelta(days=20)
    assert set(refresh(source, issued=later, signers=[key("publisher-1")])) == {
        INDEX_FILE,
        MIRRORS_FILE,
    }
    # keys.json is root-signed: a CI refresh cannot touch it.
    assert read_document(source / KEYS_FILE) == before
    assert parse_stamp(read_document(source / INDEX_FILE)["expires"]) > parse_stamp(
        before["issued"]
    )
    assert verify_source(source, ANCHOR, now=later + timedelta(days=25))


def test_a_mirror_must_be_an_https_base_url(source: Path) -> None:
    for bad in ("http://mirror.example.org/sdk/", "https://mirror.example.org/sdk"):
        with pytest.raises(SystemExit, match="https"):
            write_mirrors(
                source,
                mirrors=[{"url": bad}],
                issued=BASE + timedelta(days=1),
                signers=[key("publisher-1")],
            )


def test_rotating_keys_archives_the_predecessor(source: Path) -> None:
    roots = [key(name) for name in ("root-a", "root-b", "root-c")]
    write_keys(
        source,
        roots=[public(signer) for signer in (key("root-d"), key("root-e"), key("root-f"))],
        threshold=2,
        publishers=[public(key("publisher-1"), years=1)],
        issued=BASE + timedelta(days=1),
        signers=roots[:2],
    )
    rotated = read_document(source / KEYS_FILE)
    archived = source / str(rotated["previous"])
    assert archived.is_file(), "a client offline across rotations walks this chain"
    assert archived.with_name(archived.name + ".sig").is_file()
    assert json.loads(archived.read_text())["roots"]["keys"][0]["keyid"] == public(roots[0]).keyid
    # The anchor still reaches the new set, through the archived one.
    assert verify_source(source, ANCHOR, now=NOW)


# --------------------------------------------------------------------------- meta packages

TOOLS = "mcuhome-build-tools"
MEMBERS = {
    "arch": {
        "linux-amd64": f"{TOOLS}_linux-amd64",
        "linux-arm64": f"{TOOLS}_linux-arm64",
    }
}


def _publish_members(source: Path, *, version: str = "0.1.0", only: str | None = None) -> None:
    """The concrete per-architecture packages a meta package stands for."""
    for index, (arch, package) in enumerate(sorted(MEMBERS["arch"].items())):
        if only is not None and arch != only:
            continue
        add_package(
            source,
            name=package,
            version=version,
            file=f"{package}-{version}.tar.zst",
            sha256=f"{index + 1:x}" * 64,
            size=10 + index,
            issued=BASE + timedelta(days=index + 1),
            signers=[key("publisher-1")],
        )


def test_canonical_json_is_rfc_8785() -> None:
    """The one property the frozen hash rule rests on: no whitespace, and
    members sorted by their UTF-16 code units rather than by code point.

    The two orders differ where a character above the basic multilingual
    plane meets one just below the surrogate range: as UTF-16, U+1F600
    begins with 0xD83D and therefore sorts *before* U+E000, while by code
    point it sorts after. A plain ``sorted()`` over the keys gets this pair
    the wrong way round, and a hash rule that is usually the standard is
    not the standard.
    """
    assert canonical_json({"b": "2", "a": "1"}) == b'{"a":"1","b":"2"}'
    assert canonical_json({"\U0001f600": "x", "\ue000": "y"}) == (
        '{"\U0001f600":"x","\ue000":"y"}'.encode()
    )
    with pytest.raises(SystemExit):
        canonical_json({"a": 1})


def test_the_meta_hash_is_the_documented_document() -> None:
    """Frozen rule: SHA-256 of the UTF-8 RFC 8785 canonical JSON of the meta
    object with every leaf expanded to ``{"name", "sha256"}``.

    Spelled out here as bytes, not as a call to the same function that
    produced it — a hash rule both sides recompute is only worth something
    if it is written down somewhere that fails when it changes.
    """
    from hashlib import sha256

    expanded = {
        "arch": {
            "linux-amd64": {"name": f"{TOOLS}_linux-amd64", "sha256": "1" * 64},
            "linux-arm64": {"name": f"{TOOLS}_linux-arm64", "sha256": "2" * 64},
        }
    }
    document = (
        '{"arch":{"linux-amd64":{"name":"mcuhome-build-tools_linux-amd64","sha256":"'
        + "1" * 64
        + '"},"linux-arm64":{"name":"mcuhome-build-tools_linux-arm64","sha256":"'
        + "2" * 64
        + '"}}}'
    ).encode()
    assert canonical_json(expanded) == document
    assert meta_sha256(expanded) == sha256(document).hexdigest()


def test_a_meta_package_is_recorded_and_verifies(source: Path) -> None:
    """The whole path: members published, meta written, verifier recomputes."""
    _publish_members(source)
    landed = add_meta_package(
        source,
        name=TOOLS,
        version="0.1.0",
        meta=MEMBERS,
        issued=BASE + timedelta(days=3),
        signers=[key("publisher-1")],
    )
    assert landed == INDEX_FILE

    entry = read_document(source / INDEX_FILE)["packages"][TOOLS]["0.1.0"]
    assert entry["meta"] == MEMBERS
    assert set(entry) == {"meta", "sha256"}, "a meta entry names packages, not bytes"
    assert entry["sha256"] == meta_sha256(
        {
            "arch": {
                "linux-amd64": {"name": f"{TOOLS}_linux-amd64", "sha256": "1" * 64},
                "linux-arm64": {"name": f"{TOOLS}_linux-arm64", "sha256": "2" * 64},
            }
        }
    )
    # Three entries: the two members and the meta package.
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 3


def test_a_meta_package_needs_every_member_at_its_version(source: Path) -> None:
    """The version invariant: it exists exactly when all its members do."""
    _publish_members(source, only="linux-amd64")
    with pytest.raises(SystemExit, match="linux-arm64.*not published|not published.*linux-arm64"):
        add_meta_package(
            source,
            name=TOOLS,
            version="0.1.0",
            meta=MEMBERS,
            issued=BASE + timedelta(days=3),
            signers=[key("publisher-1")],
        )
    assert TOOLS not in read_document(source / INDEX_FILE)["packages"]


def test_recording_one_meta_package_twice_changes_nothing(source: Path) -> None:
    """Its members are immutable, so a second run can only confirm it."""
    _publish_members(source)
    common = {"name": TOOLS, "version": "0.1.0", "meta": MEMBERS, "signers": [key("publisher-1")]}
    add_meta_package(source, issued=BASE + timedelta(days=3), **common)
    before = (source / INDEX_FILE).read_bytes()
    assert "unchanged" in add_meta_package(source, issued=BASE + timedelta(days=4), **common)
    assert (source / INDEX_FILE).read_bytes() == before


def test_a_meta_package_that_says_something_else_is_refused(source: Path) -> None:
    """A published version is never replaced — meta entries included."""
    _publish_members(source)
    common = {"name": TOOLS, "version": "0.1.0", "signers": [key("publisher-1")]}
    add_meta_package(source, meta=MEMBERS, issued=BASE + timedelta(days=3), **common)
    with pytest.raises(SystemExit, match="already published"):
        add_meta_package(
            source,
            meta={"arch": {"linux-amd64": f"{TOOLS}_linux-amd64"}},
            issued=BASE + timedelta(days=4),
            **common,
        )


def test_a_meta_package_may_not_point_at_another(source: Path) -> None:
    """Resolving a meta entry is one step, never a search."""
    _publish_members(source)
    add_meta_package(
        source,
        name=TOOLS,
        version="0.1.0",
        meta=MEMBERS,
        issued=BASE + timedelta(days=3),
        signers=[key("publisher-1")],
    )
    with pytest.raises(SystemExit, match="itself a meta package"):
        add_meta_package(
            source,
            name="mcuhome-build-tools-everything",
            version="0.1.0",
            meta={"family": {"tools": TOOLS}},
            issued=BASE + timedelta(days=4),
            signers=[key("publisher-1")],
        )


def test_the_verifier_recomputes_a_meta_hash_rather_than_believing_it(source: Path) -> None:
    """A meta entry can be checked without fetching anything — so it is."""
    _publish_members(source)
    add_meta_package(
        source,
        name=TOOLS,
        version="0.1.0",
        meta=MEMBERS,
        issued=BASE + timedelta(days=3),
        signers=[key("publisher-1")],
    )
    index = read_document(source / INDEX_FILE)
    index["packages"][TOOLS]["0.1.0"]["sha256"] = "f" * 64
    write_signed(source / INDEX_FILE, index, [key("publisher-1")])
    with pytest.raises(Refused, match="does not describe the packages it points at"):
        verify_source(source, ANCHOR, now=NOW)


def test_the_verifier_refuses_a_meta_entry_whose_member_is_missing(source: Path) -> None:
    """Signed or not, an entry pointing at nothing is not a package."""
    _publish_members(source, only="linux-amd64")
    index = read_document(source / INDEX_FILE)
    index["packages"][TOOLS] = {
        "0.1.0": {"meta": MEMBERS, "sha256": "f" * 64},
    }
    write_signed(source / INDEX_FILE, index, [key("publisher-1")])
    with pytest.raises(Refused, match="does not publish"):
        verify_source(source, ANCHOR, now=NOW)


def test_the_verifier_refuses_a_meta_entry_that_claims_bytes(source: Path) -> None:
    """``file`` and ``size`` would make it look fetchable, and it is not."""
    _publish_members(source)
    index = read_document(source / INDEX_FILE)
    index["packages"][TOOLS] = {
        "0.1.0": {"meta": MEMBERS, "sha256": "f" * 64, "file": "x.tar.zst", "size": 1}
    }
    write_signed(source / INDEX_FILE, index, [key("publisher-1")])
    with pytest.raises(Refused, match="names packages, not bytes"):
        verify_source(source, ANCHOR, now=NOW)


# --------------------------------------------------------------------------- meta files

SDK = "mcuhome-sdk"


def _meta(**overrides: object) -> dict:
    """A schema-1 meta file for the SDK package the helpers below record."""
    document: dict = {
        "schema": 1,
        "package": {"name": SDK, "version": "2.4.0", "architecture": None},
        "requires": {"mcuhome-build-workspace": "~=0.2.0"},
        "inputs_sha256": "9" * 64,
        "contents": {"paths": ["mcuhome/", "west.yml"]},
    }
    document.update(overrides)
    return document


def _record(
    source: Path,
    document: dict | str | None,
    *,
    name: str = SDK,
    version: str = "2.4.0",
    require_meta: bool = True,
    day: int = 1,
) -> str:
    """Write the archive's sidecar (unless *document* is None) and record it."""
    file = f"{name}-{version}.tar.zst"
    (source / file).write_bytes(b"archive")
    if document is not None:
        payload = document if isinstance(document, str) else json.dumps(document, indent=2)
        (source / f"{file}.meta.json").write_text(payload, encoding="utf-8")
    return add_package(
        source,
        name=name,
        version=version,
        file=file,
        sha256="a" * 64,
        size=7,
        require_meta=require_meta,
        issued=BASE + timedelta(days=day),
        signers=[key("publisher-1")],
    )


def test_a_meta_file_is_recorded_beside_the_archive(source: Path) -> None:
    """Name, hash and size of the sidecar — and nothing of what it says."""
    document = _meta()
    _record(source, document)

    entry = read_document(source / INDEX_FILE)["packages"][SDK]["2.4.0"]
    payload = (source / f"{SDK}-2.4.0.tar.zst.meta.json").read_bytes()
    assert entry["meta_file"] == {
        "file": f"{SDK}-2.4.0.tar.zst.meta.json",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    # The index answers "which versions exist"; the meta file answers
    # "what does this one require". Nothing of the second is in the first.
    assert set(entry) == {"file", "sha256", "size", "meta_file"}
    assert "requires" not in json.dumps(read_document(source / INDEX_FILE))
    assert "inputs_sha256" not in json.dumps(read_document(source / INDEX_FILE))
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1


def test_a_package_may_have_no_meta_file(source: Path) -> None:
    """Optional in general: the requirement is the source's, not the format's."""
    _record(source, None, require_meta=False)
    entry = read_document(source / INDEX_FILE)["packages"][SDK]["2.4.0"]
    assert set(entry) == {"file", "sha256", "size"}
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1


def test_a_source_that_requires_a_meta_file_refuses_a_package_without_one(source: Path) -> None:
    with pytest.raises(SystemExit, match="has no .*meta.json beside its archive"):
        _record(source, None)
    assert read_document(source / INDEX_FILE)["packages"] == {}


def test_a_meta_file_belongs_to_the_archive_it_lies_beside(source: Path) -> None:
    """The one mistake a hash cannot catch: the right bytes, the wrong package."""
    with pytest.raises(SystemExit, match="describes the package 'mcuhome-build-workspace'"):
        _record(
            source,
            _meta(
                package={
                    "name": "mcuhome-build-workspace",
                    "version": "2.4.0",
                    "architecture": None,
                }
            ),
        )
    with pytest.raises(SystemExit, match="describes version 2.3.0"):
        _record(source, _meta(package={"name": SDK, "version": "2.3.0", "architecture": None}))


def test_a_meta_file_of_an_unknown_schema_is_refused(source: Path) -> None:
    """Every member is read by somebody, so a format nobody knows is not read at all."""
    with pytest.raises(SystemExit, match="declares schema 2"):
        _record(source, _meta(schema=2))
    with pytest.raises(SystemExit, match="declares schema None"):
        _record(source, {k: v for k, v in _meta().items() if k != "schema"})


def test_a_meta_file_that_is_not_json_is_refused(source: Path) -> None:
    with pytest.raises(SystemExit, match="is not JSON"):
        _record(source, "{not json at all")
    with pytest.raises(SystemExit, match="is not a JSON object"):
        _record(source, "[]")


def test_the_shape_of_requires_is_checked(source: Path) -> None:
    """Names per the package grammar, optionally host-prefixed; specifiers parsed."""
    with pytest.raises(SystemExit, match="is not a PEP 440 specifier"):
        _record(source, _meta(requires={"mcuhome-build-workspace": "newest please"}))
    with pytest.raises(SystemExit, match="is not a package name"):
        _record(source, _meta(requires={"MCUHome Build Workspace": "~=0.2.0"}))
    with pytest.raises(SystemExit, match="is not a registry host"):
        _record(source, _meta(requires={"not a host/mcuhome-build-workspace": "~=0.2.0"}))
    with pytest.raises(SystemExit, match="has to be an object"):
        _record(source, _meta(requires=["mcuhome-build-workspace~=0.2.0"]))


def test_a_host_prefixed_requirement_is_accepted(source: Path) -> None:
    """A package may require one published somewhere else entirely."""
    _record(source, _meta(requires={"packages.example.org/mcuhome-build-workspace": ">=0.2,<1"}))
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1


def test_a_package_that_requires_nothing_needs_no_requires(source: Path) -> None:
    """The tools package is the end of the chain: it constrains nobody."""
    _record(source, {k: v for k, v in _meta().items() if k != "requires"})
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1


def test_the_input_hash_is_checked(source: Path) -> None:
    with pytest.raises(SystemExit, match="inputs_sha256 is 'not a hash'"):
        _record(source, _meta(inputs_sha256="not a hash"))
    with pytest.raises(SystemExit, match="inputs_sha256 is None"):
        _record(source, {k: v for k, v in _meta().items() if k != "inputs_sha256"})


def test_contents_is_passed_through_untouched(source: Path) -> None:
    """Whatever a producer records there is its own vocabulary, not ours."""
    _record(source, _meta(contents={"x-something-nobody-here-knows": {"deeply": ["nested"]}}))
    assert verify_source(source, ANCHOR, now=NOW)["entries"] == 1


def test_contents_still_has_to_be_an_object(source: Path) -> None:
    """Opaque is not shapeless: a consumer reads members off it."""
    with pytest.raises(SystemExit, match="contents is list"):
        _record(source, _meta(contents=["not an object"]))


def test_a_per_platform_package_may_be_named_by_its_family(source: Path) -> None:
    """`<family>_<platform>` composes, so the meta file may state either half."""
    concrete = f"{TOOLS}_linux-amd64"
    _record(
        source,
        _meta(package={"name": TOOLS, "version": "2.4.0", "architecture": "linux-amd64"}),
        name=concrete,
    )
    entry = read_document(source / INDEX_FILE)["packages"][concrete]["2.4.0"]
    assert entry["meta_file"]["file"] == f"{concrete}-2.4.0.tar.zst.meta.json"


def test_a_platform_suffix_and_a_declared_architecture_must_agree(source: Path) -> None:
    with pytest.raises(SystemExit, match="declares no architecture"):
        _record(
            source,
            _meta(
                package={"name": f"{TOOLS}_linux-amd64", "version": "2.4.0", "architecture": None}
            ),
            name=f"{TOOLS}_linux-amd64",
        )
    with pytest.raises(SystemExit, match="declares the architecture 'linux-arm64'"):
        _record(
            source,
            _meta(
                package={
                    "name": f"{TOOLS}_linux-amd64",
                    "version": "2.4.0",
                    "architecture": "linux-arm64",
                }
            ),
            name=f"{TOOLS}_linux-amd64",
        )


def test_a_published_meta_file_is_never_replaced_either(source: Path) -> None:
    """Immutability reaches the sidecar, and the refusal says what changed."""
    _record(source, _meta())
    (source / f"{SDK}-2.4.0.tar.zst.meta.json").write_text(
        json.dumps(_meta(requires={"mcuhome-build-workspace": "~=0.3.0"}), indent=2),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="already published.*its meta file is"):
        add_package(
            source,
            name=SDK,
            version="2.4.0",
            file=f"{SDK}-2.4.0.tar.zst",
            sha256="a" * 64,
            size=7,
            require_meta=True,
            issued=BASE + timedelta(days=2),
            signers=[key("publisher-1")],
        )


def test_the_verifier_refuses_a_malformed_meta_file_record(source: Path) -> None:
    """The record is checked like the archive's own three members."""
    _record(source, _meta())
    for broken, fragment in (
        ({"file": "x.meta.json", "sha256": "9" * 64}, "needs file, sha256 and size"),
        ({"file": "../x", "sha256": "9" * 64, "size": 1}, "is not a plain file name"),
        ({"file": "x.meta.json", "sha256": "NOT HEX", "size": 1}, "not 64 lowercase hex digits"),
        ({"file": "x.meta.json", "sha256": "9" * 64, "size": "1"}, "is not a byte count"),
    ):
        index = read_document(source / INDEX_FILE)
        index["packages"][SDK]["2.4.0"]["meta_file"] = broken
        write_signed(source / INDEX_FILE, index, [key("publisher-1")])
        with pytest.raises(Refused, match=fragment):
            verify_source(source, ANCHOR, now=NOW)


def test_the_verifier_refuses_a_meta_package_that_claims_a_meta_file(source: Path) -> None:
    """A meta package has no archive, so nothing lies beside one."""
    _publish_members(source)
    index = read_document(source / INDEX_FILE)
    index["packages"][TOOLS] = {
        "0.1.0": {
            "meta": MEMBERS,
            "sha256": "f" * 64,
            "meta_file": {"file": "x.meta.json", "sha256": "9" * 64, "size": 1},
        }
    }
    write_signed(source / INDEX_FILE, index, [key("publisher-1")])
    with pytest.raises(Refused, match="names packages, not bytes"):
        verify_source(source, ANCHOR, now=NOW)


def test_keygen_never_overwrites(tmp_path: Path) -> None:
    from mcuhome.packagetool.keys import generate

    generate(tmp_path, "root-x")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        generate(tmp_path, "root-x")

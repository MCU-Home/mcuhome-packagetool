# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The served tree: its directory of sources, and what its pages may load.

Three properties are worth a test rather than a habit.

**The operational configuration has the shape the publish pipeline
assumes.** A source declares a list of packages, and the pipeline reads
that list with jq — a malformed entry would surface as a failed publish
rather than as a failed test.

**The catalogue cannot drift.** ``sources.json`` is generated from the
publishing configuration; a source added to one and not the other would
be a directory that lies, and it would lie silently.

**The pages load nothing from another host.** A registry exists so that
integrity is checkable, and a script fetched from somebody else's server
is the one moving part able to rewrite the hashes a visitor is reading.
Linking to another site is fine; *executing* code from one is not, and
the difference is what this checks.

The pages are read from ``pages/`` and the configuration from
``deploy/mcuhome/``, which are the sources of truth for both. The copies
still lying at the repository root are the tree as it is served today,
and they are on their way out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcuhome.packagetool.catalog import CATALOG_FILE, build_catalog
from mcuhome.packagetool.source import INDEX_FILE

ROOT = Path(__file__).resolve().parents[2]
PAGES_DIR = ROOT / "pages"
PUBLISHING = ROOT / "deploy" / "mcuhome" / "publishing.json"
PAGES = ["index.html", "browser.html"]
BROWSER = PAGES_DIR / "browser.html"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return json.loads((ROOT / CATALOG_FILE).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def publishing() -> dict:
    return json.loads(PUBLISHING.read_text(encoding="utf-8"))


def test_every_source_declares_the_packages_it_carries(publishing: dict) -> None:
    """The shape the publish pipeline loops over, checked before it runs.

    A source carries a list of packages rather than one, because an
    architecture is a suffix of a package name and not a source of its
    own. The pipeline reads that list with jq while publishing; a
    malformed entry is a failed publish, and this is where it is cheap.
    """
    for name, entry in publishing["sources"].items():
        assert entry.get("repository"), f"{name}: no upstream repository"
        packages = entry.get("packages")
        assert isinstance(packages, list) and packages, f"{name}: no packages declared"
        for package in packages:
            assert isinstance(package, dict) and package.keys() >= {"name", "asset"}, (
                f"{name}: every package states a name and an asset pattern"
            )
            assert package["asset"].startswith(package["name"] + "-"), (
                f"{name}: the asset pattern of {package['name']} must name that package — "
                "the pipeline derives the version by stripping the name from the file name"
            )


def test_a_declared_meta_package_names_packages_the_source_carries(publishing: dict) -> None:
    """The shape the publish pipeline turns into ``add-meta`` arguments.

    A meta package stands for packages of its own source: one it does not
    carry could never be published together with it, and the version
    invariant — a meta package exists exactly when every member does — could
    then never be satisfied by one publish.
    """
    for name, entry in publishing["sources"].items():
        meta = entry.get("meta")
        if meta is None:
            continue
        assert meta.get("name"), f"{name}: the meta package has no name"
        carried = {package["name"] for package in entry["packages"]}
        assert meta["name"] not in carried, (
            f"{name}: {meta['name']} is both a meta package and a concrete one"
        )
        members = meta.get("members")
        assert isinstance(members, dict) and members, f"{name}: the meta package names no member"
        for dimension, mapping in members.items():
            assert isinstance(mapping, dict) and mapping, f"{name}: {dimension} names no package"
            for key, package in mapping.items():
                assert package in carried, (
                    f"{name}: {dimension}={key} names {package}, which this source does not carry"
                )


def test_the_catalogue_is_up_to_date(catalog: dict, publishing: dict) -> None:
    assert catalog == build_catalog(publishing), (
        f"{CATALOG_FILE} is stale — run: python -m mcuhome.packagetool catalog "
        f"--publishing {PUBLISHING.relative_to(ROOT)} --out {CATALOG_FILE}"
    )


def test_the_served_copies_still_match_what_they_were_taken_from() -> None:
    """One file in two places is a bug waiting for the second edit.

    While this repository is still the host, its root carries the tree as
    it is served: the pages, the catalogue, the anchor. Those copies are
    frozen — the pages under ``pages/`` and the configuration under
    ``deploy/mcuhome/`` are what gets edited — and this is what says so
    out loud if somebody edits the wrong one.
    """
    for served, source in (
        (ROOT / "index.html", PAGES_DIR / "index.html"),
        (ROOT / "browser.html", PAGES_DIR / "browser.html"),
        (ROOT / "anchor.json", ROOT / "deploy" / "mcuhome" / "anchor.json"),
    ):
        assert served.read_bytes() == source.read_bytes(), (
            f"{served.name} at the repository root is what the host serves today, and "
            f"{source.relative_to(ROOT)} is what it is taken from — they have drifted apart"
        )


def test_the_catalogue_and_the_published_sources_agree(catalog: dict) -> None:
    listed = {entry["name"] for entry in catalog["sources"]}
    published = {path.parent.name for path in ROOT.glob("*/" + INDEX_FILE)}
    assert listed == published, "a source is published but undeclared, or declared but absent"


def test_every_catalogue_entry_points_at_a_real_source(catalog: dict) -> None:
    for entry in catalog["sources"]:
        assert entry["path"] == f"{entry['name']}/"
        assert (ROOT / entry["path"] / INDEX_FILE).is_file()


def test_the_catalogue_says_it_is_not_authoritative(catalog: dict) -> None:
    """The convenience feature must not read as the trust model."""
    assert "authority" in catalog["note"] and "unsigned" in catalog["note"]


@pytest.mark.parametrize("page", PAGES)
def test_a_page_executes_nothing_from_another_host(page: str) -> None:
    html = (PAGES_DIR / page).read_text(encoding="utf-8")
    assert not re.search(r"<script[^>]+\bsrc\s*=", html, re.I), "no external script"
    assert not re.search(r"<link[^>]+\bhref\s*=\s*[\"']https?:", html, re.I), "no stylesheet"
    assert not re.search(r"<(img|iframe)[^>]+\bsrc\s*=\s*[\"']https?:", html, re.I)
    assert "@import" not in html


@pytest.mark.parametrize("page", PAGES)
def test_a_page_fetches_only_from_this_host(page: str) -> None:
    html = (PAGES_DIR / page).read_text(encoding="utf-8")
    for target in re.findall(r"""fetch\(\s*([^,)]+)""", html):
        assert "http" not in target, f"{page}: fetch({target}) leaves this host"


def test_the_inspection_page_admits_it_verifies_nothing() -> None:
    """The page is served by the host it reads, so it can attest to nothing.

    A host serving a modified package would serve a matching hash and
    this very page, with the check taken out. Showing signed-looking data
    without saying that is worse than showing nothing: it manufactures
    confidence the visitor has no way to earn.
    """
    html = BROWSER.read_text(encoding="utf-8")
    assert "cannot verify" in html or "verifies nothing" in html
    assert "not verified" in html
    # And the caveat has to reach a downloader, not only a careful reader.
    assert "showModal" in html, "every download passes the warning dialog"
    assert "askBeforeDownloading" in html


def test_no_page_sells_the_download_as_protected() -> None:
    """Wording is the whole risk here: 'signed index' reads as 'safe file'."""
    for page in PAGES:
        html = (PAGES_DIR / page).read_text(encoding="utf-8")
        assert "signed index" not in html.lower()


def test_sources_can_be_switched_off_not_only_filtered() -> None:
    html = BROWSER.read_text(encoding="utf-8")
    assert 'type: "checkbox"' in html or 'type="checkbox"' in html
    assert "state.enabled" in html


def test_the_browser_reads_the_files_the_site_actually_has() -> None:
    html = BROWSER.read_text(encoding="utf-8")
    assert CATALOG_FILE in html
    assert INDEX_FILE in html
    # A split index keeps most entries in parts; a browser that only read
    # the head would under-report and never say so.
    assert "index.parts" in html

# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""The served tree: its directory of sources, and what its pages may load.

Three properties are worth a test rather than a habit.

**The operational configuration has the shape the publish pipeline
assumes.** A source declares a list of packages, and the pipeline reads
that list with jq — a malformed entry would surface as a failed publish
rather than as a failed test.

**The catalogue describes the sources the configuration declares.**
``sources.json`` is not kept in this repository: the publish pipeline
generates it from the publishing configuration and writes it into the
served tree. What can be checked here is the document that configuration
produces — a catalogue naming a source the registry does not publish, or
pointing somewhere other than that source's own directory, would be a
directory that lies.

**The pages load nothing from another host.** A registry exists so that
integrity is checkable, and a script fetched from somebody else's server
is the one moving part able to rewrite the hashes a visitor is reading.
Linking to another site is fine; *executing* code from one is not, and
the difference is what this checks.

The pages are read from ``pages/`` and the configuration from
``deploy/mcuhome/``, which is where both are edited and what the registry
host installs from. Nothing here reads a served tree — that is
``scripts/test.d/verify-sources``, which fetches one.
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
def publishing() -> dict:
    return json.loads(PUBLISHING.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog(publishing: dict) -> dict:
    """The catalogue this registry's configuration produces.

    Built rather than read: the served copy is written by the publish
    pipeline on the registry host, and this repository holds the input to
    it, not the result.
    """
    return build_catalog(publishing)


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


def test_the_catalogue_names_the_sources_that_are_published(
    catalog: dict, publishing: dict
) -> None:
    """A catalogue entry stands for a source of this registry, and only for one.

    ``sources.json`` is what a visitor browses the registry by, so an
    entry with no source behind it is a dead link and a source with no
    entry is invisible. The pipeline writes the file from exactly this
    configuration, which is why the two can be held to each other here.
    """
    listed = [entry["name"] for entry in catalog["sources"]]
    assert len(listed) == len(set(listed)), f"{CATALOG_FILE} names a source twice"
    assert set(listed) == set(publishing["sources"]), (
        "a source is declared but not catalogued, or catalogued but not declared"
    )


def test_every_catalogue_entry_points_at_its_own_directory(catalog: dict) -> None:
    """The path is where the source's ``index.json`` is, relative to the tree root.

    A source is a directory named after it — that is the whole layout —
    and the browser resolves ``path`` against the host it was loaded
    from, so an absolute or reaching path would take a reader off the
    tree it is inspecting.
    """
    for entry in catalog["sources"]:
        assert entry["path"] == f"{entry['name']}/", (
            f"{entry['name']}: a source lives in the directory named after it"
        )
        assert INDEX_FILE not in entry["path"], "the path names the directory, not the document"


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

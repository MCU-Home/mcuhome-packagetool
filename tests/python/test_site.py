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

**The pages execute nothing from another host.** A registry exists so
that integrity is checkable, and a script fetched from somebody else's
server is the one moving part able to rewrite the hashes a visitor is
reading. Linking to another site is fine, and the registry page has to
*fetch* from another host — the mirror a visitor picked — because the
data is not on the host that serves the page. Executing code from one is
what must never happen, and the difference is what these check.

The pages are read from ``pages/`` and the configuration from
``deploy/mcuhome/``, which is where both are edited and what the registry
host installs from. The pages are installed by the deploy roles beside
the served tree and are never part of it. Nothing here reads a served
tree — that is ``scripts/test.d/verify-sources``, which fetches one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcuhome.packagetool.catalog import CATALOG_FILE, build_catalog
from mcuhome.packagetool.source import INDEX_FILE, MIRRORS_FILE

ROOT = Path(__file__).resolve().parents[2]
PAGES_DIR = ROOT / "pages"
PUBLISHING = ROOT / "deploy" / "mcuhome" / "publishing.json"
# index.html is what the bootstrap host serves: the explanation and the
# browser in one page. mirror-index.html is the page at the root of a
# mirror.
PAGES = ["index.html", "mirror-index.html"]
REGISTRY_PAGE = PAGES_DIR / "index.html"
MIRROR_PAGE = PAGES_DIR / "mirror-index.html"


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
def test_a_page_names_no_host_of_its_own_in_a_request(page: str) -> None:
    """Every request a page makes is relative, or built from a document.

    The registry page has to read a mirror, which is another host by
    definition — but *which* host is decided by the signed mirror list it
    fetched, never by a name written into the page. A hard-coded host
    would survive a mirror being dropped from a source's list, which is
    exactly the case the list exists to decide.
    """
    html = (PAGES_DIR / page).read_text(encoding="utf-8")
    for target in re.findall(r"""fetch\(\s*([^,)]+)""", html):
        assert "http" not in target, f"{page}: fetch({target}) names a host of its own"


def test_the_registry_page_reads_the_documents_the_registry_has() -> None:
    """Two hops: the catalogue and the mirror list here, the index there."""
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
    assert CATALOG_FILE in html
    assert MIRRORS_FILE in html
    assert INDEX_FILE in html
    # A split index keeps most entries in parts; a browser that only read
    # the head would under-report and never say so.
    assert "index.parts" in html


def test_the_registry_page_holds_a_mirror_url_to_its_shape() -> None:
    """A mirror list is not verified by the page, so it decides less.

    Whatever it names ends up in a request and in a link, and a URL that
    is neither https nor a directory has no business being either — a
    "javascript:" entry in a link would run in the page's own origin.
    """
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
    assert 'protocol !== "https:"' in html
    assert 'endsWith("/")' in html
    # And the same for a file name out of an index: relative, and staying
    # inside the source it came from.
    assert "isPlainFileName" in html
    assert '".."' in html


def test_the_registry_page_remembers_a_mirror_without_depending_on_it() -> None:
    """The choice is a convenience, and storage is allowed to be absent.

    Private windows, a full quota and a browser with storage switched off
    all throw on the first access. A page that let that through would be
    a blank page for the sake of remembering a dropdown.
    """
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
    start = html.index("const storage = {")
    end = html.index("\n};", start)
    helper = html[start:end]
    assert html.count("localStorage") == helper.count("localStorage") == 2, (
        "localStorage is touched in the guarded helper and nowhere else"
    )
    assert helper.count("catch (") == 2, "reading and writing are both guarded"


def test_one_mirror_choice_carries_to_the_other_sources() -> None:
    """Mirror lists are per source, so the same mirror is the same origin.

    Picking one for a source adopts it for the others that have it — and
    only for those where nobody picked one, which is what the deliberate
    flag on a stored choice is for.
    """
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
    assert "chooseMirror" in html
    assert "explicit" in html
    assert "entry.origin === mirror.origin" in html


def test_the_registry_page_admits_it_verifies_nothing() -> None:
    """The page is served by the host that names the mirrors it reads.

    A host serving a modified package would serve a matching hash and
    this very page, with the check taken out. Showing signed-looking data
    without saying that is worse than showing nothing: it manufactures
    confidence the visitor has no way to earn.
    """
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
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
    html = REGISTRY_PAGE.read_text(encoding="utf-8")
    assert 'type: "checkbox"' in html or 'type="checkbox"' in html
    assert "state.enabled" in html


def test_the_mirror_page_lists_this_mirror_and_points_at_the_registry() -> None:
    """A mirror's page says what it is, and where the trust decision is made.

    It lists the sources this host carries out of its own catalogue, and
    sends anyone asking which mirrors a source has to the bootstrap host,
    because that answer is a signed document and not a page's to give.
    """
    html = MIRROR_PAGE.read_text(encoding="utf-8")
    assert CATALOG_FILE in html
    assert "https://packages.mcuhome.org/" in html
    assert "verify.py" in html

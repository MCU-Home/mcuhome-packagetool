# mcuhome-packagetool

`mcuhome-packagetool` is MCUHome's package tooling: the tool that publishes
a registry, the pages that registry serves, the material that sets a server
up to host one, and the reference verifier clients check it with. It is
where a released SDK package becomes something a build pins by hash and
signature, not by trusting a URL.

## What this repository holds

- `mcuhome/packagetool/` — the publishing tool: it lays a source down, records a package, renews the publisher-signed documents and reports how much validity is left.
- `verify.py` — the normative reference verifier, standalone so it can be copied next to a mirrored source and run with the standard library and `cryptography`.
- `pages/` — the pages a registry serves: a front page and the browsable index of everything published. They are part of what is deployed, not part of this repository's own presentation.
- `deploy/` — the Ansible roles that turn a server into a registry host, and under `deploy/mcuhome/` the settings, source declarations and trust anchor the public MCUHome registry is run with.
- `sdk/`, `build-workspace/`, `build-tools/` — the published sources: the SDK a build compiles from, and the two kinds of package the build environment is made of. Each is a self-contained directory carrying its own root keys, mirror list, signed index and package files.
- `sources.json` — the unsigned directory of sources, generated from the publishing configuration: a listing for a human to browse, with no authority over any source.

### The copies at the repository root

Until the registry finishes moving to its own host, this repository is
also still the site behind `packages.mcuhome.org`, and what that site
serves is what lies at its root: `CNAME`, `.nojekyll`, `index.html`,
`browser.html`, `sources.json`, `anchor.json` and the three source
directories.

Two of those exist twice on purpose. `pages/` and `deploy/mcuhome/` are
where the pages and the anchor are edited; the copies at the root are
frozen serving artefacts, and a test compares them so the two cannot
drift apart. They are removed once the served tree comes from the new
host rather than from this repository.

## Using it

A source is a plain directory, and everything needed to trust it is inside:
the packages, the key set that signs the index, and the mirror list. Verifying
one is therefore local work on bytes you already have — a mirror, a cache or a
checkout — against a root anchor supplied out of band:

```sh
python verify.py sdk --anchor deploy/mcuhome/anchor.json
```

Operating a source is the other half: `python -m mcuhome.packagetool` lays a
source down, records a package or a meta package in it, renews its
signatures and reports how long each document is still valid.

## How it fits into MCUHome

The packages served here are the release archives of
[mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) — the SDK itself and
the two kinds of package its build environment is made of — pulled from a
tagged release and recorded into their source with the tool in this
repository. A source's `index.json` names each package with its size and
sha256, which is what
[mcuhome-workbench](https://github.com/mcu-home/mcuhome-workbench) resolves an
SDK pin against and what
[mcuhome-buildserver](https://github.com/mcu-home/mcuhome-buildserver) finds a
package's bytes by. Because a source is self-contained, a copy of one is worth
exactly as much as the original.

## Layout

| Path | Purpose |
|---|---|
| `mcuhome/` | The publishing tool — keys, signed documents, sources, catalogue |
| `pages/` | The pages a registry serves: the front page and the browsable index |
| `deploy/` | Ansible roles for hosting a packagetool registry; details in `deploy/README.md` |
| `deploy/mcuhome/` | How the public MCUHome registry is run: storage and serving settings, `publishing.json`, `anchor.json` |
| `tests/` | The suite, and the fixed source directories it verifies, one per outcome |
| `deploy/tests/` | Functional tests for the deploy material, against real filesystems |
| `sdk/` | The published MCUHome SDK source: signed documents and package files |
| `build-workspace/` | The build environment's source-world package: same shape, own keys |
| `build-tools/` | The build environment's host-tool packages, one per platform, plus the meta package standing for the family |
| `.github/` | The refresh and check workflows |

## Development — how to work on this repository

This repository has its own virtual environment in `.venv/`; nothing is
installed into the system Python or into another repository's environment.
`bin/` holds the user-facing entry points, `scripts/` the development
tooling: `scripts/test` and `scripts/lint` dispatch the checks — `all` runs
every one, `list` names them, `<name>` runs one — and each check is its own
wrapper in `scripts/test.d/` or `scripts/lint.d/`. The wrappers select
`.venv` themselves (never activate one by hand) and are exactly what CI
runs, one job per check.

Needs Python ≥3.13; beyond `cryptography` and `packaging` it uses the
standard library.

```sh
python3 -m venv .venv && .venv/bin/pip install -e . --group dev
```

Four checks need more than that:

| Check | Needs |
|---|---|
| `test catalog` | regenerates `sources.json` from `deploy/mcuhome/publishing.json` into a temporary file and compares — nothing extra |
| `test verify-sources` | `jq` and network access: it verifies every published document against `deploy/mcuhome/anchor.json` |
| `test registry-snapshot` | root, `btrfs-progs` and loop devices — run it as `sudo scripts/test registry-snapshot` |
| `test registry-storage` | root, loop devices, `btrfs-progs`, `e2fsprogs`, `lvm2` and `fdisk` — run it as `sudo scripts/test registry-storage` |

Both deploy tests build their filesystems in loopback images they create
and throw away again; the storage one additionally runs in a mount
namespace of its own, so the mounts and the fstab entry it makes never
reach the machine it runs on. Without root they fail and say so rather
than reporting a pass, because a check that skipped itself has proved
nothing — which also means `scripts/test all` wants root:

```sh
sudo scripts/test all
scripts/lint all
```

The rules that hold across every MCUHome repository — coding standards,
commits, licensing — are in the organization's
[contributing guide](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## Configuration

`deploy/mcuhome/publishing.json` declares each source of the MCUHome
registry: which upstream repository feeds it, which packages it carries — a
list of `name` and release-asset `asset` pairs — optionally the meta package
that stands for them, and the title and description the catalogue publishes
for it. Adding a source is an entry in that file, not a change to a
workflow.

It sits with the rest of the instance configuration rather than at the
repository root, because it describes one particular registry and the tool
describes none: every command is told the paths it works on, and a registry
of your own is a `deploy/mcuhome/` of your own.

A source carries a *list* of packages because an architecture is a suffix of
a package name rather than a source of its own: `mcuhome-build-tools_linux-amd64`
and `mcuhome-build-tools_linux-arm64` live in one source, and whoever
resolves a build picks the package that fits the host it runs on. The `_` is
what makes that suffix readable — a package name is otherwise lowercase
alphanumerics and `-`, so the first `_` splits the family from the platform.
Publishing that source is one act for the whole list: a build that pins a
version has to find every platform of it at once.

## Meta packages

An index entry usually names a file and its bytes. A **meta package** names
other packages instead: `mcuhome-build-tools` maps each architecture to the
concrete package for it, and carries a `sha256` derived from those packages'
hashes rather than from any file of its own.

```json
"mcuhome-build-tools": {
  "0.1.0": {
    "meta": {"arch": {"linux-amd64": "mcuhome-build-tools_linux-amd64",
                      "linux-arm64": "mcuhome-build-tools_linux-arm64"}},
    "sha256": "…"
  }
}
```

It exists so that one build context can be pinned once and still run on
either architecture: whoever executes it resolves its own coordinate through
the entry, and the meta hash still pins every member's bytes. `arch` is
merely the first dimension — the `meta` object is a map of dimension names
to maps of coordinate to package name, and nothing in the tool or the
verifier knows what `arch` means.

Three rules hold, and both sides check them from the index alone:

- **The hash.** It is the SHA-256 of the UTF-8 [RFC 8785][jcs] canonical
  JSON of the `meta` object with every leaf replaced by that package's
  `{"name", "sha256"}`. `verify.py` recomputes it and refuses on a
  mismatch — a meta entry is never believed.
- **The version invariant.** A meta package at version V exists exactly when
  every one of its members exists at V; the meta version *is* its members'
  version.
- **No bytes.** A meta entry carries neither `file` nor `size`, because
  there is nothing to fetch until it has been resolved.

The publish pipeline writes the entry automatically, right after the members
it points at; `python -m mcuhome.packagetool add-meta` is the same act by
hand.

[jcs]: https://www.rfc-editor.org/rfc/rfc8785

## Sidecars

A package file is served with its `.sha256`, and with any further sidecar
the build wrote next to the archive — for the build-environment packages
that is `<archive>.build-environment.json`, the environment's
self-description. It is served beside the archive rather than only inside
it because whoever provisions the environment reads it *before* it unpacks
anything.

The publisher key the tool signs with comes from `--publisher-key` or from
`MCUHOME_PUBLISHER_KEYS`, which holds its PEM. Each source has its own
publisher key; a signature by a key the source's `keys.json` does not list
does not count, so one variable may hold every publisher key at once.

## Security

A package is trusted by signature and hash, never by the host or the mirror
that served it: root keys sign `keys.json` and stay offline, the publisher key
signs `index.json` and `mirrors.json` from a protected environment, and a
verdict is reached against an anchor the client already holds. Nothing
published is deleted — a superseded part file past its grace period is the
only artefact the tool will remove. Report a suspected key or signature
compromise through
[the organization's security policy](https://github.com/mcu-home/.github/blob/main/SECURITY.md).

## Documentation

- [`verify.py`](verify.py) — what a client must check, in executable form
- [`mcuhome/packagetool/`](mcuhome/packagetool/) — the publishing tool, documented module by module
- [`deploy/`](deploy/) — hosting a registry: the roles, the tree they lay down, and how a publish is made atomic
- [packages.mcuhome.org](https://packages.mcuhome.org) — the sources this repository serves
- [The MCUHome organization](https://github.com/mcu-home) — the other repositories of the project

## Contributing and support

Bug reports and questions go to this repository's
[issue tracker](https://github.com/mcu-home/mcuhome-packagetool/issues).
How a change is submitted is described in
[the organization's contributing rules](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## License

Apache License 2.0, see [`LICENSE`](LICENSE).

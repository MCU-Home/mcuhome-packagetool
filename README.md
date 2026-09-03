# mcuhome-packagetool

`mcuhome-packagetool` is MCUHome's package host: the static site at
`packages.mcuhome.org`, the tool that publishes into it, and the reference
verifier clients check it with. It is where a released SDK package becomes
something a build pins by hash and signature, not by trusting a URL.

## What this repository holds

- `sdk/`, `build-workspace/`, `build-tools/` — the published sources: the SDK a build compiles from, and the two kinds of package the build environment is made of. Each is a self-contained directory carrying its own root keys, mirror list, signed index and package files.
- `verify.py` — the normative reference verifier, standalone so it can be copied next to a mirrored source and run with the standard library and `cryptography`.
- `mcuhome/packagetool/` — the publishing tool: it lays a source down, records a package, renews the publisher-signed documents and reports how much validity is left.
- `anchor.json` — the root key set, published so a client can compare it against the anchor it already holds; fetching it at verification time is not verification.
- The pages the host serves, and `sources.json`, the unsigned directory of sources generated from `publishing.json` — a listing for a human to browse, with no authority over any source.

## Using it

A source is a plain directory, and everything needed to trust it is inside:
the packages, the key set that signs the index, and the mirror list. Verifying
one is therefore local work on bytes you already have — a mirror, a cache or a
checkout — against a root anchor supplied out of band:

```sh
python verify.py sdk --anchor anchor.json
```

Operating a source is the other half: `python -m mcuhome.packagetool` lays a
source down, records a package or a meta package in it, renews its
signatures and reports how long each document is still valid.

## How it fits into MCUHome

The packages served here are the release archives of
[mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) — the SDK itself and
the two kinds of package its build environment is made of — pulled from a
tagged release and recorded into their source by the publish workflow in this
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
| `sdk/` | The published MCUHome SDK source: signed documents and package files |
| `build-workspace/` | The build environment's source-world package: same shape, own keys |
| `build-tools/` | The build environment's host-tool packages, one per platform, plus the meta package standing for the family |
| `mcuhome/` | The publishing tool — keys, signed documents, sources, catalogue |
| `tests/` | The suite, and the fixed source directories it verifies, one per outcome |
| `.github/` | The publish, refresh and check workflows |

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
standard library. `scripts/test catalog` checks the committed `sources.json`
against `publishing.json`; `scripts/test verify-sources` additionally needs
`jq` and network access to check every published document against
`anchor.json`.

```sh
python3 -m venv .venv && .venv/bin/pip install -e . --group dev
```

```sh
scripts/test all
scripts/lint all
```

The rules that hold across every MCUHome repository — coding standards,
commits, licensing — are in the organization's
[contributing guide](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## Configuration

`publishing.json` declares each source: which upstream repository feeds it,
which packages it carries — a list of `name` and release-asset `asset` pairs
— optionally the meta package that stands for them, and the title and
description the catalogue publishes for it. Adding a source is an entry in
that file, not a change to a workflow.

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

The publish workflow writes the entry automatically, right after the members
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
signs `index.json` and `mirrors.json` from a protected CI environment, and a
verdict is reached against an anchor the client already holds. Nothing
published is deleted — a superseded part file past its grace period is the
only artefact the tool will remove. Report a suspected key or signature
compromise through
[the organization's security policy](https://github.com/mcu-home/.github/blob/main/SECURITY.md).

## Documentation

- [`verify.py`](verify.py) — what a client must check, in executable form
- [`mcuhome/packagetool/`](mcuhome/packagetool/) — the publishing tool, documented module by module
- [packages.mcuhome.org](https://packages.mcuhome.org) — the sources this repository serves
- [The MCUHome organization](https://github.com/mcu-home) — the other repositories of the project

## Contributing and support

Bug reports and questions go to this repository's
[issue tracker](https://github.com/mcu-home/mcuhome-packagetool/issues).
How a change is submitted is described in
[the organization's contributing rules](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## License

Apache License 2.0, see [`LICENSE`](LICENSE).

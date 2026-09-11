# mcuhome-packagetool

`mcuhome-packagetool` is MCUHome's package tooling: the tool that publishes
a registry, the pages a registry host serves, the material that sets such a
server up, and the reference verifier clients check it with. It is
where a released SDK package becomes something a build pins by hash and
signature, not by trusting a URL.

## What this repository holds

- `mcuhome/packagetool/` — the publishing tool: it lays a source down, records a package, renews the publisher-signed documents and reports how much validity is left. `mcuhome/packagetool/verify.py` in it is the normative reference verifier: the rules a client must reach the same verdicts by, importable so that other MCUHome tools run them rather than transcribe them.
- `verify.py` — the command line over that verifier. It decides nothing itself; run it from a checkout whose `.venv` has this repository installed.
- `pages/` — the two pages a registry host serves: `index.html`, the page of the bootstrap host, which explains the registry and browses it over the mirrors each source names; and `mirror-index.html`, the page at the root of a mirror. They are installed by the deploy roles beside the served tree, never into it, and are not this repository's own presentation.
- `deploy/` — the Ansible roles that turn a server into a registry host, and under `deploy/mcuhome/` the settings, source declarations and trust anchor the public MCUHome registry is run with.

### What it does not hold

The registry itself. `packages.mcuhome.org` is served from its own host:
the signed documents, the package files and the generated `sources.json`
live in a filesystem tree there, published in one atomic step per
release and mirrorable in full over plain rsync. This repository holds
what produces that tree and what checks it — a published package is
never in a git history, which is what lets one be larger than a
repository has any business carrying.

## Using it

A source is a plain directory, and everything needed to trust it is inside:
the packages, the key set that signs the index, and the mirror list. Verifying
one is therefore local work on bytes you already have — a mirror or a cache —
against a root anchor supplied out of band:

```sh
rsync -a rsync://mirror-1.packages.mcuhome.org/registry/sdk/ sdk/
python verify.py sdk --anchor deploy/mcuhome/anchor.json
```

The verifier fetches nothing itself, on purpose: what it says holds for the
bytes on disk, so pulling them and checking them stay two separable acts and
a mirror is worth exactly as much as the origin.

Operating a source is the other half: `python -m mcuhome.packagetool` lays a
source down, records a package or a meta package in it, renews its
signatures and reports how long each document is still valid.

## How it fits into MCUHome

The packages the registry carries are the release archives of
[mcuhome-sdk](https://github.com/mcu-home/mcuhome-sdk) — the SDK itself and
the two kinds of package its build environment is made of — pulled from a
tagged release by the registry host and recorded into their source with the
tool in this repository. A source's `index.json` names each package with its size and
sha256, which is what
[mcuhome-workbench](https://github.com/mcu-home/mcuhome-workbench) resolves an
SDK pin against and what
[mcuhome-buildserver](https://github.com/mcu-home/mcuhome-buildserver) finds a
package's bytes by. Because a source is self-contained, a copy of one is worth
exactly as much as the original.

Those three are three **release lines of one repository**, each versioned on
its own and tagged for its line:

| Tag | Releases | Source |
|---|---|---|
| `v<version>` | `mcuhome-sdk` | `sdk` |
| `workspace-v<version>` | `mcuhome-build-workspace` | `build-workspace` |
| `tools-v<version>` | `mcuhome-build-tools_<os>-<arch>`, one asset per platform | `build-tools` |

No line takes another's version, so two of them will sooner or later publish
the same number. That is why each source declares which tags are its own
rather than recognising an archive by its file name.

## Layout

| Path | Purpose |
|---|---|
| `mcuhome/` | The publishing tool — keys, signed documents, sources, catalogue |
| `pages/` | The two pages a registry host serves: the bootstrap host's page and a mirror's root page |
| `deploy/` | Ansible roles for hosting a packagetool registry; details in `deploy/README.md` |
| `deploy/mcuhome/` | How the public MCUHome registry is run: storage and serving settings, `publishing.json`, `anchor.json` |
| `tests/` | The suite, and the fixed source directories it verifies, one per outcome |
| `deploy/tests/` | Functional tests for the deploy material, against real filesystems |
| `.github/` | The check workflows: one job per lint and test wrapper |

## Development — how to work on this repository

This repository has its own virtual environment in `.venv/`; nothing is
installed into the system Python or into another repository's environment.
There is no `bin/`: the tool is invoked as `python -m mcuhome.packagetool`
and the verifier as `python verify.py`. `scripts/` holds the development
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

Six checks need more than that, four of them deploy tests that need root:

| Check | Needs |
|---|---|
| `test catalog` | `jq`: it generates `sources.json` from `deploy/mcuhome/publishing.json` into a temporary file and checks it against that configuration — offline, nothing else |
| `test verify-sources` | `jq`, `curl` and network access: it asks `packages.mcuhome.org` for each source's mirror list, fetches the signed documents from every mirror named there, and verifies them against `deploy/mcuhome/anchor.json`. It checks the live registry, so it fails while that registry is unreachable |
| `test registry-snapshot` | root, `btrfs-progs` and loop devices — run it as `sudo scripts/test registry-snapshot` |
| `test registry-storage` | root, loop devices, `btrfs-progs`, `e2fsprogs`, `lvm2` and `fdisk` — run it as `sudo scripts/test registry-storage` |
| `test mirror-sync` | root, `btrfs-progs`, loop devices and `jq` — run it as `sudo scripts/test mirror-sync` |
| `test registry-publish` | root, loop devices, `btrfs-progs`, `jq`, `curl`, `useradd`, `unshare` and this repository installed into a Python (its own `.venv` unless `$PACKAGETOOL_PYTHON` says otherwise) — run it as `sudo scripts/test registry-publish` |

All four deploy tests build their filesystems in loopback images they
create and throw away again; `registry-storage` and `registry-publish`
additionally run in a mount namespace of their own, so the mounts, the
fstab entry and the throwaway system account they make never reach the
machine they run on. Without root they fail and say so rather than
reporting a pass, because a check that skipped itself has proved
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
registry: which upstream repository feeds it, which release tags are its own
(`tag`, a glob — `v*`, `workspace-v*`, `tools-v*`), which packages it carries
— a list of `name` and release-asset `asset` pairs — whether every one of
them has to bring its meta file (`meta_file_required`), optionally the meta
package that stands for them, and the title and description the catalogue
publishes for it. Adding a source is an entry in that file, not a change to a
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

## The index

`index.json` is the signed list of what a source publishes: one entry per
package and version, and nothing else about them.

```json
"mcuhome-build-workspace": {
  "0.2.0": {
    "file": "mcuhome-build-workspace-0.2.0.tar.zst",
    "sha256": "996a117c…",
    "size": 584893895,
    "meta_file": {
      "file": "mcuhome-build-workspace-0.2.0.tar.zst.meta.json",
      "sha256": "5338eaab…",
      "size": 412
    }
  }
}
```

`file`, `sha256` and `size` are the archive. `meta_file` is the sidecar
served beside it, recorded by exactly those same three members and **never
by its content**: the index answers *which versions exist*, the meta file
answers *what one of them requires*, and keeping the second out of the first
is what keeps the index small enough to be fetched whole by everybody.

So resolving a chain is two steps and one small fetch per stage:

1. **Newest satisfying.** Take the version list the entry map already gives
   you, keep the versions your constraint accepts, take the highest. No
   fetch: the index is in hand.
2. **What that version requires.** Fetch the one `meta_file` named by the
   entry you picked, check its bytes against the `sha256` recorded there,
   and read `requires` out of it. That constraint is the next stage's input,
   and step 1 begins again.

Archives are fetched for what a build actually uses, and for nothing else.

The entry may grow further members later; a client that does not know one
ignores it, exactly as it ignores anything else a later generation of the
format added. `meta_file` is optional in the format — a source may carry
packages that have none — and required per source by the publishing
configuration, which is where "every package of *this* registry brings one"
belongs.

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
  `{"name", "sha256"}`. The verifier recomputes it and refuses on a
  mismatch — a meta entry is never believed.
- **The version invariant.** A meta package at version V exists exactly when
  every one of its members exists at V; the meta version *is* its members'
  version.
- **No bytes.** A meta entry carries neither `file` nor `size` nor
  `meta_file`, because there is nothing to fetch until it has been resolved.

That last rule is also the answer to "where is the meta file of a family?".
There is none, and there could not be: a meta package has no archive, so
nothing lies beside one, and its members' meta files differ per platform
anyway. Whoever wants what a platform requires resolves the family entry to
that platform's package first and reads the `meta_file` of *that* entry —
one coordinate, one member, one sidecar, and exactly the same two steps as
any other package.

The publish pipeline writes the entry automatically, right after the members
it points at; `python -m mcuhome.packagetool add-meta` is the same act by
hand.

[jcs]: https://www.rfc-editor.org/rfc/rfc8785

## Sidecars

Two files are served beside a package archive, under the archive's own name:

- `<archive>.sha256` — the checksum the build published with it. It is what
  the pipeline holds the downloaded bytes against before anything is
  recorded, and it is served on because a mirror's copy can be checked
  without the index.
- `<archive>.meta.json` — what the package is and what it requires. Served
  beside the archive rather than only inside it because whoever resolves a
  chain reads it *before* it fetches anything, and recorded in the index as
  `meta_file` so that its bytes are pinned by the same signature the archive
  is.

There used to be a third, `<archive>.build-environment.json`, the build
environment's self-description. It is **retired**: the meta file supersedes
it and says the same things in one place for every package rather than for
one family of them. Nothing publishes it any more and the pipeline no longer
looks for it.

The meta file is one JSON object of schema 1 — here the build workspace,
the middle link of the chain:

```json
{
  "schema": 1,
  "package": {"name": "mcuhome-build-workspace", "version": "0.2.0",
              "architecture": null},
  "requires": {"mcuhome-build-tools": "~=0.1.0"},
  "inputs_sha256": "9f2c…",
  "contents": {}
}
```

The chain runs one way and ends: the SDK requires a build workspace, a build
workspace requires build tools, and **the tools require nothing** — a tools
package carries no `requires` at all. Its `package` block is the one that
names a platform: `{"name": "mcuhome-build-tools", "version": "0.1.0",
"architecture": "linux-amd64"}`.

| Member | Checked when it is recorded |
|---|---|
| `schema` | is 1; anything else is refused rather than guessed at |
| `package` | `name` per the package-name grammar, `version` a PEP 440 version, and `architecture` — a **required key**: `null` where the package is the same on every platform, the platform string (`"linux-amd64"`) where it is not. All of it is held against the archive it lies beside |
| `requires` | absent, or package name to PEP 440 specifier; a name may be prefixed with the registry host it is published on (`packages.example.org/mcuhome-build-tools`) |
| `inputs_sha256` | 64 lowercase hex digits |
| `contents` | an object, and otherwise **untouched** |

`requires` maps `[<host>/]<family name>` to a PEP 440 specifier and nothing
else. Four consequences worth stating, because they are what a producer gets
wrong:

- The key is the **family** name (`mcuhome-build-tools`), not one platform's
  package: a requirement is about a release, and its platforms are resolved
  by whoever executes the build.
- **Exact byte pins are not expressible here.** There is no `@sha256:` form
  in a `requires` value — a package says which *versions* of the next one it
  works with, and pinning one particular archive is a decision about one
  device, made in that device's own `sources.` override.
- An **empty specifier** (`""`) parses and means "any version". Producers
  should not emit one: a constraint that accepts everything is a statement
  nobody can act on, and leaving `requires` out says the same thing more
  honestly.
- `requires: {}` and an absent `requires` mean the same: this package
  constrains nothing.

`contents` is the producing side's own vocabulary — a workspace package's
project revisions, a tools package's tool versions — and is deliberately
opaque here: a registry that validated it would need changing whenever a
producer learned a new word, and it could not tell whether what it read was
true either way.

A package built per platform is published as `<family>_<platform>`, and its
meta file may state either the concrete name or the family plus the
architecture: the two compose into one name, so they say the same thing.
Disagreeing with the archive is the refusal — a meta file beside the wrong
package is the one mistake a hash cannot catch, because the bytes are
exactly the ones somebody meant to publish.

`add` records the sidecar where it finds one beside the archive;
`add --require-meta` refuses where there is none, and the publishing
configuration turns that on per source.

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

- [`mcuhome/packagetool/verify.py`](mcuhome/packagetool/verify.py) — what a client must check, in executable form; [`verify.py`](verify.py) is the command line over it
- [`mcuhome/packagetool/`](mcuhome/packagetool/) — the publishing tool, documented module by module
- [`deploy/`](deploy/) — hosting a registry: the roles, the tree they lay down, and how a publish is made atomic
- [packages.mcuhome.org](https://packages.mcuhome.org/) — the bootstrap host: where a client asks which mirrors a source has, and the page that explains the registry and browses what is published
- [mirror-1.packages.mcuhome.org](https://mirror-1.packages.mcuhome.org/) — a mirror: the full tree, browsable directory by directory, and the rsync export to copy it from
- [The MCUHome organization](https://github.com/mcu-home) — the other repositories of the project

## Contributing and support

Bug reports and questions go to this repository's
[issue tracker](https://github.com/mcu-home/mcuhome-packagetool/issues).
How a change is submitted is described in
[the organization's contributing rules](https://github.com/mcu-home/.github/blob/main/CONTRIBUTING.md).

## License

Apache License 2.0, see [`LICENSE`](LICENSE).

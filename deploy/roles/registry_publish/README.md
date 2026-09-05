# registry_publish

Installs the publish pipeline: the tool in a virtual environment of its
own, the account that holds the publisher key, one command that takes the
registry from *a new release exists upstream* to *the mirror serves it*,
and two systemd units that run it.

The role installs. It never publishes — a configuration run that
published would turn every configuration run into a release.

## The pull model

Nothing upstream pushes into this registry, and nothing outside the
server holds a key that could. A run asks the forge which releases exist,
fetches the assets it has not recorded yet over plain HTTPS, checks them
against the checksum the build published beside them, records them in the
source, verifies the result with the reference verifier, and only then
takes a snapshot and switches the served tree onto it.

One run covers every source at once. Each source is snapshotted on its
own and only if its content changed, but the served tree is composed once
per run out of one snapshot per source: a release set that spans several
sources — an SDK, its build tools, its workspace — becomes one tree and
one flip, because a client that pins a version has to find every part of
it at the same moment.

The command is idempotent. A version already recorded is never recorded
again (and the tool would refuse: a published version is immutable), a
tree file that already has the right content is not rewritten, and
`packagetool-snapshot` composes no tree and generates no dumps when it
finds nothing to publish.

The snapshot and dump commands run at the end of every non-dry run,
whether or not that particular run recorded anything itself — they decide
from the working tree's own state, not from what this run remembers doing.
That is what makes a run that was interrupted after recording a package
but before publishing self-healing: the next run, changed or not, reaches
the same tail and the snapshot command finds and publishes what is
already there, instead of that content waiting for the weekly refresh to
happen to touch the same source.

## Who runs what

| Phase | Runs as | Why |
|---|---|---|
| discover, download, checksum | the publishing account | it talks to the network and handles bytes nobody has checked yet |
| record and sign | the publishing account | it is the only identity that can read the publisher key |
| pages, extra files, `sources.json` | the publishing account | it owns the working tree |
| verify | the publishing account | reading |
| snapshots, the composed tree and the docroot switch | root | creating read-only btrfs snapshots needs `CAP_SYS_ADMIN` |
| mirror dumps | root | `btrfs send` needs it too |

The unit starts as root and the command drops out of it for everything
above except the last two lines. The other way round — running
unprivileged and asking for the privileged parts back through `sudo` or
polkit — would mean handing the account that reads the publisher key a
documented way to become root, which is the thing being avoided.

The publishing account is a system account with no login, no shell and no
home worth having. It owns the working tree and the key directory and
nothing else. The key directory is mode `0500` and the role puts nothing
into it: keys are not deployment material, and a role that fetched them
would be the wrong place to decide who may.

## What it installs

```
/opt/packagetool/checkout        the tool, at the configured revision, root-owned
/opt/packagetool/venv            its virtual environment
/opt/packagetool/installed-revision   the commit that is running
/usr/local/sbin/packagetool-publish   the command
/etc/packagetool/publish.conf         its settings
/etc/packagetool/publisher-keys/      where the keys are expected
/etc/systemd/system/packagetool-publish.service   one publish, by hand
/etc/systemd/system/packagetool-publish.timer     installed, off
/etc/systemd/system/packagetool-refresh.service   renew the signatures
/etc/systemd/system/packagetool-refresh.timer     weekly, on
<registry root>/staging          where a fetched asset waits for its checksum
```

The checkout is root-owned on purpose: the account that runs the tool
cannot change the tool it runs, and a publish that could rewrite its own
verifier would verify nothing. The staging area sits beside the working
tree rather than inside it, so nothing half fetched can end up in a
snapshot, and on the same filesystem, so putting a checked package into a
source is a reflink rather than a second copy of half a gigabyte.

## Publishing and refreshing

Publishing:

```
sudo systemctl start packagetool-publish.service
journalctl -u packagetool-publish.service -e
```

`systemctl start` on a oneshot blocks until the run is over and fails
when the run failed, so the manual path and the timer path are the same
path and both can have a failure notification hung off them.

The timer for it is installed and switched off. Publishing is a
deliberate act; turning it on later is `registry_publish_timer_enabled`
and nothing else. Its `Persistent=` stays `false`
(`registry_publish_timer_persistent`) while it is off: a missed run of a
deliberate act must not fire on its own at the next boot the moment
somebody enables the timer later.

Refreshing is the same command with `--refresh`: it renews the
publisher-signed documents instead of looking for new releases, then
verifies, snapshots and dumps exactly as a publish does. That one runs on
a timer from the start, because expiry is what bounds a frozen mirror: a
mirror serving a stale but validly signed copy is caught because the copy
runs out, and re-signing is the price of that. Its timer is `Persistent=`
(`registry_publish_refresh_timer_persistent`, default `true`) so a server
that was off over the scheduled moment catches up at the next boot
instead of waiting another week.

Both units end with a validity report. A document close to running out
makes the run fail *after* everything else has succeeded — the one
document a refresh cannot renew is the root-signed key set, which needs
an offline ceremony arranged weeks ahead, and a warning nobody is paged
about is a warning nobody reads. Set
`registry_publish_status_warning_is_failure` to `false` if you would
rather read it in the journal.

## A source that is not there yet

A source that has not been laid down is reported and skipped, not an
error. Its directory exists from the moment the storage was set up —
every source is a btrfs subvolume of its own and those are created there,
not here — so what decides the question is whether the two documents a
source is made of, `keys.json` and `index.json`, are in it. Writing them
needs the offline root keys, which are deliberately not on this machine,
so an empty source is a state the command has to survive rather than fix.
Everything else — the pages, the extra tree files, `sources.json` — is
installed all the same, so a registry that has no sources yet still
serves a landing page.

## Settings

Everything is in `defaults/main.yml` with its reasoning. The ones without
a usable default, which a deployment has to give:

| Variable | What it is |
|---|---|
| `registry_publish_version` | the commit, tag or branch of the tool to run |
| `registry_publish_publishing_config` | which upstream release feeds which source |
| `registry_publish_anchor` | the root key set the result is verified against |

`registry_publish_tree_files` is a space-separated list of paths on the
server that the served tree carries and no source owns — the trust anchor
belongs there, published so a mirror operator or an auditor can compare
it against the one their tool already has.

## Ordering

Run this after `registry_storage`, `registry_snapshot` and `mirror_sync`.
It calls the last two, and it is what gives the working tree and every
source subvolume in it to the publishing account — `registry_storage`
deliberately leaves those directories' ownership alone, because the
account is created here and cannot exist when the storage is first laid
down.

## The clock

Signed documents carry a timestamp to the second and the tool refuses one
that does not advance past the document it replaces, so two writes inside
the same second are a refusal rather than a publish. The command
therefore waits for the clock to leave the second the last document used
before it writes the next one. A publish of four packages costs a few
seconds; a publish that failed halfway through a release set would cost
rather more.

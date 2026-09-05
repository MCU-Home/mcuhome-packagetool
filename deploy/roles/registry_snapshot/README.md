# registry_snapshot

Installs `packagetool-snapshot`, the command that publishes the registry
working tree, plus its settings file. The role never publishes anything
itself: it installs the command, and a dry run at the end proves the
installation works against the layout on this server.

Expects `registry_storage` (or an equivalent layout) to be in place.

## The command

```
packagetool-snapshot [--config PATH] [--root PATH] [--source NAME]...
                     [--keep-count N] [--keep-days D] [--keep-trees N]
                     [--dry-run] [--quiet]
```

One run:

1. takes a read-only btrfs snapshot of every source whose working content
   changed, into `<root>/snapshots/<source>/snapshot-<timestamp>`;
2. composes `<root>/trees/tree-<same timestamp>` — a subvolume holding
   one read-only snapshot copy of each source's newest snapshot, plus the
   files that live above the sources — and makes it read-only;
3. writes `<root>/trees/tree-<timestamp>.sources`, the manifest that says
   which snapshot of which source the tree holds;
4. points `<root>/current` at the tree by building the replacement
   symlink next to it and renaming it over the old one — `rename(2)`, so
   a client gets either the old tree or the new one and never a
   half-published one;
5. deletes the trees and the snapshots retention has let go of.

A run that would compose a tree identical to the one being served does
none of this and says so: publishing an unchanged registry would cost
every mirror a re-transfer of nothing.

The timestamp is ISO 8601 basic format in UTC, `20260904T093000Z`. Not
the extended format with colons: the name ends up in file names, in URLs
and in rsync arguments, and a colon is awkward in all three. As plain
text it sorts chronologically, which is what retention counts on. One run
uses one timestamp for everything it creates, so a tree and the snapshots
it was composed from read as one publish.

Only one run at a time: the command takes an exclusive lock on
`<root>/.snapshot.lock` and exits 6 if another run holds it. `--dry-run`
takes no lock and creates nothing at all.

Exit codes: 0 success, 1 usage error, 2 precondition not met (settings,
layout, missing tools), 3 a snapshot or the composed tree failed, 4 the
docroot switch failed, 5 everything was published but retention did not
finish, 6 another run holds the lock. What a run did goes to stdout, what
went wrong or was deliberately left alone goes to stderr — which is what
makes it usable from a systemd unit with `OnFailure=`.

## Why the tree holds copies

A composed tree is what is served, and what is served is copied by plain
rsync and read by a file server. Neither follows a symlink that leaves
the tree: an rsync daemon chroots into the docroot, where such a link
points nowhere at all. So each source in the tree is a real read-only
btrfs snapshot of that source's snapshot — the same extents, no data
copied, a directory of real files however it is read, across a reboot and
without a mount of any kind.

The tree itself is a subvolume so that it can be made read-only when it
is finished. What is served is then exactly as unwritable as the
snapshots inside it, down to the source catalogue and the pages.

## Which sources changed

Worked out from the trees themselves: the type, mode, size, modification
time and path of everything in a source, against the same in that
source's newest snapshot. A btrfs snapshot preserves all five exactly, so
two trees with the same fingerprint hold the same files — and a publish
that rewrites a document moves its modification time whatever else it
does. Reading half a gigabyte of package on every run to learn the same
thing is not worth it, and no state is kept anywhere for it: the answer
comes out of what is on disk.

The worst a mistake here can do is take a snapshot that was not needed. A
caller that already knows which sources it touched can say so with
`--source NAME`, repeatably; the others are then not even looked at, and
the composed tree still holds every source.

## Retention

A source's snapshot survives while *either* bound still covers it: it is
among the newest `keep-count` of that source, or it is younger than
`keep-days`. On top of that, a snapshot a surviving tree was composed
from is never deleted — the manifest names it, and a tree that cannot
account for what it holds is worse than a snapshot too many. The run says
on stderr when that is what kept one.

A tree survives while it is among the newest `keep-trees`. The tree
`current` points at is never deleted, whatever the bound says. Trees are
pruned before snapshots, so a tree going releases what it was holding on
to in the same run.

Entries in a snapshot area that are not named like a snapshot, and
subvolumes lying directly in `snapshots/` rather than in a source's
directory, are counted by nobody and deleted by nobody. The run names
them on stderr.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_root` | `/srv/registry` | Registry root. Must match what `registry_storage` was given. |
| `registry_snapshot_keep_count` | `10` | Keep at least this many of the newest snapshots per source. |
| `registry_snapshot_keep_days` | `7` | Keep every snapshot younger than this. |
| `registry_snapshot_keep_trees` | `10` | Keep at least this many of the newest composed trees. |
| `registry_snapshot_script_path` | `/usr/local/sbin/packagetool-snapshot` | Where the command is installed. |
| `registry_snapshot_config_dir` | `/etc/packagetool` | Settings directory. |
| `registry_snapshot_config_path` | `/etc/packagetool/snapshot.conf` | Settings file. |

The command looks for `/etc/packagetool/snapshot.conf` when it is not
given `--config`. If you move the settings file, everything that calls
the command has to pass `--config`.

## If a run stops

- *"is not a btrfs subvolume"* — a directory under `working/` that is not
  a subvolume cannot be snapshotted, and composing a tree without it
  would serve a registry that quietly lost a source. Create the
  subvolume, or move the directory's contents into one.
- *"is not a usable source name"* — a source name is a path segment of
  every URL the registry serves. Rename the directory.
- *"a tree or a snapshot for the current second already exists"* — two
  runs in the same second. Run it again.

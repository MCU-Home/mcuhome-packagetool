# registry_snapshot

Installs `packagetool-snapshot`, the command that publishes the registry
working tree, plus its settings file. The role never publishes anything
itself: it installs the command, and a dry run at the end proves the
installation works against the layout on this server.

Expects `registry_storage` (or an equivalent layout) to be in place.

## The command

```
packagetool-snapshot [--config PATH] [--root PATH]
                     [--keep-count N] [--keep-days D]
                     [--dry-run] [--quiet]
```

One run:

1. takes a read-only btrfs snapshot of `<root>/working` into
   `<root>/snapshots/snapshot-<timestamp>`;
2. points `<root>/current` at it by building the replacement symlink next
   to it and renaming it over the old one — `rename(2)`, so a client gets
   either the old tree or the new one and never a half-published one;
3. deletes the snapshots that both retention bounds have let go of.

The timestamp is ISO 8601 basic format in UTC, `20260904T093000Z`. Not
the extended format with colons: the name ends up in file names, in URLs
and in rsync arguments, and a colon is awkward in all three. As plain
text it sorts chronologically, which is what retention counts on.

Only one run at a time: the command takes an exclusive lock on
`<root>/.snapshot.lock` and exits 6 if another run holds it. `--dry-run`
takes no lock and creates nothing at all.

Exit codes: 0 success, 1 usage error, 2 precondition not met (settings,
layout, missing tools), 3 the snapshot failed, 4 the docroot switch
failed, 5 snapshot and switch succeeded but retention did not, 6 another
run holds the lock. What a run did goes to stdout, what went wrong or was
deliberately left alone goes to stderr — which is what makes it usable
from a systemd unit with `OnFailure=`.

## Retention

A snapshot survives while *either* bound still covers it: it is among the
newest `keep-count`, or it is younger than `keep-days`. Only a snapshot
that neither bound covers is deleted. The snapshot `current` points at is
never deleted, even if both bounds have let go of it; the run says so on
stderr instead.

Entries in the snapshot area that are not named like a snapshot are
counted by nobody and deleted by nobody. The run names them on stderr.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_root` | `/srv/registry` | Registry root. Must match what `registry_storage` was given. |
| `registry_snapshot_keep_count` | `10` | Keep at least this many of the newest snapshots. |
| `registry_snapshot_keep_days` | `7` | Keep every snapshot younger than this. |
| `registry_snapshot_script_path` | `/usr/local/sbin/packagetool-snapshot` | Where the command is installed. |
| `registry_snapshot_config_dir` | `/etc/packagetool` | Settings directory. |
| `registry_snapshot_config_path` | `/etc/packagetool/snapshot.conf` | Settings file. |

The command looks for `/etc/packagetool/snapshot.conf` when it is not
given `--config`. If you move the settings file, everything that calls
the command has to pass `--config`.

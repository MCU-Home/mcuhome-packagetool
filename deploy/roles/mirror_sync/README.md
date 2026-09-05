# mirror_sync

Installs `packagetool-mirror-sync`: the command that turns a registry's
btrfs snapshots into dumps an official mirror can follow, and the chain
index that says how those dumps fit together.

The role installs the command and its settings and never runs it for
real. Dumps are made when a publish makes a snapshot, and the publish
pipeline is what calls it.

## Why this exists next to rsync

A registry is a plain file tree and anybody can mirror it with anonymous
rsync. That stays true and is the path for everyone. This is the other
one: a mirror that runs btrfs itself can take a source over as snapshots
instead of as files, which costs a fraction of the transfer and gives the
mirror the same snapshot history the origin has — including the ability
to serve an old snapshot while the next one arrives.

## One chain per source

Every source is a btrfs subvolume with a snapshot chain of its own, so it
gets a dump chain of its own. A mirror that only wants one source follows
one chain and receives one subvolume, instead of taking the whole
registry to get at a tenth of it.

```
<root>/mirror-sync/<source>/snapshot-<ts>.btrfs.dump
                                the difference between a snapshot of that
                                source and the one before it (btrfs send -p)
<root>/mirror-sync/<source>/full-<ts>.btrfs.dump
                                that source's newest snapshot on its own;
                                only the newest is kept
<root>/mirror-sync/<source>/index.json
                                the chain
```

`<ts>` is the timestamp out of the snapshot's own name, so a dump is
named after what it carries and no name is ever reused. A dump is written
once: a run generates the ones that are missing and never rewrites one
that is there, because a mirror may be reading it.

What the dumps do **not** carry: the few files that live above the
sources — the pages, the source catalogue, the trust anchor. They belong
to the composed tree rather than to any source, and a mirror gets them
the way every other client does, over rsync or HTTPS.

## The chain index

```json
{
  "version": 1,
  "note": "...",
  "generated": "2026-09-05T12:00:00Z",
  "source": "sdk",
  "full": {
    "snapshot": "snapshot-20260905T120000Z",
    "parent": null,
    "file": "full-20260905T120000Z.btrfs.dump",
    "sha256": "…",
    "size": 1234
  },
  "chain": [
    {
      "snapshot": "snapshot-20260904T090000Z",
      "parent": "snapshot-20260903T080000Z",
      "file": "snapshot-20260904T090000Z.btrfs.dump",
      "sha256": "…",
      "size": 1234
    }
  ]
}
```

`chain` is ordered oldest first, and every entry's `parent` is the
`snapshot` of the entry before it: it is one unbroken line, not a set of
candidates. `full` is the newest snapshot of that source, which is why it
is not part of the line — a mirror that takes it is immediately current.

How a mirror uses it, for each source it follows:

1. `HEAD <source>/index.json` and watch `ETag`/`Last-Modified`.
2. On a change, `GET` it and look for the snapshot you already have.
3. Found — apply every entry after it, in order, with `btrfs receive`.
4. Not found — take `full` and continue from its snapshot.

Both decisions come out of that one index alone. A mirror never has to
guess and never has to be told anything out of band, and a mirror
following two sources runs the same loop twice against two independent
chains.

The index is written to a temporary name and renamed into place, and so
is every dump. A rename is atomic, so a mirror polling in the middle of a
run reads the previous complete index or the next one and never half of
either — and a dump the index names is always a dump that is finished.

## Retention

There is no retention setting, deliberately. A dump is worth keeping
exactly as long as the snapshot it carries and the snapshot it starts
from both exist; anything else is a file no mirror could apply. Since the
snapshot retention decides which snapshots exist, the dumps follow it
exactly, and the two cannot be configured into disagreeing.

In practice: when the oldest snapshot of a source is deleted, the
difference that started from it is deleted with it, the snapshot after it
becomes the new start of that source's chain, and a mirror that was
sitting on the deleted one falls back to the full dump. That is the
intended behaviour, not a degradation. It happens per source, so pruning
one source's history does not touch another's.

## Requirements

Root, because `btrfs send` needs it, and `btrfs-progs` and `jq`.
Snapshots must be read-only — `btrfs send` refuses otherwise — which is
what `registry_snapshot` creates.

Serving the dumps is not this role's business: they land in a directory
beside the served tree, not inside it, so no path under a public virtual
host can reach them, and who may read them is decided by whatever sits in
front.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_root` | `/srv/registry` | Registry root. Must match what `registry_storage` was given. |
| `mirror_sync_script_path` | `/usr/local/sbin/packagetool-mirror-sync` | Where the command is installed. |
| `mirror_sync_config_dir` | `/etc/packagetool` | Settings directory. |
| `mirror_sync_config_path` | `/etc/packagetool/mirror-sync.conf` | Settings file. |
| `mirror_sync_packages` | `[btrfs-progs, jq]` | Packages to install. Set to `[]` if managed elsewhere. |

The command takes `--source NAME`, repeatably, to work on one source
rather than all of them.

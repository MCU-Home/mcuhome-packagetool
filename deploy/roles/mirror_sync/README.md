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
one: a mirror that runs btrfs itself can take the tree over as snapshots
instead of as files, which costs a fraction of the transfer and gives the
mirror the same snapshot history the origin has — including the ability
to serve an old snapshot while the next one arrives.

## What a run produces

```
<root>/mirror-sync/snapshot-<ts>.btrfs.dump   the difference between a
                                              snapshot and the one before
                                              it (btrfs send -p)
<root>/mirror-sync/full-<ts>.btrfs.dump       the newest snapshot on its
                                              own; only the newest is kept
<root>/mirror-sync/index.json                 the chain
```

`<ts>` is the timestamp out of the snapshot's own name, so a dump is
named after what it carries and no name is ever reused. A dump is written
once: a run generates the ones that are missing and never rewrites one
that is there, because a mirror may be reading it.

## The chain index

```json
{
  "version": 1,
  "note": "...",
  "generated": "2026-09-05T12:00:00Z",
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
candidates. `full` is the newest snapshot, which is why it is not part of
the line — a mirror that takes it is immediately current.

How a mirror uses it:

1. `HEAD index.json` and watch `ETag`/`Last-Modified`.
2. On a change, `GET` it and look for the snapshot you already have.
3. Found — apply every entry after it, in order, with `btrfs receive`.
4. Not found — take `full` and continue from its snapshot.

Both decisions come out of the index alone. A mirror never has to guess
and never has to be told anything out of band.

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

In practice: when the oldest snapshot is deleted, the difference that
started from it is deleted with it, the snapshot after it becomes the new
start of the chain, and a mirror that was sitting on the deleted one
falls back to the full dump. That is the intended behaviour, not a
degradation.

## Requirements

Root, because `btrfs send` needs it, and `btrfs-progs` and `jq`. Snapshots
must be read-only — `btrfs send` refuses otherwise — which is what
`registry_snapshot` creates.

Serving the dumps is not this role's business: they land in a directory
beside the served tree, not inside it, so no path under a public virtual
host can reach them, and who may read them is decided by whatever sits in
front.

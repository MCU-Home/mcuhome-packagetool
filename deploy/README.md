# deploy — hosting a packagetool registry

A packagetool registry is a plain static file tree: signed index
documents, package files, and a few HTML pages. Integrity comes from the
signatures and hashes inside the tree, not from the server, so anything
that can serve files can serve a registry, and anything that can copy
files can mirror one.

This directory holds the Ansible material that sets such a server up.

```
roles/          generic roles, usable by anyone hosting a registry
mcuhome/        the values the public MCUHome registry is run with
tests/          checks that run on a throwaway filesystem, not on a server
```

Nothing under `roles/` knows anything about a particular registry: every
value it needs is a variable. `mcuhome/` is the other half — the settings
one specific registry is run with — and is kept separate so that using
these roles for a registry of your own means writing your own equivalent
of `mcuhome/`, not editing the roles.

## The registry root

Everything below assumes one directory, the registry root, with a fixed
shape inside it:

```
<root>/working        the working tree, a btrfs subvolume, never served
<root>/snapshots/     read-only snapshots, one per publish
<root>/mirror-sync/   dumps for official mirrors
<root>/placeholder/   empty; the docroot before the first publish
<root>/current        symlink to the tree that is served
```

Only the root is configurable. The names inside it are not, because the
web server, the rsync export, the publish pipeline and the mirror export
all have to agree on them, and a mismatch there is not a matter of taste
but a registry that serves the wrong thing.

## Publishing by snapshot

A publish edits `working`, which nothing serves. When the tree is
complete, `packagetool-snapshot` takes a read-only btrfs snapshot of it
and replaces the `current` symlink with one pointing at that snapshot.
Replacing a symlink is a rename, and a rename is atomic: a client either
gets the old tree or the new one, never a tree that is half published.
Because snapshots share their data with the working tree, keeping the
last few dozen publishes costs almost nothing.

Old snapshots are deleted only when both retention bounds have let go of
them — not among the newest N, and older than D days — and the snapshot
the docroot points at is never deleted.

## Roles

| Role | What it does |
|---|---|
| `registry_storage` | btrfs filesystem on a dedicated device, the registry subvolume, the mount, and the tree inside it. |
| `registry_snapshot` | installs `packagetool-snapshot`: snapshot, docroot switch, retention. |

Each role has a README of its own next to it.

A minimal playbook using both:

```yaml
- name: Set the registry storage up
  hosts: registry
  become: true
  vars:
    registry_root: /srv/registry
    registry_storage_device: /dev/disk/by-id/...
  roles:
    - role: registry_storage
    - role: registry_snapshot
```

`registry_storage` writes to a block device. It creates a filesystem only
on a device a low-level probe finds completely empty, and adopts a device
that already carries btrfs as it is. A device that carries a filesystem
other than btrfs, a partition table, or an LVM or RAID signature stops
the run, unchanged. Point it at the wrong device and you get an error,
not a lost disk. Check mode is not supported: on a device without a
filesystem, everything after the step that would create one has nothing
to look at.

## Tests

`tests/registry-snapshot` exercises the snapshot, the docroot switch and
the retention bounds against a real btrfs filesystem in a loopback image,
created and thrown away by the test itself. It needs root, `btrfs-progs`
and loop devices, and exits 77 when it cannot have them.

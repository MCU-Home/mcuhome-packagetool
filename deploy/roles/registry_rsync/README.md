# registry_rsync

The anonymous read-only rsync export of the registry. A mirror runs

```
rsync -a --delete rsync://<host>/registry/ /srv/mirror/
```

and has the whole tree — which is the point of a registry being a plain
file tree and not a service.

Expects `registry_storage` (or an equivalent layout).

## Native, not containerised

The daemon needs to chroot into the served tree and to answer on the
host's own addresses. Both are things a container would have to be handed
back one at a time until nothing of the container was left, so this one
runs on the host.

## Started per connection

systemd owns the listening socket and starts one short-lived daemon per
connection, the way the rsync project's own units do it. Two reasons, and
both of them decide something:

- rsyncd's `address` parameter holds exactly one address, and a
  dual-stacked host has two. A socket unit takes as many `ListenStream=`
  lines as you like.
- The dependency on the registry filesystem belongs on the socket. With
  the volume missing, nothing listens at all — and a connection refused
  is a far better answer to a mirror than an empty tree that looks like a
  registry which lost everything.

The connection limit still works across those separate processes: rsyncd
enforces it with record locks on its lock file, which is what that file
is for.

## Limits

Two bounds, and neither replaces the other:

- `max connections` — how many transfers may run at once. A client
  arriving when the limit is reached is told to come back later.
- `--bwlimit` — how much one of them may cost.

The worst case is their product, and that is the number to compare
against the machine's uplink and its traffic budget. With the defaults,
four connections at 5 MiB/s each, that is 20 MiB/s.

The bandwidth limit is **not** an `rsyncd.conf` setting. rsync has no
such parameter, in any version — the daemon takes `--bwlimit` on its own
command line, and treats it as a ceiling rather than a default: a client
asking for more is clamped down to it, a client asking for less keeps its
own lower value. That is why the limit lives in the unit and not in the
configuration file, and why a client cannot argue with it.

It is a plain number of KiB/s. The daemon form of the option takes an
integer and rejects everything else — `5m` gets you "invalid numeric
value (in daemon mode)" and a daemon that does not start, even though the
same spelling is fine on a client. Zero, or empty, means no limit.

A per-connection ceiling is not a monthly traffic cap. Nothing here
watches a budget; if that matters for your uplink, it is a separate
mechanism.

## The docroot is a symlink

The module path is `<root>/current`, the symlink a publish replaces.
rsync resolves it — and chroots into it — once per connection, in the
process that serves that connection. A transfer already running keeps the
tree it started with, the next connection gets the new one, and neither
ever sees a tree that is half published. `<root>/current` points at a
composed tree — a read-only btrfs subvolume with a read-only btrfs
subvolume nested under it for each source. Tree retention, not snapshot
retention, is what makes the first half safe: it is what keeps a tree a
running transfer holds open from being deleted, and the tree the docroot
currently points at is never the one a publish removes.

### Per-source subvolumes and `--one-file-system`

Each source's directory inside the served tree is its own btrfs
subvolume, so it sits on a different device number than the tree around
it. Plain `rsync -a` (or `-av`, `--delete`) does not care about device
boundaries and copies everything regardless. `-x` / `--one-file-system`
does care: against this module it would stop at every source's boundary
and leave behind empty directories instead of their contents, so it must
not be used here. A mirror that wants one source only does not need that
option either — adding the source's name to the module path, as in
`rsync://<host>/<module>/<source>/`, copies just that subvolume.

`use chroot = yes` is set explicitly and should stay that way. Since
rsync 3.2.7 an unset value means "try, and carry on without it if it
fails", and a module whose path is a symlink is exactly the shape that
has been a path-traversal problem in rsync without the chroot.

## Not serving an empty registry

Both units carry `RequiresMountsFor=` on the registry root and an
`AssertPathIsMountPoint=` on top. A boot without the volume — the fstab
entry says `nofail`, so that boot is possible — leaves the socket failed
instead of exporting the empty directory underneath the mount point.

The per-connection unit's `ExecStart` is prefixed with `-`, so an aborted
or refused connection does not mark a unit failed; a public export
collects those all day and treating each as a failure would drown
anything worth being told about. An assertion failure is not covered by
that prefix, which is the point: a missing filesystem still fails
visibly, and `OnFailure=` still fires for it.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_root` | `/srv/registry` | Registry root. Must match what `registry_storage` was given. |
| `registry_rsync_module` | `registry` | The name in `rsync://<host>/<module>/`. |
| `registry_rsync_comment` | `packagetool registry, read-only` | What a module listing shows. |
| `registry_rsync_listen_addresses` | `[]` | Addresses to listen on. Empty means all of them. |
| `registry_rsync_port` | `873` | Port to listen on. |
| `registry_rsync_max_connections` | `4` | Simultaneous transfers. |
| `registry_rsync_bwlimit` | `5120` | Per-connection ceiling, in KiB/s. A whole number. Zero or empty means none. |
| `registry_rsync_timeout` | `600` | I/O timeout imposed on the client, in seconds. |
| `registry_rsync_uid` / `_gid` | `nobody` / `nogroup` | Who transfers run as after the chroot. |
| `registry_rsync_refuse_options` | `delete* remove-source-files` | Options a client may not use. `delete*` is inert for this download-only module — a client's deletions happen purely on its own side and never reach the daemon — and is kept only as belt-and-suspenders. |
| `registry_rsync_dont_compress` | `*.zst *.gz *.xz *.bz2 *.zip *.7z` | What not to spend CPU compressing. |
| `registry_rsync_config_path` | `/etc/packagetool/rsyncd.conf` | Generated configuration. |
| `registry_rsync_lock_file` | `/run/packagetool-rsyncd.lock` | Where the connection limit is enforced. |
| `registry_rsync_service` | `packagetool-rsyncd` | Unit name, without the suffix. |
| `registry_rsync_disable_packaged_units` | `true` | Switch `rsync.service` and `rsync.socket` off. |
| `registry_rsync_packages` | `[rsync]` | Packages to install. Set to `[]` if managed elsewhere. |

## If a run stops

- *the check at the end fails* — the run connects to the export the way a
  mirror does and asks the module for a listing. A connection refused
  means the socket is not listening: `systemctl status
  packagetool-rsyncd.socket`, and an assertion failure there is a storage
  problem. A chroot error in the journal means the module path does not
  resolve to a directory — check what `<root>/current` points at.
- *`@ERROR: max connections`* — the limit, doing its job. It is also what
  a stale lock file looks like if a machine was hard-reset; the file is
  in `/run` and goes away on a reboot.

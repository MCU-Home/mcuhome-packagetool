# registry_storage

Puts a registry on a block device: a btrfs filesystem, one subvolume that
carries the registry, the mount for it, and the tree inside it.

## What it does, in order

1. Installs `btrfs-progs`.
2. Probes the device with `blkid -p`. A device that carries a filesystem
   other than btrfs, a partition table, or a container signature stops
   the run, unchanged. A device that carries btrfs is adopted as it is.
   Only a device the probe finds completely empty is formatted, and
   `mkfs.btrfs` is called without `--force`, so it would refuse a second
   time if the probe were ever wrong.
3. Creates the registry subvolume (`@registry` by default). The volume
   root is mounted at the registry root for exactly as long as that
   takes, and unmounted again — the volume root is never left mounted, so
   the same volume can carry other subvolumes later.
4. Writes the fstab entry and mounts the subvolume. The entry names the
   filesystem by UUID and carries `nofail`, so a missing volume delays
   the boot but does not stop it.
5. Creates `working` (a subvolume), `snapshots/`, `trees/`,
   `mirror-sync/`, `placeholder/`, and the `current` symlink if it does
   not exist yet. An existing `current` is never touched: it is what a
   running registry is being served from.
6. Creates one subvolume per name in `registry_storage_sources`, under
   `working/`. A subvolume is what can be snapshotted, and publishing
   snapshots the sources that changed rather than the whole tree.

A source subvolume with nothing in it is a source that has not been laid
down yet. Laying one down needs keys that are deliberately not on a
registry server, so this role creates the subvolume and nothing else, and
the publish pipeline reports such a source and carries on.

The role can be run again at any time. It changes nothing once the state
above is reached.

## What it does not do

- It never deletes, moves or reformats anything that is already on the
  device. A source subvolume that is not in
  `registry_storage_sources` is left exactly where it is: it is a source
  somebody published, and removing one is not a configuration run's
  decision.
- It does not turn a plain directory under `working/` into a subvolume.
  That means moving the contents of something that is already published,
  which is a migration and not a step of a role that runs on every
  change. The snapshot command refuses such a directory and says so.
- It does not remount a filesystem whose options changed in fstab. A
  `subvol=` cannot be changed by a remount at all, and the rest is not
  worth unmounting a live registry for; the run reports the difference
  and leaves the decision to the operator.
- It does not fill `mirror-sync/`. That directory is reserved for the
  mirror export.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_storage_device` | – | Block device to use. Required. Use a stable name under `/dev/disk/by-id/`. |
| `registry_root` | `/srv/registry` | Where the registry subvolume is mounted. Shared with the other registry roles. |
| `registry_storage_subvolume` | `@registry` | Name of the subvolume that carries the registry. |
| `registry_storage_label` | `registry` | Filesystem label, used only when the role creates the filesystem. |
| `registry_storage_sources` | `[]` | The sources this registry carries. One subvolume each under `working/`. Every name is a path segment of a public URL, so only letters, digits, dots, dashes and underscores are accepted. |
| `registry_storage_mount_options` | `noatime,compress=zstd:3,nodev,nosuid,nofail,x-systemd.device-timeout=15s` | Mount options. `subvol=` is added by the role and must not be listed. |
| `registry_storage_owner` | `root` | Owner of the registry tree. |
| `registry_storage_group` | `root` | Group of the registry tree. |
| `registry_storage_format_if_empty` | `true` | Whether an empty device may be formatted. A device that carries anything is never written to, whatever this is set to. |
| `registry_storage_packages` | `[btrfs-progs]` | Packages to install. Set to `[]` if packages are managed elsewhere. |

## If a run stops

- *"already carries data"* — the probe found something on the device.
  Nothing was changed. Either point `registry_storage_device` at the
  right device, or wipe the one you meant by hand.
- *"Something other than the … subvolume is mounted"* — usually the
  volume root, left behind by a run that was interrupted while the
  subvolume was being created. Unmount the registry root and run again.
- *"exists and is not a symlink"* — `current` is a real directory.
  Publishing switches the served tree by replacing that symlink, so it
  has to be one. Move the directory aside by hand.

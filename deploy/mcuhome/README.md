# mcuhome — how the public MCUHome registry is run

The roles in `../roles/` know nothing about any particular registry. This
directory is the other half: the settings the registry at
packages.mcuhome.org is actually run with.

It is kept separate for two reasons. Reading it tells you what a real
registry looks like in practice, which is more useful than a table of
defaults. And running a registry of your own means writing your own
version of this directory rather than editing the roles.

```
vars/registry.yml   storage layout and snapshot retention
vars/serving.yml    the host names, what each one serves, the rsync limits
```

## What is here and what is not

Here: values that describe the registry itself — where it is mounted,
what the subvolume is called, how the filesystem is mounted, how many
snapshots and how many days of them are kept, which host names it answers
on and what each of them is allowed to answer with.

Not here: anything that is a property of one particular machine or of the
way it is administered. Which block device the registry lives on, which
addresses the virtual hosts bind, how the server is reached, and every
credential belong to the inventory that describes the machine, not to
this repository.

## The three host names

| Name | Serves |
|---|---|
| `mirror-1.packages.mcuhome.org` | the full tree over HTTPS, the browsable pages, and the anonymous rsync export |
| `packages.mcuhome.org` | the bootstrap subset: the anchor, the source list, and each source's key set and mirror list |
| `mirror-sync.packages.mcuhome.org` | the dumps official mirrors bootstrap and catch up from, behind per-mirror credentials |

They are three names on one machine, and nothing about the split assumes
they stay that way. A client that has an anchor asks the bootstrap host
where the data is, and goes wherever `mirrors.json` points — which is why
that document is signed and why no index document contains an absolute
URL.

The mirror host name is deliberately not derived from any scheme. Mirrors
are discovered through `mirrors.json`, and a future independent mirror
will live under whatever domain its operator has.

## Using it

The playbook that runs against the registry server loads these files and
hands the roles what is in them:

```yaml
- name: Set the registry up
  hosts: registry
  become: true
  vars_files:
    - <this repository>/deploy/mcuhome/vars/registry.yml
    - <this repository>/deploy/mcuhome/vars/serving.yml
  vars:
    # The public sites here, plus whatever this machine adds of its own.
    registry_proxy_sites: >-
      {{ registry_proxy_public_sites + (registry_proxy_private_sites | default([])) }}
  roles:
    - role: registry_storage
    - role: registry_snapshot
    - role: registry_web
    - role: registry_proxy
    - role: registry_rsync
```

Four values are deliberately not set here, because each of them describes
one machine rather than the registry:

| Value | Where it belongs |
|---|---|
| `registry_storage_device` | the block device the registry lives on |
| `registry_proxy_public_addresses` | the addresses the public sites bind |
| `registry_rsync_listen_addresses` | the addresses the rsync export listens on |
| `registry_mirror_sync_users` | the mirror-sync credentials — never in a repository |

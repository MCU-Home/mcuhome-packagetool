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
```

## What is here and what is not

Here: values that describe the registry itself — where it is mounted,
what the subvolume is called, how the filesystem is mounted, how many
snapshots and how many days of them are kept.

Not here: anything that is a property of one particular machine or of the
way it is administered. Which block device the registry lives on, how the
server is reached, and every credential belong to the inventory that
describes the machine, not to this repository.

## Using it

The playbook that runs against the registry server loads this file and
hands the roles what is in it:

```yaml
- name: Set the registry storage up
  hosts: registry
  become: true
  vars_files:
    - <this repository>/deploy/mcuhome/vars/registry.yml
  roles:
    - role: registry_storage
    - role: registry_snapshot
```

`registry_storage_device` is deliberately not set here. It names the
device on one specific server and is set in that server's inventory.

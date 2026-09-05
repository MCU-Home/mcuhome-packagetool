# registry_web

Serves the registry tree over HTTP, on the loopback address, from an
nginx container. It terminates no TLS and knows no host name: what it
answers is decided by which of its ports a request arrives on, and
putting a name and a certificate in front of it is `registry_proxy`'s
job.

Expects `registry_storage` (or an equivalent layout) and a running
Docker.

## The three vhosts

| Port | Docroot | Serves |
|---|---|---|
| 8080 | `<root>/current` | the full registry tree, including the pages |
| 8081 | `<root>/current` | the bootstrap subset, and nothing else |
| 8082 | `<root>/mirror-sync` | the per-source dumps for official mirrors |

Three servers rather than three locations of one server. A request that
somehow reaches the wrong port then gets a server that serves a different
tree, instead of one path rule away from the tree it was not meant to
see. The dump area helps: it sits *beside* the served tree, not inside
it, so no path under the public vhosts can reach it at all.

Who may read 8082 is not decided here. Nothing on it is public, and the
proxy in front is what asks for credentials.

Under that docroot each source has its own directory: a mirror fetches
`https://<host>/<source>/index.json` and the dumps it names, never a
single index for the whole registry. The vhost's own root is a 404 by
construction — nothing matches there, and a source directory requested
without one of the two allowed file shapes is a 404 too.

### What the bootstrap vhost serves, and why exactly that

Enough to get a client from "I have a trust anchor and a source name" to
"I know which mirror to fetch from", and not one file more:

```
/anchor.json                    the trust anchor, for comparing against
                                the one the tool already carries
/sources.json                   which sources exist
/<source>/keys.json      + .sig  the key set, verified against the anchor
/<source>/keys/*.json    + .sig  the key sets it superseded
/<source>/mirrors.json   + .sig  where this source may be fetched
```

That is the first half of the verification the reference verifier does:
the anchor is the caller's, `keys.json` is checked against it, and
`mirrors.json` is checked against the publisher keys `keys.json`
authorises. Choosing a mirror comes next, and everything after that —
the index, its parts, the package bytes — happens against the mirror.

The archived key sets are in the set because they are not optional. A
tool's anchor is as old as the tool, a source rotates its root keys on
its own schedule, and a client whose anchor predates a rotation gets to
the current key set only by walking the `previous` chain backwards. Leave
those out and the bootstrap host works for everyone except the clients
that need it most.

`index.json` is deliberately *not* in the set. It is the head of the
package index, and a host that serves it while serving neither the index
parts nor a single package byte looks like a source and is not one — a
client resolving a package against it would find every entry it wanted
and nothing to download. Answering "not here" for the whole index is the
honest half of the split, and it is what makes the bootstrap host
unmistakably a signpost rather than a mirror.

The rule is written as an allow list. Everything not named above is a
404, so a file that appears in the tree later is not served here by
accident.

## Cache headers

| What | Header |
|---|---|
| package files, their `.sha256` sidecars | `public, max-age=31536000, immutable` |
| content-named index parts (`index-…-<hash>.json`) | same |
| archived key sets under `<source>/keys/` | same |
| `index.json`, `keys.json`, `mirrors.json` and their `.sig` | `no-cache` |
| `sources.json`, `anchor.json`, HTML pages | `no-cache` |
| each source's mirror-sync chain index | `no-cache` |
| everything else | `public, max-age=300` |

The split follows one rule: a name that can never stand for different
bytes may be cached forever, and everything else has to be revalidated.
A published version is immutable and never deleted, an index part carries
the hash of its own content in its name, and an archived key set is
written once under the timestamp it was issued at — those three can be
kept for a year. The signed head documents are replaced in place on every
publish and every refresh, and a stale copy of one of them is precisely
what the `expires` field and the weekly refresh exist to catch, so they
are revalidated every time. `no-cache` does not mean "do not store": with
the `ETag` nginx sends, a poll costs a 304 and no body, which is what
each source's mirror-sync chain index is polled with.

Nothing is compressed. Package files are compressed already, the
documents are a kilobyte each, and on-the-fly compression is the classic
way to lose byte ranges on the large files that need them most. Ranges
come from nginx unmodified and survive the proxy in front.

## The docroot is a symlink

`<root>/current` is a symlink that a publish replaces by renaming a new
tree over it — a composed tree under `<root>/trees/`, not a single
snapshot. nginx resolves it per request, so a request either gets the
old tree or the new one. `open_file_cache` is therefore not configured
and must not be: cached `stat()` results would keep serving the tree that
was published before.

## Not serving an empty registry

The unit carries `RequiresMountsFor=` on the registry root and an
`AssertPathIsMountPoint=` on top of it. A boot that comes up without the
volume — the fstab entry says `nofail`, so that boot happens — would
otherwise find an empty directory where the registry is mounted and serve
it: an established registry that suddenly has nothing in it, which is the
one answer a mirror must never get. With those two lines the web server
fails to start instead, loudly, and `OnFailure=` can be hung off it.

## Why systemd owns the container

The container is started by a systemd unit that runs `docker run` in the
foreground, not by Docker's own restart policy. A unit can be ordered
after the filesystem the registry lives on, can be told to fail when that
filesystem is missing, and can have a notification hung off its failure.
A container with `--restart=always` can do none of the three.

## Networking, and why the host firewall still means something

The container runs with `--network host`. That is a deliberate choice
with two reasons behind it.

A published container port (`-p 203.0.113.1:80:80`) is DNAT'ed in the
prerouting hook and then traverses the *forward* path. The input chain of
a host firewall never sees it, so every input rule on the host is
bypassed for that port — which is exactly the kind of thing that is true
and invisible until someone tests it. With host networking there is no
port publishing and no DNAT: the process binds a host socket like any
other daemon, and the host's input rules decide who reaches it. The
firewall's default-drop policy becomes a real second layer behind the
addresses configured here, instead of a rule set that quietly does not
apply.

The second reason is the client address. Docker's userland proxy — which
is what handles a published port on an IPv6 host address when the daemon
has no IPv6 bridge — rewrites the source address, so every IPv6 client
would arrive as the bridge gateway. Host networking keeps both address
families intact.

What it costs is the network namespace: a compromised web server could
bind other host ports and reach services on the loopback address. For a
static file server behind a proxy that is an acceptable trade against a
firewall that does not do what it says. If you would rather have the
namespace, publish ports to explicit host addresses instead and constrain
container traffic in Docker's `DOCKER-USER` chain — the input rules will
not do it for you.

Everything else about the container is closed down: read-only root
filesystem with tmpfs for the two directories nginx writes to, all
capabilities dropped except the three the master process needs to hand its
workers to an unprivileged user (`CHOWN`, `SETGID`, `SETUID`),
`no-new-privileges`, and the registry root bind-mounted read-only.

## The generated configuration replaces the image's own

`registry_web_config_path` is bind-mounted over `/etc/nginx/nginx.conf`,
so the image's own configuration file is never read. The generated file
sets no `user` directive, so which user the worker processes run as after
the master hands them off is whatever the nginx binary was compiled
with — the official image's `nginx` binary defaults to `nginx` (built
with `--user=nginx --group=nginx`), which is why the three capabilities
dropped down to (`CHOWN`, `SETGID`, `SETUID`) are enough. Anyone pointing
`registry_web_image` at a different image must check that same property;
an image built with a different default user, or with root as the
compiled-in default, changes what the container needs and what it can
do.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_root` | `/srv/registry` | Registry root. Must match what `registry_storage` was given. |
| `registry_web_image` | `docker.io/library/nginx:stable-alpine` | Image to run. May carry a digest. |
| `registry_web_listen_address` | `127.0.0.1` | Address the vhosts listen on. |
| `registry_web_tree_port` | `8080` | Port of the full-tree vhost. |
| `registry_web_bootstrap_port` | `8081` | Port of the bootstrap vhost. |
| `registry_web_mirror_sync_port` | `8082` | Port of the mirror-sync vhost. |
| `registry_web_bootstrap_enabled` | `true` | Whether the bootstrap vhost exists. |
| `registry_web_mirror_sync_enabled` | `true` | Whether the mirror-sync vhost exists. |
| `registry_web_autoindex` | `false` | Directory listings on the full-tree vhost. |
| `registry_web_bootstrap_redirect` | `""` | Where `/` on the bootstrap vhost redirects to. Empty means 404. |
| `registry_web_immutable_max_age` | `31536000` | Lifetime for files that cannot change under their name. |
| `registry_web_default_max_age` | `300` | Lifetime for everything the rules do not name. |
| `registry_web_config_path` | `/etc/packagetool/nginx.conf` | Generated configuration. |
| `registry_web_service` | `packagetool-web` | Unit name, without the suffix. |
| `registry_web_container` | `packagetool-web` | Container name. |
| `registry_web_validate_config` | `true` | Check the configuration with the image's nginx before writing it. |
| `registry_web_docker` | `/usr/bin/docker` | The docker client. |
| `registry_web_docker_service` | `docker.service` | The unit the container unit depends on. |

## If a run stops

- *the configuration was refused* — the run stops at the template step
  with nginx's own message and the previous configuration is still in
  place, untouched. Nothing was restarted.
- *the unit will not start* — `journalctl -u packagetool-web` first. An
  assertion failure there means the registry root is not a mount point,
  which is a storage problem, not a web server one.
- *every request is a 404* — check what `<root>/current` points at. An
  unpublished registry points at the empty placeholder, and 404 is the
  correct answer then.

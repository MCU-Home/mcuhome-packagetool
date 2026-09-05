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
| 8080 | `<root>/current` | the full registry tree, browsable, plus one page of its own at `/` |
| 8081 | `<root>/current` | the bootstrap subset, plus one page of its own at `/` |
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
accident. The page at `/` is the one exception, and it is not a file of
the tree at all — see below.

## The pages are not in the tree

Neither vhost serves HTML out of the registry. The tree is what a mirror
copies byte for byte, and a page is a property of the host that serves
it: a mirror that is only an rsync target has no business carrying
somebody else's landing page, and a page that lived in the tree would be
copied, mirrored and dumped along with the packages.

So each of the two public vhosts answers exactly `/` with one file
installed beside the configuration, in `registry_web_assets_dir`, and
nothing else:

| Variable | Vhost | What the page is for |
|---|---|---|
| `registry_web_tree_index_page` | full tree | what this mirror is, and the sources it carries |
| `registry_web_bootstrap_page` | bootstrap | what the registry is, how to verify a copy, and a browser over the mirrors |

Both are paths on the machine running Ansible; the role copies them onto
the server and bind-mounts that directory into the container read-only.
Leave one empty and that vhost has no page: the tree vhost answers `/`
with a listing, the bootstrap vhost with a 404 or with the redirect
`registry_web_bootstrap_redirect` names. Setting a page and a redirect
at once is refused — they are two answers to one question.

The location is `location = /` with a `root` of its own and `try_files`,
not an `alias` naming the file. For a URI ending in `/` nginx's index
module appends the index file to whatever the path resolves to, which
turns an alias naming a file into `<file>index.html` and a 500 into the
first thing a visitor sees.

## Browsing the tree

With `registry_web_autoindex` on, a directory of the full tree is
answered with a listing. With `registry_web_autoindex_xslt` on as well —
the default — that listing is nginx's own XML output rendered by an XSLT
stylesheet **in the server**: no JavaScript, no assets, and the same
styling as the pages. The stylesheet this role ships is rendered from
`templates/autoindex.xslt.j2`, not copied verbatim, so its footer can
carry a deployment's own links without forking the file — see
`registry_web_autoindex_footer_html` below. `registry_web_autoindex_stylesheet`
replaces the whole thing with a file of your own instead, copied as-is
with no templating applied.

Every href in a listing is written with a leading `./`: a file or
directory name comes out of nginx's own XML and is otherwise untrusted —
a name such as `javascript:alert(1)` would otherwise parse as an
absolute URL with a `javascript:` scheme instead of a relative link.
nginx's own built-in autoindex (the one this stylesheet replaces) escapes
the colon and is not affected; the XML/XSLT path is. `./` forces every
href to read as relative regardless of what the name looks like.

Two things about it are worth knowing before it is switched on:

- The filter is a **dynamic module**. The generated configuration loads
  it with `load_module`, and the official nginx images (Debian and
  Alpine) ship it in `/usr/lib/nginx/modules`. An image without it makes
  the server refuse to start; set `registry_web_autoindex_xslt` to false
  there and get nginx's built-in listing instead.
- The stylesheet is read and compiled **while the configuration is
  loaded**, not per request. A missing or broken one is a server that
  will not start, which is why the role installs it before it writes the
  configuration, and why the configuration check runs with the asset
  directory mounted.

Only this server's own listings are ever transformed: the filter runs on
`text/xml` responses, and nothing else here has that type.

## Reading the documents from another host

The pages of this registry are served by the bootstrap host and the data
by the mirrors — that is what a mirror list is *for* — so the browser
page's requests to a mirror are cross-origin, and a browser refuses them
unless the mirror says otherwise. `registry_web_cors_origin` is that
permission; `*` is the right value for a public registry, and empty (the
default) switches the header off.

It is set on the full-tree vhost and on the JSON documents only:
`sources.json`, `anchor.json`, the signed head documents and their
signatures, the archived key sets, and the content-named index parts.
Package files are left out — nothing reads them with a script. The
bootstrap vhost never sends it: its own page reads it from its own
origin.

Nothing is given away by it. Every document it covers is public,
unauthenticated and fetchable with `curl` by anyone; the header only
stops browsers from pretending otherwise. It is sent with `always`, so a
404 arrives at the page as a 404 instead of as an unexplained network
error.

## Cache headers

| What | Header |
|---|---|
| package files, their `.sha256` sidecars | `public, max-age=31536000, immutable` |
| content-named index parts (`index-…-<hash>.json`) | same |
| archived key sets under `<source>/keys/` | same |
| `index.json`, `keys.json`, `mirrors.json` and their `.sig` | `no-cache` |
| `sources.json`, `anchor.json`, the two pages | `no-cache` |
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
`no-new-privileges`, and the registry root and the asset directory both
bind-mounted read-only.

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
| `registry_web_autoindex_xslt` | `true` | Render those listings with a stylesheet in the server. Needs the xslt module. |
| `registry_web_autoindex_stylesheet` | `""` | The stylesheet to render them with, copied verbatim. Empty means the one this role ships, rendered from a template instead. |
| `registry_web_autoindex_footer_html` | `""` | Extra footer HTML on the shipped stylesheet's listings, right after the "This mirror" link. Ignored when `registry_web_autoindex_stylesheet` names a file of your own. |
| `registry_web_xslt_module` | `modules/ngx_http_xslt_filter_module.so` | Where the xslt filter module is, as nginx resolves it. |
| `registry_web_tree_index_page` | `""` | Page served at `/` on the full-tree vhost, as a path on the Ansible machine. |
| `registry_web_bootstrap_page` | `""` | Page served at `/` on the bootstrap vhost, same. |
| `registry_web_bootstrap_redirect` | `""` | Where `/` on the bootstrap vhost redirects to when it serves no page. Empty means 404. |
| `registry_web_cors_origin` | `""` | Origin allowed to read the full tree's JSON documents. `*` for a public registry, empty for none. |
| `registry_web_assets_dir` | `/etc/packagetool/web` | Where the pages and the stylesheet are installed. |
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
  which is a storage problem, not a web server one. A complaint about
  `ngx_http_xslt_filter_module.so` means the image does not carry the
  module: set `registry_web_autoindex_xslt` to false, or point
  `registry_web_xslt_module` at where that image keeps it.
- *the root of a vhost answers 404* — the page for it is not installed.
  The role only installs one when `registry_web_tree_index_page` or
  `registry_web_bootstrap_page` names a file it can read on the machine
  running Ansible.
- *every request is a 404* — check what `<root>/current` points at. An
  unpublished registry points at the empty placeholder, and 404 is the
  correct answer then.

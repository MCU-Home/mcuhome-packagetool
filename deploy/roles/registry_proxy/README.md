# registry_proxy

The public face of the registry: a Caddy container that terminates TLS,
answers for the host names, obtains and renews the certificates, asks for
credentials where they are required, and forwards everything else to
whatever serves the files.

Expects a running Docker, and a host firewall that lets 80 and 443
through on the addresses the sites bind.

## Sites

The role is driven by one list, `registry_proxy_sites`. Every entry is a
virtual host:

```yaml
registry_proxy_sites:
  - name: mirror.example.org
    bind: ["203.0.113.10", "2001:db8::10"]
    upstream: "127.0.0.1:8080"

  - name: dumps.example.org
    bind: ["203.0.113.10", "2001:db8::10"]
    upstream: "127.0.0.1:8082"
    auth: basic
    realm: "example.org mirror sync"
    users: []

  - name: not-yet.example.org
    bind: ["203.0.113.10", "2001:db8::10"]
    upstream: "127.0.0.1:8081"
    tls: internal
```

| Key | Meaning |
|---|---|
| `name` | The host name, and the name on the certificate. |
| `bind` | The host addresses this site listens on. Required. |
| `upstream` | Where requests go, `address:port`. |
| `tls` | `acme` (default) or `internal`. |
| `auth` | `none` (default) or `basic`. |
| `realm` | For `basic`: what the password prompt says. |
| `users` | For `basic`: `{name, hash}` entries with bcrypt hashes. |

Adding a site is adding an entry. A site on a different address — an
internal service on an internal address, say — needs nothing else; that
is what the per-site `bind` list is for.

## Binding, and what listens where

`bind` is not decoration. Without it a site listens on every address the
host has, and on a machine that keeps its administrative address apart
from its public ones, that is the whole separation gone. Caddy builds the
port-80 server it needs — for the redirect to HTTPS and for the ACME
HTTP-01 challenge — out of the same addresses the site binds, so a name
that is not supposed to be reachable somewhere does not get a listener
there either.

HTTP/3 is off. It runs over UDP 443, and a host firewall that opens only
TCP would have Caddy advertise a port that is closed. Open UDP 443 first,
then set `registry_proxy_http3`.

## Credentials

`auth: basic` puts HTTP basic authentication in front of everything the
site serves. Caddy runs it before the proxy, so an unauthenticated
request is answered with 401 and never reaches the upstream at all.

Hashes only, never passwords. `caddy hash-password` prints what belongs
in `users`:

```
docker run --rm -it caddy:2-alpine caddy hash-password
```

The generated configuration is mode 0600, and the role writes it with
diffing switched off: `--diff` would otherwise put every hash into the
run's transcript, and a transcript travels further than a file on the
server does.

**An empty `users` list denies everyone.** The site keeps its name, its
certificate and its shape, and answers 403 to every request; the upstream
is not even configured into that site's route. This is the deliberate
default for a host whose credentials are handed out one by one: the host
exists and is closed, rather than existing and being open until someone
remembers to close it.

## A name that does not point here yet

`tls: internal` is for a site that is fully configured but whose DNS
still points somewhere else — the state a migration is in until the
records are moved. It uses a certificate from Caddy's own local CA, which
nobody trusts and nothing has to be asked for. The cutover is changing
that one word to `acme` and restarting.

Leaving it at `acme` is not dangerous either, and worth knowing about
because it is the behaviour you get if you forget: Caddy manages
certificates in the background, so nothing blocks startup and no other
site is affected. It retries a failing name — briefly at first, then with
exponential backoff, at most a day apart, for up to 30 days — and every
one of those attempts is a failed validation recorded against your
account at the certificate authority, where failed validations are
themselves rate-limited. For a name that is known not to resolve here
yet, `internal` costs nothing and asks nobody.

## Large files and byte ranges

Nothing here buffers a response: Caddy's request and response buffers are
off by default and this role does not turn them on, so a 206 and its
`Content-Range` pass through as they came and a resumed download of a
several-hundred-megabyte package works. Compression is not enabled
either, which matters for the same reason — Caddy's `encode` strips
`Accept-Ranges` from anything it compresses on the fly.

## Certificates survive the container

`/data` is a named volume. It holds the certificates, their private keys
and the ACME account, and it is what keeps a container being replaced
from turning into a fresh application for every certificate — with rate
limits at the far end that are real. The role creates the volume itself
rather than leaving it to `docker run`, so it also exists when a run
never gets as far as starting anything.

## Networking, the firewall, and the mount

The container runs with `--network host`, for the reasons the
`registry_web` README spells out: a published port bypasses the host's
input rules, and the userland proxy rewrites the client address on IPv6.

Unlike the web server, this unit carries **no** dependency on the
registry filesystem. The proxy serves no files of its own. With the
registry gone it answers 502, which is a clear message and keeps the
certificates being renewed; a proxy that refused to start would take
every certificate on the host down with a volume that failed to mount.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `registry_proxy_image` | `docker.io/library/caddy:2-alpine` | Image to run. May carry a digest. |
| `registry_proxy_sites` | `[]` | The virtual hosts. See above. |
| `registry_proxy_acme_email` | `""` | Address the certificate authority may reach you at. |
| `registry_proxy_acme_ca` | `""` | A different ACME directory, e.g. a staging endpoint. |
| `registry_proxy_http3` | `false` | HTTP/3 over UDP 443. |
| `registry_proxy_hsts` | `false` | Send `Strict-Transport-Security`. |
| `registry_proxy_hsts_max_age` | `31536000` | How long that promise lasts. |
| `registry_proxy_config_path` | `/etc/packagetool/Caddyfile` | Generated configuration. Mode 0600: it holds the credential hashes. |
| `registry_proxy_service` | `packagetool-proxy` | Unit name, without the suffix. |
| `registry_proxy_container` | `packagetool-proxy` | Container name. |
| `registry_proxy_data_volume` | `packagetool-proxy-data` | Volume holding the certificates. |
| `registry_proxy_config_volume` | `packagetool-proxy-config` | Volume Caddy autosaves its config to. |
| `registry_proxy_validate_config` | `true` | Check the configuration with the image's Caddy before writing it. |
| `registry_proxy_docker` | `/usr/bin/docker` | The docker client. |
| `registry_proxy_docker_service` | `docker.service` | The unit the container unit depends on. |

## If a run stops

- *the configuration was refused* — the run stops at the template step
  with Caddy's own message. `caddy validate` loads and provisions every
  module, so it catches more than a syntax error; it starts no listener
  and asks no certificate authority anything. The previous configuration
  is untouched.
- *a site does not answer on its bind address* — the check at the end of
  the role makes a plain HTTP request to the first bound address of every
  site and expects the redirect to HTTPS. A connection refused there
  means the address is not on the host or something else holds the port;
  a 404 means Caddy is up but does not know that name.
- *a certificate does not appear* — `journalctl -u packagetool-proxy`.
  For a public certificate the name has to resolve to one of the bound
  addresses and port 80 has to be open on it.

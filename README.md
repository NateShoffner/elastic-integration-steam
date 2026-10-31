# Steam Integration for Elastic

[![CI](https://github.com/NateShoffner/elastic-integration-steam/actions/workflows/ci.yml/badge.svg)](https://github.com/NateShoffner/elastic-integration-steam/actions/workflows/ci.yml)

An Elastic Agent integration for [Steam](https://store.steampowered.com), the
Valve game platform. It collects two unrelated things, split across two policy
templates so a deployment only sees the configuration it needs:

**Steam client logs** (`filestream`, from the machine Steam runs on):

| Data stream | What it gives you |
|---|---|
| `gameprocess` | Game launch and exit events, per app, with command lines and exit codes |
| `content` | Installs, updates, depot downloads, CDN connections, LAN cache peers |
| `cloud` | Steam Cloud save sync passes and per-file sync decisions |
| `connection` | Connection-manager ping latency and load, logons, disconnects, reconnects |
| `workshop` | Workshop subscriptions, item change feeds, download jobs |

**Steam Web API** (`cel`, polling `api.steampowered.com`):

| Data stream | What it gives you |
|---|---|
| `player_summary` | Presence, persona state, and the game a player is currently in |
| `recently_played` | Per-app playtime over the rolling two-week window |
| `owned_game` | Per-app lifetime playtime and last-played time |

The client-log streams need Elastic Agent installed on the machine running
Steam. The Web API streams run anywhere and need a free
[Steam Web API key](https://steamcommunity.com/dev/apikey) plus the 64-bit
Steam IDs you want to poll. The three Web API streams are `metrics` data
streams with TSDB enabled, keyed on `steam.player.id` and `steam.app.id`.

Requires **Kibana 8.19 or later**.

**[Read the integration documentation](packages/steam/docs/README.md)** for
setup steps, configuration options, exported fields, and troubleshooting.

## Correlating client logs with Web API data

Both collection paths populate the same two join keys, which is the point of
shipping them in one package:

- `steam.app.id` — the Steam application ID, a `keyword` in every stream.
- `steam.player.id` — the SteamID64, a `keyword` in every stream.

The client logs only ever record a 32-bit *account ID* (as `11223344`, or in
the Steam2 textual form `[U:1:11223344]`), so the `cloud`, `connection` and
`workshop` pipelines derive the SteamID64 from it
(`76561197960265728 + accountID`) and keep the raw account ID in a separate
field. That means a `gameprocess` launch event joins directly against the
`owned_game` playtime series for the same player and app.

## Installation

Download the `.zip` from the [latest release](https://github.com/NateShoffner/elastic-integration-steam/releases/latest)
and upload it through **Kibana > Integrations > Upload integration**, or:

```sh
elastic-package install --zip steam-<version>.zip
```

To build it yourself instead:

```sh
cd packages/steam
elastic-package build     # writes build/packages/steam-<version>.zip
elastic-package install
```

## Development

Requires [`elastic-package`](https://github.com/elastic/elastic-package) and
Docker. Run from `packages/steam`:

```sh
elastic-package check            # lint and build
elastic-package stack up -d -v   # needed by the test commands below
elastic-package test pipeline    # ingest pipeline tests
elastic-package test system      # end-to-end, per data stream
```

Edit documentation in `_dev/build/docs/README.md`, never in `docs/README.md`,
which is regenerated on every build.

### Sample data

`samples/` holds one anonymized log file per client-log data stream. They were
derived from a real Steam install and scrubbed of SteamIDs, persona names,
Windows usernames, local drive paths, LAN hostnames and library contents, then
reduced to a representative line per observed message shape. They are the
source for both the pipeline test fixtures and the system test mock logs, so
keep the two in sync when editing either.

Every shape in those files was verified against a real install rather than
inferred from documentation, which matters more than it sounds: Steam's logs
print the same address twice on CDN lines (not a remote/local pair), use
comma- and space-separated flag sets where a single token looks likely
(`(init,eval,)`, `(downloading  )`), and wrap tracked-process command lines in
an outer quote pair around an already-quoted executable path. `notes/research-brief.md`
documents the formats and the traps.

Raw, unscrubbed captures belong in `samples/private/`, which is gitignored.
Never commit them.

### Releasing

Bump `version` in `packages/steam/manifest.yml`, add a matching
`changelog.yml` entry, then push a tag:

```sh
git tag v0.2.0 && git push origin v0.2.0
```

CI verifies the tag matches the manifest version, runs the tests, builds the
package, and publishes a GitHub release with the `.zip` attached, a build
provenance attestation, and notes drawn from the changelog entry.

Both CI and the release run on the shared reusable workflows in
[`NateShoffner/elastic-package-workflows`](https://github.com/NateShoffner/elastic-package-workflows).

## License

[Elastic License 2.0](LICENSE). Steam and Valve are trademarks of Valve
Corporation. This project is not affiliated with Valve or Elastic.

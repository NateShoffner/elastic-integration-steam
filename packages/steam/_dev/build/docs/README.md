{{- generatedHeader }}
# Steam Integration for Elastic

## Overview

The Steam integration for Elastic collects telemetry about [Steam](https://store.steampowered.com), the Valve game platform, from two independent sources: the log files the Steam desktop client writes on the machine it runs on, and the public Steam Web API.

Together they answer questions neither source can answer alone: the client logs record what actually happened on a machine (which game launched, when it exited and with what code, what downloaded, whether cloud saves synced), while the Web API records account-level state (who is online, what they are playing, how much they have played each title).

### Compatibility

This integration is compatible with the current Steam desktop client on Windows, Linux and macOS. The client-log data streams were developed against client build `1792618285`; Valve does not version these log formats, and the parsers are written to keep unrecognised lines intact rather than fail, so client updates should not break collection.

The Web API data streams use the public `api.steampowered.com` endpoints and require a Steam Web API key. No Steamworks partner or publisher access is needed.

### How it works

The integration ships two policy templates, so a deployment only sees the configuration it actually needs:

- **Steam Client Logs** uses the `filestream` input to tail the plain-text logs under the Steam installation directory. Elastic Agent must run on the machine where Steam is installed.
- **Steam Web API** uses the `cel` input to poll `api.steampowered.com` for a configured list of Steam accounts. It can run from anywhere with outbound HTTPS and does not require Steam to be installed.

The two paths share two join keys, `steam.app.id` and `steam.player.id`, but neither key is present on every data stream, because the client logs only carry what each line happens to record. `steam.app.id` appears on `gameprocess`, `content`, `cloud` and `workshop` and on all three Web API data streams; `connection` has no application context at all. `steam.player.id` appears on all three Web API data streams and on the `cloud`, `connection` and `workshop` lines that carry an account tag. Those client logs record only a 32-bit account ID, so the integration derives the SteamID64 from it (`76561197960265728 + accountID`) and keeps the raw account ID in a separate field.

## What data does this integration collect?

The Steam integration collects log messages and metrics of the following types:

From the Steam client, as `logs` data streams:

* `gameprocess` — game launch and exit events, tracked per application, including the process command line and exit code.
* `content` — application installs, updates, depot downloads, CDN connections, local file validation, and LAN content-cache peers.
* `cloud` — Steam Cloud save synchronisation passes and per-file sync, upload and delete decisions.
* `connection` — connection-manager ping latency and reported load, connection establishment and teardown, logon results, connectivity probes, and automatic reconnects.
* `workshop` — Workshop subscription refreshes, item change feeds, item detail lookups, and download jobs.

From the Steam Web API, as `metrics` data streams:

* `player_summary` — persona name, presence state, profile visibility, and the application a player is currently in.
* `recently_played` — per-application playtime over Steam's rolling two-week window.
* `owned_game` — per-application lifetime and per-platform playtime, plus when each title was last played.

The three Web API data streams are time series (TSDB) data streams, dimensioned on `steam.player.id` and, where applicable, `steam.app.id`.

### Supported use cases

- Track how long is actually spent in each game on a machine, and reconcile it against Steam's own playtime accounting.
- Detect games that crash: `gameprocess` records a non-zero process exit code when a title exits abnormally.
- Monitor Steam's health as a dependency: connection-manager ping latency and load, connectivity-probe outcomes, and reconnect attempts all surface in `connection`.
- Watch download and update activity, including CDN endpoints used, achieved download rates, failed manifest requests, and file-validation problems.
- Confirm Steam Cloud saves are syncing, and spot accounts or titles where sync is repeatedly deferred.
- Follow Workshop content: which items are added, removed or updated, and which item detail lookups are being denied.
- Query presence across a group of accounts, showing who is online and what they are playing. This release ships no Kibana dashboards; the data streams are designed to be charted directly in Discover or Lens.

## What do I need to use this integration?

For the **Steam Client Logs** policy template:

- Elastic Agent installed on the machine running the Steam client, with read access to the Steam installation directory.

For the **Steam Web API** policy template:

- A Steam Web API key, issued free at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey). A key is tied to a Steam account and a domain, and is rate limited to 100,000 calls per day.
- The 64-bit Steam IDs (SteamID64) of the accounts to poll.
- Outbound HTTPS access to `api.steampowered.com`.

Note that profile privacy governs what the Web API returns. A private profile still responds successfully, but with far fewer fields, and its owned and recently played games come back empty. Playtime data is only available for accounts whose game details are public.

## How do I deploy this integration?

### Agent-based deployment

Elastic Agent must be installed. For more details, check the Elastic Agent [installation instructions](https://www.elastic.co/guide/en/fleet/current/elastic-agent-installation.html). You can install only one Elastic Agent per host.

For the client-log data streams, Elastic Agent must be installed on the same host as the Steam client, because it reads Steam's log files directly from disk. The Web API data streams make outbound HTTPS calls and can be assigned to any agent.

### Set up steps in Steam

For the client-log data streams, no configuration is needed inside Steam. The client writes these logs by default. You only need the path to the Steam installation directory:

- Windows: `C:\Program Files (x86)\Steam` by default, but very frequently relocated to another drive. The authoritative value is in the registry at `HKEY_CURRENT_USER\Software\Valve\Steam\SteamPath`.
- Linux: `~/.steam/steam` or `~/.local/share/Steam`.
- macOS: `~/Library/Application Support/Steam`.

Steam rotates these logs by renaming the current file to `<name>.previous.txt` and starting a fresh one. The integration deliberately collects only the current `<name>_log.txt` files; collecting the `.previous.txt` files as well would ingest every line twice.

For the Web API data streams:

1. Sign in at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) and register a key.
2. Collect the SteamID64 of each account to poll. It is the 17-digit number in a profile URL of the form `https://steamcommunity.com/profiles/<SteamID64>`. For a profile using a custom vanity URL, resolve it with the `ISteamUser/ResolveVanityURL` endpoint.
3. Confirm that each account's profile and game details are set to public, otherwise playtime will not be returned.

#### Vendor resources

- [Steam Web API key registration](https://steamcommunity.com/dev/apikey)
- [Steamworks Web API overview](https://partner.steamgames.com/doc/webapi_overview)
- [Steamworks IPlayerService reference](https://partner.steamgames.com/doc/webapi/IPlayerService)

### Set up steps in Kibana

1. In Kibana, go to **Management > Integrations**, search for **Steam**, and select **Add Steam**.
2. Choose the policy template you need. The two are independent and can be added to different agent policies.

For **Steam Client Logs**, each of the five data streams can be enabled independently and exposes:

| Option | Description |
|---|---|
| Paths | Log files to collect. Defaults cover the standard Windows, Linux and macOS locations; change this if Steam is installed elsewhere. |
| Exclude Files | Patterns to ignore. Defaults exclude the rotated `.previous.txt` files, which would otherwise duplicate every line. |
| Preserve original event | Keeps the raw log line in `event.original`. |
| Tags | Tags added to every event from the data stream. |
| Condition | Restricts collection to agents matching a condition, which is useful when one policy covers several platforms, for example `${host.platform} == 'windows'`. |

For **Steam Web API**, the key and account list are configured once and shared by all three data streams:

| Option | Description |
|---|---|
| Steam Web API Key | The key registered above. Stored as a secret. |
| Steam IDs | One or more SteamID64 values to poll. |
| Steam Web API URL | Defaults to `https://api.steampowered.com`; change it only to point at a proxy. |

Each Web API data stream additionally has its own collection interval. The defaults reflect how quickly each kind of data actually changes: presence changes within minutes, two-week playtime within hours, and an owned-games library slowly. Raising them costs API quota; see Performance and scaling.

3. Select **Save and continue**, then assign the policy to an agent.

### Validation

To confirm data is flowing:

1. In Kibana, open **Discover**.
2. For the client-log data streams, query `logs-steam.*`. Launching and then quitting a game is the quickest way to generate activity: it produces `gameprocess` documents with `event.category: process` and `event.type: start`, then a matching `event.type: end` carrying `process.exit_code`.
3. For the Web API data streams, query `metrics-steam.*`. Documents appear on the first poll after the policy is applied, which can take up to the configured interval.
4. Confirm correlation works by picking a `steam.app.id` present in both, for example comparing a `gameprocess` session against the `owned_game` playtime for the same `steam.player.id`.

## Troubleshooting

- No client log data is collected: Confirm the Steam installation path. The default Windows path is frequently wrong because users relocate Steam to another drive; check `HKEY_CURRENT_USER\Software\Valve\Steam\SteamPath` and update the Paths option. Confirm the agent's user can read the `logs` directory.

- Events appear duplicated: Check that the Exclude Files option still excludes `\.previous\.txt$`. Steam rotates by renaming the active log to `.previous.txt`, so collecting both files ingests every line twice.

- Timestamps are offset by hours: Steam writes local time with no timezone or offset. The integration resolves this using the timezone reported by the agent host, so a mismatch usually means the agent host's clock or timezone is wrong, not the parser.

- No Web API data and the agent log shows a 403: The API key is invalid, revoked, or not yet active. Steam returns an HTML error body rather than JSON for this case. Re-register the key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey).

- Web API data arrives for presence but not playtime: The account's profile or game details are private. Steam does not report an error for this; it returns a successful response with no games. Set **Game details** to Public in the account's Steam privacy settings.

- A Web API data stream returns nothing for one account only: Confirm the value is a SteamID64, not a vanity name or a 32-bit account ID. A malformed ID returns HTTP 400 for that account while the others continue normally.

- Playtime looks too low for an old account: `playtime_forever` only counts play since early 2009, when Steam began tracking it. Earlier play is not recorded by Steam and cannot be recovered.

- Per-platform playtimes do not add up to the total: This is expected. Steam added per-platform tracking later than the overall total, so `steam.playtime.windows.minutes` and its siblings genuinely do not sum to `steam.playtime.forever.minutes`. Do not derive one from the others.

## Performance and scaling

The client-log data streams are ordinary file tailing and scale with how busy Steam is. Two of the five are much noisier than the rest: `connection` is dominated by a single repeated client-update check that accounts for roughly a fifth of its volume, and `content` by LAN peer-server discovery. Both can be dropped with a `drop_event` processor via the data stream's Processors option. That option runs in the agent, before the ingest pipeline parses the line, so match on the raw `message` rather than on `event.action`:

```yaml
- drop_event:
    when:
      regexp:
        message: 'CClientJobGetClientUpdateHosts|(Adding|Removing) peer server'
```

The Web API data streams consume API quota, which is limited to 100,000 calls per key per day. The call cost differs sharply by data stream, because only one of the three endpoints batches:

- `player_summary` batches up to 100 accounts per request, so it costs `ceil(accounts / 100)` calls per poll.
- `recently_played` and `owned_game` take a single account per request, so each costs one call **per account** per poll.

With the default intervals, a handful of accounts uses a negligible fraction of the quota. If you are polling many accounts, raise the `recently_played` and `owned_game` intervals rather than `player_summary`'s, since those are the ones that scale linearly with account count.

Document volume differs too: `owned_game` emits one document per owned game per account per poll, so an account with a large library produces hundreds of documents each poll. The three Web API data streams are TSDB data streams with synthetic source, which keeps the storage cost of these repeated snapshots low, and documents sharing identical dimensions and timestamp are deduplicated automatically.

For more information on architectures that can be used for scaling this integration, check the [Ingest Architectures](https://www.elastic.co/docs/manage-data/ingest/ingest-reference-architectures) documentation.

## Reference

### Inputs used
{{ inputDocs }}

### API usage

These APIs are used with this integration:

* [`ISteamUser/GetPlayerSummaries/v2`](https://partner.steamgames.com/doc/webapi/ISteamUser) — account presence and profile state, for the `player_summary` data stream. Accepts up to 100 SteamID64 values per request.
* [`IPlayerService/GetRecentlyPlayedGames/v1`](https://partner.steamgames.com/doc/webapi/IPlayerService) — two-week playtime, for the `recently_played` data stream. One account per request.
* [`IPlayerService/GetOwnedGames/v1`](https://partner.steamgames.com/doc/webapi/IPlayerService) — owned games and lifetime playtime, for the `owned_game` data stream. One account per request, called with `include_appinfo` and `include_played_free_games` enabled.

None of these endpoints paginate; every poll retrieves a complete snapshot. All `playtime` values they return are in minutes.

### Vendor documentation links

- [Steamworks Web API overview](https://partner.steamgames.com/doc/webapi_overview)
- [Steam Web API key registration](https://steamcommunity.com/dev/apikey)
- [Steam Support: profile privacy settings](https://help.steampowered.com/en/faqs/view/588C-C67D-0251-C276)

### Data streams

#### gameprocess

The `gameprocess` data stream provides events from the Steam client's `gameprocess_log.txt` covering the operating system processes Steam launches for each application: process start, process exit with its exit code, and removal of an application from the running list. One application commonly spawns several tracked processes, such as the game itself plus a launcher and a crash handler, so a play session is not a single process.

##### gameprocess fields

{{ fields "gameprocess" }}

##### gameprocess sample event

{{ event "gameprocess" }}

#### content

The `content` data stream provides events from the Steam client's `content_log.txt` covering application install and update state transitions, depot configuration changes, CDN and local-cache download connections, achieved download rates, file validation results, and Steam library folder scans.

##### content fields

{{ fields "content" }}

##### content sample event

{{ event "content" }}

#### cloud

The `cloud` data stream provides events from the Steam client's `cloud_log.txt` covering Steam Cloud save synchronisation: the start and completion of sync passes, per-file in-sync, upload, persist and delete decisions, and local cache writes. Note that Steam uses two different path conventions in this log, a cloud-relative path for remote storage and an absolute local path for files on disk, and the integration keeps them in separate fields.

##### cloud fields

{{ fields "cloud" }}

##### cloud sample event

{{ event "cloud" }}

#### connection

The `connection` data stream provides events from the Steam client's `connection_log.txt` covering connectivity to Steam's connection managers: connection-manager list retrieval, per-endpoint ping latency and reported load, ping failures, connection establishment and disconnection, logon results, connectivity probes, and automatic reconnect scheduling.

This log contains a large amount of internal client diagnostic output. The integration parses the shapes that carry useful signal and preserves every other line in `message` without categorising it.

##### connection fields

{{ fields "connection" }}

##### connection sample event

{{ event "connection" }}

#### workshop

The `workshop` data stream provides events from the Steam client's `workshop_log.txt` covering Steam Workshop content: installed item scans, subscription list refreshes, item change feeds, item detail lookups including denied ones, detected subscription and cache changes, and download jobs. Workshop item IDs are published-file IDs, a separate identifier space from application IDs, and are kept in their own field.

##### workshop fields

{{ fields "workshop" }}

##### workshop sample event

{{ event "workshop" }}

#### player_summary

The `player_summary` data stream provides a periodic snapshot of each configured account from `ISteamUser/GetPlayerSummaries`: persona name, presence state, profile visibility, avatar, account creation time, optional location, and the application the account is currently in.

Two caveats are worth knowing. A private profile is reported as offline, so offline and unreadable are indistinguishable without also checking `steam.player.visibility.code`. And the current-application fields are only present while the account is actually in a game; for a non-Steam shortcut added to the library, the reported application ID is a synthetic value rather than a real Steam application ID.

##### player_summary fields

{{ fields "player_summary" }}

##### player_summary sample event

{{ event "player_summary" }}

#### recently_played

The `recently_played` data stream provides a periodic snapshot of playtime over Steam's rolling two-week window from `IPlayerService/GetRecentlyPlayedGames`, as one document per account and application. All playtime values are in minutes.

A title leaving the two-week window is recorded by the absence of a document rather than by a zero value.

##### recently_played fields

{{ fields "recently_played" }}

##### recently_played sample event

{{ event "recently_played" }}

#### owned_game

The `owned_game` data stream provides a periodic snapshot of each configured account's library from `IPlayerService/GetOwnedGames`, as one document per account and application, with lifetime and per-platform playtime and the time each title was last played. All playtime values are in minutes.

A title that has never been launched has no last-played value at all, rather than a zero or epoch timestamp.

##### owned_game fields

{{ fields "owned_game" }}

##### owned_game sample event

{{ event "owned_game" }}

{{ ilm }}

{{ transform }}

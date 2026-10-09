# Dispatcharr → Music Assistant Bridge v5.3

v5.3 is based on the **standalone v5.0 playback architecture** and adds fast, free
SiriusXM now-playing metadata using the same bulk feeds used by the current
Dispatcharr Ticker plugin.

## What stays from v5.0

The playback core is intentionally unchanged in principle:

- one shared hub per channel + output format
- one FFmpeg process per active hub
- FFmpeg owns HTTP reconnects internally
- the bridge respawns FFmpeg only after the process exits and demand remains
- no playback stall → kill → respawn loop
- maximum upstream connections defaults to 2
- 30-second playback linger
- startup silence for cold Dispatcharr streams
- sequential Music Assistant imports
- import pin/release grace
- persistent channel catalog cache in `/data/bridge-cache.json`

This is the v5 behavior that avoids repeated Dispatcharr connect/disconnect churn.

## New in v5.3: isolated ICY metadata + full diagnostics

v5.3 keeps the v5 playback core intact and changes the metadata path so a Ticker/xmplaylist
refresh can never perform network work in the audio delivery loop. Metadata providers build
complete ICY blocks in the background and atomically swap immutable cached bytes. The stream
path only counts audio bytes and inserts a cached block after exactly `ICY_METAINT` audio bytes.

This is designed to eliminate metadata-refresh pauses as a possible cause of audible skips or
repeats. You can also disable in-band ICY injection without disabling metadata collection:

```env
METADATA_ENABLED=true
ICY_METADATA_ENABLED=true
ICY_METAINT=16384
```

For an A/B test, set `ICY_METADATA_ENABLED=false`. Ticker/xmplaylist metadata continues to be
available through `/metadata/<channel-id>` and `/channels`, but no ICY blocks are inserted into
the audio response.

### API and dependency diagnostics

v5.3 adds non-destructive health checks for every external dependency used by the bridge.
`GET /api/checks` and `POST /api/checks/run` perform a fresh check and return one JSON document.
The default timeout for each external check is controlled by:

```env
API_CHECK_TIMEOUT=10
```

The report includes:

- **Dispatcharr/XC** — XC authentication, `player_api.php`, live categories, live streams,
  response latency, HTTP status, and filtered catalog count. In Dispatcharr-M3U mode it checks
  the configured playlist URL, response status, size, latency, and current filtered catalog.
- **Music Assistant** — API reachability, bearer-token authentication, and read access to the
  radio library. The diagnostic intentionally does **not** add or delete a radio just to prove
  write/remove permission; those fields are reported as `not_mutated_by_health_check`.
- **Ticker/SiriusXM metadata** — `channels.json`, `nowplaying.json`, HTTP status, latency, row
  counts, current polling state, and number of locally cached metadata matches.
- **xmplaylist fallback** — `/api/station` and `/api/feed`, HTTP status, latency and returned rows.
- **FFmpeg** — executable presence, FFprobe presence, FFmpeg version, AAC encoder availability,
  libmp3lame availability, and configured AAC mode.
- **Bridge** — version, persistent-cache path/writability, catalog size, metadata source, ICY
  state, and current shared-hub statistics.

Examples:

```bash
curl http://BRIDGE:8088/api/checks
curl -X POST http://BRIDGE:8088/api/checks/run
```

A healthy top-level response looks like:

```json
{
  "status": "ok",
  "failed": [],
  "checks": {
    "source": {"status": "ok"},
    "music_assistant": {"status": "ok"},
    "ticker": {"status": "ok"},
    "xmplaylist": {"status": "ok"},
    "ffmpeg": {"status": "ok"},
    "bridge": {"status": "ok"}
  }
}
```

If one dependency fails, the overall status becomes `degraded` and its name appears in
`failed`. Credentials are redacted from returned errors/logs.

### Per-stream diagnostic

Use the stream-specific endpoint to inspect one channel without opening a new source:

```bash
curl http://BRIDGE:8088/api/checks/stream/xc-23261
```

By default this reports catalog identity, current metadata, and an already-active hub if one
exists. It does not consume one of the limited upstream connections.

To deliberately warm/open the source and verify real audio startup:

```bash
curl 'http://BRIDGE:8088/api/checks/stream/xc-23261?live=true'
```

The live check uses the same shared-hub architecture as Music Assistant, reports startup time,
codec and hub health, and may temporarily use one upstream source slot. Do not run multiple live
checks concurrently when `UPSTREAM_MAX_CONNECTIONS=2`.

### Troubleshooting with the checks

If audio skips but `/api/checks` is healthy, compare playback with `ICY_METADATA_ENABLED=true`
and `false`. If disabling ICY eliminates the skips, leave metadata collection enabled and keep
ICY disabled while inspecting the Music Assistant client's handling of in-band metadata. If the
problem remains with ICY disabled, inspect `/streams` for `dropped_chunks`,
`slow_subscriber_disconnects`, queue depth, recent bitrate, HTTP 503 counts and restart counts.
For one problematic station, run the per-stream check with `?live=true` and compare startup and
codec results with a known-good station.

## New in v5.2: active-stream-only Ticker polling

Ticker now-playing polling is demand-driven. With the defaults below, the bridge does not poll
the bulk now-playing feed while no Sirius stream hub is active. The first listener or Music
Assistant warm-up pin wakes the poller immediately. Fast polling continues while the shared hub
is running, including the normal 30-second linger after the final listener leaves, then stops.

```env
TICKER_ACTIVE_POLL_SECONDS=15
TICKER_IDLE_POLL_SECONDS=0
```

Set `TICKER_IDLE_POLL_SECONDS` to a non-zero value (for example `900`) only if you want an
occasional background now-playing refresh while idle. Channel mapping refresh remains cached
independently and is refreshed when Ticker metadata is next needed.

## Ticker metadata source

The current Dispatcharr Ticker plugin uses these free bulk feeds:

- `https://stellartunerlog.com/nowplaying.json`
- `https://stellartunerlog.com/channels.json`

Ticker itself caches now-playing for 15 seconds. v5.2 uses the same approach directly,
so **you do not need to enable Ticker's video overlay or let Ticker change stream
profiles** just to get metadata.

Recommended settings:

```env
METADATA_PROVIDER=ticker
METADATA_FALLBACK=xmplaylist
TICKER_ACTIVE_POLL_SECONDS=15
TICKER_IDLE_POLL_SECONDS=0
TICKER_CHANNEL_REFRESH_SECONDS=86400
```

If the Ticker bulk feed is unavailable, the bridge can fall back to xmplaylist.

## Build and run

```bash
docker compose up -d --build
```

Check:

```text
http://YOUR-BRIDGE:8088/health
http://YOUR-BRIDGE:8088/channels
http://YOUR-BRIDGE:8088/streams
```

`/health` should show metadata similar to:

```json
{
  "provider": "ticker",
  "active_source": "ticker",
  "fallback_provider": "xmplaylist",
  "ticker_poll_seconds": 15
}
```

## Recommended Dispatcharr audio profile

The AAC-copy path assumes Dispatcharr outputs AAC-LC, 48 kHz, stereo:

```text
-user_agent {userAgent} -i {streamUrl} -vn -map 0:a:0 -c:a aac -b:a 128k -ar 48000 -ac 2 -probesize 500000 -analyzeduration 1000000 -fflags +discardcorrupt+nobuffer -flags low_delay -af aresample=async=1 -muxdelay 0 -muxpreload 0 -f mpegts pipe:1
```

Then use:

```env
AAC_MODE=copy
AAC_BITRATE=128k
SAMPLE_RATE=48000
CHANNELS=2
```

## v5 reconnect behavior

The input FFmpeg command uses reconnect support for streamed HTTP sources, EOF,
network errors, and transient HTTP errors, with a short maximum reconnect delay.
The bridge does not kill an FFmpeg process merely because no bytes arrived for a
short diagnostic stall interval.

If FFmpeg actually exits while listeners or an import pin remain, the hub respawns it.
If there is no demand, it does not restart an idle hub.

## Persistent cache

The catalog is stored in:

```text
/data/bridge-cache.json
```

The included compose file persists `/data` in the `bridge-data` volume. If a catalog
refresh fails, the last known-good catalog can continue to be served.

## Music Assistant synchronization

For automatic Radio synchronization:

```env
MUSIC_ASSISTANT_URL=http://music-assistant:8095
MUSIC_ASSISTANT_TOKEN=YOUR_TOKEN
MA_AUTO_SYNC=true
MA_SYNC_ON_START=true
MA_SYNC_AFTER_CATALOG_REFRESH=true
MA_REMOVE_MISSING=false
MA_IMPORT_RELEASE_GRACE_SECONDS=11
```

The bridge warms/pins the same shared stream while Music Assistant validates a new
Radio URL, then keeps it pinned for the release-grace period before allowing normal
30-second linger behavior.

Manual sync:

```bash
curl -X POST http://YOUR-BRIDGE:8088/ma/sync
```

## Useful endpoints

- `/health` — service/catalog/metadata/MA state
- `/channels` — filtered channel catalog and now-playing data
- `/streams` — active shared hub diagnostics
- `/metadata/<channel-id>` — metadata debug view
- `/playlist.m3u8` — Music Assistant-ready playlist
- `/stream/<channel-id>.aac` — AAC radio stream
- `/stream/<channel-id>.mp3` — MP3 radio stream

## Security

Keep port 8088 private to your LAN/reverse proxy. XC credentials and configured
secrets are redacted from bridge error/FFmpeg logs.

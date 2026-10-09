# IPTV Music Assistant Bridge

A lightweight Docker bridge that turns Dispatcharr / Xtream Codes IPTV audio channels into radio streams that Music Assistant can use reliably.

The bridge is designed for audio-only IPTV channels such as SiriusXM. It keeps a small number of upstream Dispatcharr connections open, converts or remuxes the source with FFmpeg, exposes AAC and MP3 radio URLs, adds optional now-playing metadata, and can synchronize the resulting stations into Music Assistant automatically.

Current release: **v5.3**

## Features

- Dispatcharr M3U or Xtream Codes / XC source support
- AAC and MP3 radio endpoints for Music Assistant
- Shared upstream stream per channel and format
- Configurable upstream connection limit
- 30-second hot-stream linger to avoid unnecessary reconnects
- Startup silence while a cold IPTV stream opens
- AAC copy mode when Dispatcharr already provides AAC
- Optional SiriusXM now-playing metadata
- Ticker-compatible free SiriusXM metadata source
- xmplaylist fallback
- Optional ICY artist/title injection
- Automatic Music Assistant radio synchronization
- Persistent channel catalog cache
- Health, stream and dependency diagnostics
- FFmpeg/XC credential redaction in bridge logs

## Requirements

- Docker with Docker Compose
- A working Dispatcharr instance
- Music Assistant if you want automatic radio synchronization
- FFmpeg is included in the container

For the recommended configuration, Dispatcharr should output audio-only **AAC-LC, 128 kbps, 48 kHz, stereo**.

## Installation

Clone the repository:

```bash
git clone https://github.com/iceman73/iptv-music-assistant-bridge.git
cd iptv-music-assistant-bridge
```

Create your local environment file:

```bash
cp .env.example .env
```

Edit `.env` with your Dispatcharr/XC and Music Assistant settings, then build and start the container:

```bash
docker compose up -d --build
```

Check that the bridge is running:

```bash
curl http://YOUR-BRIDGE-HOST:8088/health
```

The default bridge port is **8088**.

To update later:

```bash
git pull
docker compose down
docker compose up -d --build
```

## Dispatcharr configuration

### Recommended audio-only stream profile

Create a Dispatcharr stream profile named **audio_only** and use:

```text
-user_agent {userAgent} -i {streamUrl} -vn -map 0:a:0 -c:a aac -b:a 128k -ar 48000 -ac 2 -probesize 500000 -analyzeduration 1000000 -fflags +discardcorrupt+nobuffer -flags low_delay -af aresample=async=1 -muxdelay 0 -muxpreload 0 -f mpegts pipe:1
```

This profile:

- removes video
- selects the first audio stream
- outputs AAC at 128 kbps
- normalizes to 48 kHz stereo
- uses MPEG-TS for the Dispatcharr output
- keeps the stream suitable for the bridge's `AAC_MODE=copy` path

If your provider is already stable AAC, this avoids an additional audio encode inside the bridge.

## Source configuration

The bridge supports two source modes.

### Option 1: Xtream Codes / XC

This is the recommended setup when using Dispatcharr's XC-compatible endpoint.

```env
SOURCE_MODE=xc

XC_BASE_URL=http://dispatcharr:9191
XC_USERNAME=your-xc-username
XC_PASSWORD=your-xc-password
XC_OUTPUT=ts
XC_VERIFY_SSL=true

GROUP_FILTER=SiriusXM
NAME_FILTER=
XC_CATEGORY_IDS=
```

The bridge authenticates to `player_api.php`, loads live categories and streams, then creates the appropriate XC live-stream URL.

### Option 2: Dispatcharr M3U

```env
SOURCE_MODE=dispatcharr

DISPATCHARR_BASE_URL=http://dispatcharr:9191
DISPATCHARR_M3U_PATH=/output/m3u
DISPATCHARR_M3U_URL=

GROUP_FILTER=SiriusXM
NAME_FILTER=
```

Set `DISPATCHARR_M3U_URL` if you want to use a complete custom M3U URL. Otherwise the bridge uses:

```text
DISPATCHARR_BASE_URL + DISPATCHARR_M3U_PATH
```

## Minimum recommended configuration

For an XC-based Dispatcharr installation with Music Assistant:

```env
# Source
SOURCE_MODE=xc
XC_BASE_URL=http://dispatcharr:9191
XC_USERNAME=your-xc-username
XC_PASSWORD=your-xc-password
XC_OUTPUT=ts
XC_VERIFY_SSL=true
GROUP_FILTER=SiriusXM

# Bridge
PUBLIC_BASE_URL=http://YOUR-BRIDGE-HOST:8088
DEFAULT_FORMAT=aac
IMPORT_FORMAT=aac

# Audio
AAC_MODE=copy
AAC_BITRATE=128k
SAMPLE_RATE=48000
CHANNELS=2

# Shared stream behavior
UPSTREAM_MAX_CONNECTIONS=2
STREAM_LINGER_SECONDS=30
STREAM_SUBSCRIBER_QUEUE_CHUNKS=16
STREAM_READ_CHUNK_BYTES=16384
STREAM_RING_BUFFER_SECONDS=2

# Metadata
METADATA_ENABLED=true
METADATA_PROVIDER=ticker
METADATA_FALLBACK=xmplaylist
TICKER_ACTIVE_POLL_SECONDS=15
TICKER_IDLE_POLL_SECONDS=0
ICY_METADATA_ENABLED=true
ICY_METAINT=16384

# Music Assistant
MUSIC_ASSISTANT_URL=http://music-assistant:8095
MUSIC_ASSISTANT_TOKEN=YOUR_LONG_LIVED_TOKEN
MA_AUTO_SYNC=true
MA_SYNC_ON_START=true
MA_SYNC_AFTER_CATALOG_REFRESH=true
MA_REMOVE_MISSING=false
```

## Configuration reference

### Bridge and audio

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | `http://localhost:8088` | URL Music Assistant uses to reach the bridge |
| `DEFAULT_FORMAT` | `aac` | Default playlist format: `aac` or `mp3` |
| `IMPORT_FORMAT` | same as default | Format imported into Music Assistant |
| `AAC_MODE` | `auto` | `copy`, `auto`, or transcode behavior |
| `AAC_BITRATE` | `128k` | AAC bitrate when transcoding |
| `MP3_BITRATE` | `192k` | MP3 output bitrate |
| `SAMPLE_RATE` | `48000` | Output sample rate |
| `CHANNELS` | `2` | Output audio channels |
| `SOURCE_RW_TIMEOUT_SECONDS` | `60` | Upstream read timeout |
| `UPSTREAM_MAX_CONNECTIONS` | `2` | Maximum simultaneous upstream streams |
| `STREAM_LINGER_SECONDS` | `30` | Keep a hub hot after the final listener leaves |
| `STREAM_SUBSCRIBER_QUEUE_CHUNKS` | `16` | Per-listener queue depth |
| `STREAM_READ_CHUNK_BYTES` | `16384` | FFmpeg output read size |
| `STREAM_RING_BUFFER_SECONDS` | `2` | Encoded reconnect pre-roll |
| `STARTUP_SILENCE_ENABLED` | `true` | Send valid silence while a cold source starts |
| `STARTUP_SILENCE_MAX_SECONDS` | `60` | Maximum cold-start silence period |
| `FFMPEG_LOG_LEVEL` | `warning` | FFmpeg log verbosity |

### Catalog and filtering

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROUP_FILTER` | empty | Only include groups containing this text |
| `NAME_FILTER` | empty | Only include channel names containing this text |
| `XC_CATEGORY_IDS` | empty | Optional comma-separated XC category IDs |
| `CATALOG_REFRESH_SECONDS` | `86400` | Normal catalog refresh interval |
| `CATALOG_RETRY_SECONDS` | `3600` | Retry interval after a failed refresh |
| `BRIDGE_CACHE_FILE` | `/data/bridge-cache.json` | Persistent last-known-good catalog |

### SiriusXM metadata

| Variable | Default | Purpose |
| --- | --- | --- |
| `METADATA_ENABLED` | `true` | Enable now-playing collection |
| `METADATA_PROVIDER` | `auto` | Metadata provider; recommended value is `ticker` |
| `METADATA_FALLBACK` | `xmplaylist` | Fallback provider |
| `TICKER_NOWPLAYING_URL` | StellarTunerLog bulk feed | Current SiriusXM now-playing feed |
| `TICKER_CHANNEL_URL` | StellarTunerLog channel feed | SiriusXM channel mapping |
| `TICKER_ACTIVE_POLL_SECONDS` | `15` | Poll interval while a bridge stream is active |
| `TICKER_IDLE_POLL_SECONDS` | `0` | Idle polling; `0` disables it |
| `TICKER_CHANNEL_REFRESH_SECONDS` | `86400` | Channel-map refresh interval |
| `XMPLAYLIST_POLL_SECONDS` | `120` | xmplaylist fallback polling interval |
| `ICY_METADATA_ENABLED` | `true` | Inject artist/title into clients requesting ICY metadata |
| `ICY_METAINT` | `16384` | Audio bytes between ICY metadata blocks |

Ticker/xmplaylist lookups run outside the audio-delivery loop. v5.3 prebuilds the ICY metadata block and only inserts cached bytes into the stream.

For troubleshooting, you can disable in-band metadata without disabling metadata collection:

```env
ICY_METADATA_ENABLED=false
```

### Music Assistant

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUSIC_ASSISTANT_URL` | `http://music-assistant:8095` | Music Assistant API URL |
| `MUSIC_ASSISTANT_TOKEN` | empty | Long-lived MA bearer token |
| `IMPORT_LOGOS` | `true` | Import channel artwork |
| `MA_AUTO_SYNC` | `false` | Enable automatic MA radio synchronization |
| `MA_SYNC_ON_START` | `true` | Sync after bridge startup |
| `MA_SYNC_AFTER_CATALOG_REFRESH` | `true` | Sync after catalog refresh |
| `MA_REMOVE_MISSING` | `false` | Remove bridge-managed stations no longer in the source |
| `MA_SYNC_TIMEOUT` | `90` | MA API timeout |
| `MA_ADD_RETRIES` | `3` | Add-radio retry count |
| `MA_SYNC_CONCURRENCY` | `1` | Number of stations imported concurrently |
| `STREAM_WARMUP_ENABLED` | `true` | Warm new streams before MA validates them |
| `MA_IMPORT_RELEASE_GRACE_SECONDS` | `11` | Keep a stream pinned briefly after MA accepts it |

## How streaming works

```text
Dispatcharr / XC
      ↓
one FFmpeg process per active channel + format
      ↓
shared encoded stream hub
      ├── Music Assistant
      ├── another listener
      └── reconnecting listener
```

FFmpeg owns normal upstream HTTP reconnects. The bridge does not intentionally kill and respawn FFmpeg because of a short gap in received audio. When the last listener disconnects, the hub remains available for `STREAM_LINGER_SECONDS` before the upstream is closed.

## Music Assistant setup

With automatic synchronization enabled, the bridge can add/update the filtered stations itself.

Force a manual sync:

```bash
curl -X POST http://YOUR-BRIDGE-HOST:8088/ma/sync
```

Check sync status:

```bash
curl http://YOUR-BRIDGE-HOST:8088/ma/status
```

Alternatively, Music Assistant can consume the generated playlist:

```text
http://YOUR-BRIDGE-HOST:8088/playlist.m3u8
```

## API endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/` | Bridge summary and endpoint discovery |
| GET | `/health` | Bridge, catalog, metadata, MA and playback health |
| GET | `/channels` | Filtered channel catalog with bridge URLs and now-playing data |
| GET | `/streams` | Active shared hubs, listeners and stream diagnostics |
| GET | `/playlist.m3u8` | Generated radio playlist |
| GET | `/playlist.m3u` | Generated radio playlist |
| GET | `/stream/{channel_id}.aac` | AAC radio stream |
| GET | `/stream/{channel_id}.mp3` | MP3 radio stream |
| GET | `/metadata/{channel_id}` | Metadata mapping and current now-playing data |
| GET | `/probe/{channel_id}` | Probe source audio codec and selected AAC mode |
| POST | `/catalog/refresh` | Force a catalog refresh |
| GET | `/ma/status` | Music Assistant synchronization status |
| POST | `/ma/sync` | Force Music Assistant synchronization |
| GET | `/api/checks` | Run dependency/API diagnostics |
| POST | `/api/checks/run` | Force dependency/API diagnostics |
| GET | `/api/checks/stream/{channel_id}` | Non-disruptive per-channel diagnostic |
| GET | `/api/checks/stream/{channel_id}?live=true` | Open/warm one source and verify real audio |

### Full dependency check

```bash
curl http://YOUR-BRIDGE-HOST:8088/api/checks
```

It checks:

- Dispatcharr/XC connectivity and authentication
- Music Assistant connectivity/authentication
- Ticker/SiriusXM metadata feeds
- xmplaylist fallback
- FFmpeg and FFprobe
- bridge cache and internal stream state

A failed dependency changes the overall result to `degraded`.

### Per-stream check

Without opening a new source:

```bash
curl http://YOUR-BRIDGE-HOST:8088/api/checks/stream/xc-23261
```

To deliberately open/warm the source:

```bash
curl 'http://YOUR-BRIDGE-HOST:8088/api/checks/stream/xc-23261?live=true'
```

The live check can consume one of the configured upstream connection slots, so avoid running several live checks at the same time when `UPSTREAM_MAX_CONNECTIONS=2`.

## Troubleshooting

### Stream skips or repeats

First compare direct Dispatcharr playback with bridge playback.

Then temporarily disable ICY injection:

```env
ICY_METADATA_ENABLED=false
```

Metadata will still be collected and visible through the API. If playback becomes clean, the issue is isolated to the client's handling of in-band ICY metadata.

If the problem remains, inspect:

```text
/streams
/api/checks
/api/checks/stream/{channel_id}?live=true
```

Look for upstream restarts, HTTP errors, queue buildup, dropped chunks, slow-subscriber disconnects and abnormal bitrate.

### Music Assistant will not add a station

Check:

```text
/ma/status
/api/checks
/probe/{channel_id}
```

Make sure `PUBLIC_BASE_URL` is reachable from Music Assistant and the MA token has the necessary permissions.

### Metadata is old or missing

Check:

```text
/metadata/{channel_id}
/health
/api/checks
```

The recommended Ticker metadata source polls every 15 seconds only while a shared stream hub is active. `TICKER_IDLE_POLL_SECONDS=0` disables unnecessary idle polling.

## Security

Keep the bridge on a trusted LAN or behind your reverse proxy/firewall. The streaming and diagnostics endpoints are not intended to be exposed directly to the public Internet.

Do not commit your real `.env` file. XC passwords, Music Assistant tokens and other secrets belong only in your local environment.

The bridge redacts configured credentials from its own error and FFmpeg logs where possible.

## Persistent data

The Docker Compose configuration stores the last-known-good channel catalog in the `bridge-data` volume:

```text
/data/bridge-cache.json
```

This allows the bridge to continue using its cached catalog if a scheduled source refresh temporarily fails.

## License

Licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for project and third-party attribution information.

Copyright 2026 iceman73.

This project is an independent integration tool and is not affiliated with or endorsed by Dispatcharr, Music Assistant, SiriusXM, Amazon, StellarTunerLog, xmplaylist, or their respective owners.

The Apache-2.0 license applies to this project's source code and documentation only. It does not grant rights to third-party audio streams, logos, artwork, metadata, trademarks, subscription services, or other content. Users are responsible for complying with the terms and licensing requirements of the services they connect.

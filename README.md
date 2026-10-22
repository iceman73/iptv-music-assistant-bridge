# IPTV Music Assistant Bridge

A Docker bridge that turns Dispatcharr or Xtream Codes audio channels into stable radio streams for Music Assistant. The default path is tuned for Amazon Echo playback.

**Current release: v6.0.2**

v6 is based on the playback path validated in v5.6.13. v6.0.2 restores that validated FFmpeg/transcode path after the v6.0.1 async-resample experiment caused renewed stuttering. The old Python MP3 pacing, adaptive jitter buffer, learned jitter state, post-start silence gap-fill, and async-resample experiment are not part of the active playback path.

## Architecture

```text
Dispatcharr / XC MPEG-TS
        ↓
Persistent FFmpeg
  reconnect + -readrate 1
        ↓
MP3 192k by default
        ↓
8 KB coalescer
500 ms hard flush deadline
        ↓
Shared station hub
        ↓
Music Assistant
        ↓
Echo / other players
```

FFmpeg consumes the timestamped MPEG-TS source at media rate before encoding MP3. The Python bridge does not pace encoded MP3 bytes and does not inject silence after real playback has started.

## What v6 keeps

- Dispatcharr M3U and Xtream Codes / XC source support
- MP3 and AAC stream endpoints
- MP3 192 kbps default for Echo compatibility
- Persistent FFmpeg per active channel
- FFmpeg reconnect handling
- FFmpeg `-readrate 1` input pacing
- Shared channel/format hubs
- 8 KB write coalescing with a real 500 ms timer
- 30-second hot-stream linger
- 0.5-second reconnect pre-roll ring buffer
- Startup silence while a cold source opens
- Live-playback priority over Music Assistant warmups
- Native Music Assistant sync, migration, audit, and cleanup
- Ticker metadata with xmplaylist fallback
- Optional ICY metadata
- Catalog persistence
- Stream-gap and dependency diagnostics
- Credential redaction in logs

## Removed in v6

These settings and code paths are intentionally gone:

```text
STREAM_OUTPUT_PACING
STREAM_OUTPUT_PACING_SLACK_SECONDS
STREAM_JITTER_BUFFER_*
STREAM_JITTER_STATE_FILE
POST_START_GAP_FILL_*
UPSTREAM_RETRY_INITIAL_SECONDS
UPSTREAM_RETRY_MAX_SECONDS
UPSTREAM_RETRY_FOREVER_WHILE_LISTENING
REAL_AUDIO_STALL_SECONDS
STARTUP_SILENCE_MAX_SECONDS
STARTUP_REAL_AUDIO_RETRIES
STARTUP_REAL_AUDIO_RETRY_DELAY
```

The standalone `import_to_ma.py` v4-era importer is also removed. Music Assistant management is built into the bridge.

Old variables left in an existing local `.env` are harmless, but they are ignored by v6. Cleaning them out is recommended.

## Requirements

- Docker with Docker Compose
- Dispatcharr or an XC-compatible source
- Music Assistant if you want automatic station management
- FFmpeg is included in the container

The recommended Dispatcharr source is audio-only AAC-LC, 128 kbps, 48 kHz, stereo in MPEG-TS.

## Published images and releases

Normal builds from `main` publish:

```text
ghcr.io/iceman73/iptv-music-assistant-bridge:latest
ghcr.io/iceman73/iptv-music-assistant-bridge:sha-<git-commit>
```

Formal releases use a semantic Git tag such as `v6.0.0`. A successful tagged build publishes:

```text
ghcr.io/iceman73/iptv-music-assistant-bridge:6.0.0
ghcr.io/iceman73/iptv-music-assistant-bridge:6.0
ghcr.io/iceman73/iptv-music-assistant-bridge:6
ghcr.io/iceman73/iptv-music-assistant-bridge:latest
ghcr.io/iceman73/iptv-music-assistant-bridge:sha-<git-commit>
```

The tagged workflow also creates the matching GitHub Release with generated release notes. The release tag must match the version declared in `app.py`; the workflow fails rather than publishing a mismatched version.

Use semantic versioning from v6 forward:

- patch release: `6.0.1` for fixes
- minor release: `6.1.0` for backward-compatible features
- major release: `7.0.0` for breaking or architectural changes

For reproducible deployments, pin the full release tag:

```yaml
image: ghcr.io/iceman73/iptv-music-assistant-bridge:6.0.0
```

Use `latest` only when you intentionally want the newest successful main/release build.

## Install

```bash
git clone https://github.com/iceman73/iptv-music-assistant-bridge.git
cd iptv-music-assistant-bridge
cp .env.example .env
docker compose up -d --build
```

For a deployment already using the published image:

```bash
docker compose pull
docker compose up -d --force-recreate
```

Check the bridge:

```bash
curl http://YOUR-BRIDGE-HOST:8088/health
curl http://YOUR-BRIDGE-HOST:8088/streams
```

## Recommended Dispatcharr audio profile

Use an audio-only profile similar to:

```text
-user_agent {userAgent} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 2 -i {streamUrl} -vn -map 0:a:0 -c:a aac -b:a 128k -ar 48000 -ac 2 -probesize 1000000 -analyzeduration 2000000 -fflags +discardcorrupt+genpts -muxdelay 0 -muxpreload 0 -mpegts_flags +resend_headers+initial_discontinuity -f mpegts pipe:1
```

The bridge then opens that MPEG-TS source with its own persistent FFmpeg process and adds `-readrate 1` before `-i`.

## Source configuration

### Xtream Codes / XC

```env
SOURCE_MODE=xc
XC_BASE_URL=http://dispatcharr:9191
XC_USERNAME=your-user
XC_PASSWORD=your-password
XC_OUTPUT=ts
XC_VERIFY_SSL=true
XC_CATEGORY_IDS=
GROUP_FILTER=SiriusXM
NAME_FILTER=
```

The bridge reads XC categories and live streams, then builds the normal live-stream URL for each matching channel.

### Dispatcharr M3U

```env
SOURCE_MODE=dispatcharr
DISPATCHARR_BASE_URL=http://dispatcharr:9191
DISPATCHARR_M3U_PATH=/output/m3u
GROUP_FILTER=SiriusXM
NAME_FILTER=
```

You can use `DISPATCHARR_M3U_URL` instead of the base/path pair when needed.

## Minimum recommended v6 configuration

```env
SOURCE_MODE=xc
XC_BASE_URL=http://dispatcharr:9191
XC_USERNAME=your-user
XC_PASSWORD=your-password
GROUP_FILTER=SiriusXM

PUBLIC_BASE_URL=http://bridge:8088
DEFAULT_FORMAT=mp3
MP3_BITRATE=192k

UPSTREAM_MAX_CONNECTIONS=2
STREAM_LINGER_SECONDS=30
STREAM_COALESCE_BYTES=8192
STREAM_COALESCE_MAX_SECONDS=0.50
STREAM_RING_BUFFER_SECONDS=0.5
STARTUP_SILENCE_ENABLED=true

METADATA_ENABLED=true
METADATA_PROVIDER=ticker
METADATA_FALLBACK=xmplaylist
ICY_METADATA_ENABLED=true

MUSIC_ASSISTANT_URL=http://music-assistant:8095
MUSIC_ASSISTANT_TOKEN=your-token
IMPORT_FORMAT=mp3
MA_AUTO_SYNC=true
MA_SYNC_ON_START=true
```

## Configuration reference

### Audio and stream behavior

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_FORMAT` | `mp3` | Default bridge format |
| `MP3_BITRATE` | `192k` | MP3 output bitrate |
| `AAC_BITRATE` | `128k` | AAC transcode bitrate |
| `AAC_MODE` | `auto` | AAC copy/transcode behavior |
| `SAMPLE_RATE` | `48000` | Output sample rate |
| `CHANNELS` | `2` | Stereo output |
| `SOURCE_RW_TIMEOUT_SECONDS` | `60` | FFmpeg source read timeout |
| `UPSTREAM_STARTUP_TIMEOUT_SECONDS` | `30` | Maximum cold-start wait for real audio |
| `UPSTREAM_MAX_CONNECTIONS` | `2` | Maximum concurrent source FFmpeg sessions |
| `STREAM_LINGER_SECONDS` | `30` | Keep an idle station hot briefly |
| `STREAM_SUBSCRIBER_QUEUE_CHUNKS` | `16` | Per-client queue capacity |
| `STREAM_RESTART_DELAY` | `1` | Delay before respawning exited FFmpeg |
| `STREAM_READ_CHUNK_BYTES` | `16384` | FFmpeg stdout read size |
| `STREAM_COALESCE_BYTES` | `8192` | Preferred downstream write size |
| `STREAM_COALESCE_MAX_SECONDS` | `0.50` | Hard deadline for a partial coalesced write |
| `STREAM_RING_BUFFER_SECONDS` | `0.5` | Short pre-roll for reconnecting clients |
| `STARTUP_SILENCE_ENABLED` | `true` | Feed valid audio while a cold source opens |

`-readrate 1` is part of the v6 FFmpeg command and is not an environment switch.

### Catalog

| Variable | Default | Purpose |
| --- | --- | --- |
| `CATALOG_REFRESH_SECONDS` | `86400` | Normal source-catalog refresh |
| `CATALOG_RETRY_SECONDS` | `3600` | Retry delay after refresh failure |
| `GROUP_FILTER` | empty | Regex filter for source group |
| `NAME_FILTER` | empty | Regex filter for channel name |

The bridge stores its persistent cache at `/data/bridge-cache.json`.

### Metadata

| Variable | Default | Purpose |
| --- | --- | --- |
| `METADATA_ENABLED` | `true` | Enable now-playing metadata |
| `METADATA_PROVIDER` | `auto` | `ticker`, `xmplaylist`, `stellar`, or automatic selection |
| `METADATA_FALLBACK` | `xmplaylist` | Fallback when Ticker fails |
| `ICY_METADATA_ENABLED` | `true` | Inject ICY metadata for clients that request it |
| `ICY_METAINT` | `16384` | Audio bytes between ICY metadata blocks |
| `TICKER_ACTIVE_POLL_SECONDS` | `15` | Poll rate while bridge streams are active |
| `TICKER_IDLE_POLL_SECONDS` | `0` | Ticker polling while no bridge stream is active |
| `XMPLAYLIST_POLL_SECONDS` | `120` | xmplaylist refresh interval |

### Music Assistant

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUSIC_ASSISTANT_URL` | `http://music-assistant:8095` | MA server |
| `MUSIC_ASSISTANT_TOKEN` | empty | MA API token |
| `IMPORT_FORMAT` | default format | Format used for managed radios |
| `IMPORT_LOGOS` | `true` | Add source artwork/logo |
| `MA_AUTO_SYNC` | `false` | Enable automatic synchronization |
| `MA_SYNC_ON_START` | `true` | Sync after bridge startup |
| `MA_SYNC_AFTER_CATALOG_REFRESH` | `true` | Sync after catalog changes |
| `MA_REMOVE_MISSING` | `false` | Remove bridge-managed stations no longer in source |
| `MA_SYNC_RETRY_SECONDS` | `3600` | Retry interval after failed sync |
| `MA_SYNC_TIMEOUT` | `90` | MA API timeout |
| `MA_ADD_RETRIES` | `3` | Retry station creation |
| `MA_ADD_RETRY_DELAY` | `3` | Delay between station-add retries |
| `MA_SYNC_CONCURRENCY` | `1` | Parallel MA station work |
| `MA_IMPORT_RELEASE_GRACE_SECONDS` | `11` | Grace period after warm source release |

The bridge identifies its own MA radios by the managed bridge URL. Format changes such as AAC to MP3 migrate the same channel rather than leaving an old managed station behind.

## Music Assistant workflow

Automatic synchronization can:

- add missing bridge stations
- skip unchanged stations
- migrate a managed station when its URL format changes
- remove stale formats
- optionally remove source channels that no longer exist
- warm a stream before MA probes it
- yield upstream capacity to real playback

Useful endpoints:

```text
/ma/status
/ma/sync
/ma/audit
```

Use the audit/cleanup endpoints before bulk removals. The bridge only manages radios whose URL matches its configured managed URL prefix.

## Streaming endpoints

```text
/stream/<channel-id>.mp3
/stream/<channel-id>.aac
/playlist.m3u8
/playlist.m3u
/channels
/metadata/<channel-id>
/probe/<channel-id>
```

MP3 is recommended for Echo playback.

## Diagnostics

### Health

```text
GET /health
```

The v6 playback section reports the active architecture and supported tuning only. Removed v5 experiment fields are no longer exposed.

The top-level health response also publishes the running bridge `version`, for example `"version": "6.0.0"`, so deployments can confirm exactly which release is active.

### Active streams

```text
GET /streams
```

Important per-hub fields include:

```text
state
listeners
current_attempt_number
seconds_since_last_real_chunk
current_real_chunk_gap_seconds
max_real_chunk_gap_seconds
effective_max_real_chunk_gap_seconds
avg_real_chunk_gap_seconds
real_chunk_gap_over_1s_count
real_chunk_gap_over_3s_count
real_chunk_gap_over_5s_count
real_chunk_gap_over_10s_count
recent_real_kbps
avg_chunk_bytes
coalesce_bytes
coalesce_max_seconds
ffmpeg_readrate
ffmpeg_timestamp_warning_count
ffmpeg_timestamp_discontinuity_count
current_attempt_timestamp_warning_count
current_attempt_timestamp_discontinuity_count
ring_buffer_seconds
dropped_chunks
slow_subscriber_disconnects
restart_count
last_error
```

For the default 192 kbps MP3 path, a healthy sustained stream should normally show `recent_real_kbps` close to 192 rather than the 300-400+ kbps burst pattern that the pre-v6 path exposed.

### Dependency checks

```text
GET  /api/checks
POST /api/checks/run
GET  /api/checks/stream/<channel-id>
```

Checks cover the configured source, Music Assistant, metadata providers, FFmpeg/ffprobe, and bridge internals.

## How v6 streaming works

1. A client requests a bridge station.
2. The shared hub is created or reused.
3. FFmpeg waits for an upstream slot.
4. FFmpeg opens the live source with reconnect controls and `-readrate 1`.
5. If the source is cold, the bridge provides startup silence until the first real FFmpeg audio arrives.
6. Startup silence ends permanently.
7. Real encoded audio passes through the 8 KB coalescer.
8. A partial coalescer buffer is flushed after 500 ms even if no new FFmpeg bytes arrive.
9. The hub fans the same encoded stream out to listeners.
10. When the final listener leaves, the source remains hot for the linger interval before closing.

## Amazon Echo compatibility

The v6 defaults come from direct playback testing through Music Assistant and Echo.

The working path is intentionally simple:

- MP3 192 kbps
- FFmpeg `-readrate 1`
- no Python output pacing
- no adaptive jitter buffer
- no post-start encoded-silence insertion
- 8 KB coalesced network writes
- real 500 ms partial-write flush

If browser playback works but Echo does not, first verify that `/streams` shows `ffmpeg_readrate: 1.0`, no subscriber drops, and no repeated FFmpeg restarts.

## Troubleshooting

### Silence spots

Inspect `/streams`.

If `current_real_chunk_gap_seconds` or the 3/5/10-second counters climb while `recent_real_kbps` remains near 192, the source or FFmpeg is pausing.

If the bitrate swings far above 192 followed by long gaps, verify you are actually running v6 and that the stream status reports:

```text
ffmpeg_readrate: 1.0
```

### Crackle or static on Echo

Do not reintroduce the old Python pacing or jitter-buffer environment variables. They are removed from v6.

Confirm:

```text
format: mp3
recent_real_kbps: approximately 192
dropped_chunks: 0
restart_count: stable
```

### Music Assistant cannot add a station

Check:

```text
/ma/status
/api/checks
/api/checks/stream/<channel-id>
```

Also confirm the public bridge URL is reachable from Music Assistant.

### Ticker has no match

The bridge automatically falls back to xmplaylist when configured with:

```env
METADATA_PROVIDER=ticker
METADATA_FALLBACK=xmplaylist
```

A Ticker matching failure should not interrupt audio.

## Upgrade from v5

1. Pull the v6 image.
2. Remove retired v5 variables from your local `.env`.
3. Keep your XC/Dispatcharr, Music Assistant, metadata, and current stream settings.
4. Recreate the container.
5. Confirm `/health` reports `architecture: v6_ffmpeg_readrate`.
6. Confirm `/streams` reports `ffmpeg_readrate: 1.0`.

Example:

```bash
docker compose pull
docker compose up -d --force-recreate
```

The existing `/data/bridge-cache.json` can remain. The old `/data/jitter-targets.json` file is no longer read and can be deleted.

## Persistent data

The Docker volume stores bridge state under `/data`.

Active v6 persistence:

```text
/data/bridge-cache.json
```

Live hub/listener state is not persisted.

## Security

Do not commit provider passwords or Music Assistant tokens. The bridge attempts to redact configured XC passwords, MA tokens, and metadata API keys from logs and diagnostic errors.

## Development

Run tests:

```bash
pytest -q
```

Build locally:

```bash
docker compose build
```

For playback changes, validate `/streams` and `/api/checks/stream/<channel-id>` before changing timing behavior.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.


### v6.0.1 DJ/talk transition diagnostics

v6.0.1 keeps the validated v6 stream architecture and adds only an FFmpeg-side audio timing correction stage for transcoded audio:

```text
-af aresample=async=1:first_pts=0
```

AAC stream-copy mode is unchanged. `/streams` now exposes FFmpeg timestamp warning and discontinuity counters so audible stutters can be correlated with source-timeline problems rather than inferred from bitrate alone.


### v6.0.2 rollback to validated playback path

v6.0.2 removes the v6.0.1 `aresample=async=1:first_pts=0` filter after renewed stuttering was observed. The active playback path is again the architecture validated in v5.6.13:

```text
Dispatcharr / XC MPEG-TS
        ↓
Persistent FFmpeg
reconnect + -readrate 1
        ↓
MP3 192k
        ↓
8 KB coalescer / 500 ms flush
        ↓
Shared hub
        ↓
Music Assistant / Echo
```

The FFmpeg timing-warning counters remain available in `/streams` for diagnostics, but they do not alter playback.

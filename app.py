import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote, urljoin

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").strip().strip('"').strip("'").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dispatcharr-ma-bridge")

APP_NAME = "Dispatcharr / XC Music Assistant Bridge"
SOURCE_MODE = os.getenv("SOURCE_MODE", "dispatcharr").strip().lower()

DISPATCHARR_BASE_URL = os.getenv("DISPATCHARR_BASE_URL", "http://dispatcharr:9191").rstrip("/")
DISPATCHARR_M3U_PATH = os.getenv("DISPATCHARR_M3U_PATH", "/output/m3u")
DISPATCHARR_M3U_URL = os.getenv("DISPATCHARR_M3U_URL", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8088").rstrip("/")
GROUP_FILTER = os.getenv("GROUP_FILTER", "").strip()
NAME_FILTER = os.getenv("NAME_FILTER", "").strip()
DEFAULT_FORMAT = os.getenv("DEFAULT_FORMAT", "mp3").lower()
MP3_BITRATE = os.getenv("MP3_BITRATE", "192k")
AAC_BITRATE = os.getenv("AAC_BITRATE", "128k")
SAMPLE_RATE = os.getenv("SAMPLE_RATE", "48000")
CHANNELS = os.getenv("CHANNELS", "2")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
FFMPEG_LOG_LEVEL = os.getenv("FFMPEG_LOG_LEVEL", "warning")
DISPATCHARR_USER_AGENT = os.getenv("DISPATCHARR_USER_AGENT", "Dispatcharr-MA-Bridge/5.5")
AAC_MODE = os.getenv("AAC_MODE", "auto").strip().lower()
AAC_PROBE_TIMEOUT = max(2.0, float(os.getenv("AAC_PROBE_TIMEOUT", "30")))
AUDIO_CODEC_CACHE_SECONDS = max(60, int(os.getenv("AUDIO_CODEC_CACHE_SECONDS", "86400")))

# Xtream Codes / XC source settings.  In XC mode the bridge authenticates to
# player_api.php, reads live categories and live streams, then constructs the
# standard /live/<user>/<pass>/<stream_id>.<format> playback URL.
XC_BASE_URL = os.getenv("XC_BASE_URL", "").strip().rstrip("/")
XC_USERNAME = os.getenv("XC_USERNAME", "").strip()
XC_PASSWORD = os.getenv("XC_PASSWORD", "").strip()
XC_OUTPUT = os.getenv("XC_OUTPUT", "ts").strip().lower()
XC_CATEGORY_IDS = {x.strip() for x in os.getenv("XC_CATEGORY_IDS", "").split(",") if x.strip()}
XC_VERIFY_SSL = os.getenv("XC_VERIFY_SSL", "true").lower() in {"1", "true", "yes", "on"}

CATALOG_REFRESH_SECONDS = max(60, int(os.getenv("CATALOG_REFRESH_SECONDS", "86400")))
CATALOG_RETRY_SECONDS = max(60, int(os.getenv("CATALOG_RETRY_SECONDS", "3600")))

METADATA_PROVIDER = os.getenv("METADATA_PROVIDER", "auto").strip().lower()
METADATA_ENABLED = os.getenv("METADATA_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
ICY_METADATA_ENABLED = os.getenv("ICY_METADATA_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
ICY_METAINT = max(1024, int(os.getenv("ICY_METAINT", "16384")))
API_CHECK_TIMEOUT = max(2.0, float(os.getenv("API_CHECK_TIMEOUT", "10")))
XMPLAYLIST_BASE_URL = os.getenv("XMPLAYLIST_BASE_URL", "https://xmplaylist.com").rstrip("/")
XMPLAYLIST_POLL_SECONDS = max(120, int(os.getenv("XMPLAYLIST_POLL_SECONDS", "120")))
STELLAR_BASE_URL = os.getenv("STELLAR_BASE_URL", "https://api.stellartunerlog.com/v1").rstrip("/")
STELLAR_API_KEY = os.getenv("STELLAR_API_KEY", "").strip()
STELLAR_POLL_SECONDS = max(15, int(os.getenv("STELLAR_POLL_SECONDS", "30")))
METADATA_STALE_SECONDS = max(60, int(os.getenv("METADATA_STALE_SECONDS", "600")))
# Ticker integration (v5.2). This intentionally uses the same free bulk feeds
# as the current Dispatcharr Ticker plugin, without enabling Ticker overlays.
TICKER_NOWPLAYING_URL = os.getenv("TICKER_NOWPLAYING_URL", "https://stellartunerlog.com/nowplaying.json").strip()
TICKER_CHANNEL_URL = os.getenv("TICKER_CHANNEL_URL", "https://stellartunerlog.com/channels.json").strip()
TICKER_ACTIVE_POLL_SECONDS = max(5.0, float(os.getenv("TICKER_ACTIVE_POLL_SECONDS", os.getenv("TICKER_POLL_SECONDS", "15"))))
TICKER_IDLE_POLL_SECONDS = max(0.0, float(os.getenv("TICKER_IDLE_POLL_SECONDS", "0")))
TICKER_CHANNEL_REFRESH_SECONDS = max(3600, int(os.getenv("TICKER_CHANNEL_REFRESH_SECONDS", "86400")))
METADATA_FALLBACK = os.getenv("METADATA_FALLBACK", "xmplaylist").strip().lower()

# Music Assistant radio synchronization. builtin/add_radio is idempotent by URL,
# so a sync safely updates names/images while adding any new bridge stations.
MUSIC_ASSISTANT_URL = os.getenv("MUSIC_ASSISTANT_URL", "http://music-assistant:8095").strip().rstrip("/")
MUSIC_ASSISTANT_TOKEN = os.getenv("MUSIC_ASSISTANT_TOKEN", "").strip().strip('"').strip("'")
IMPORT_FORMAT = os.getenv("IMPORT_FORMAT", DEFAULT_FORMAT).strip().lower()
IMPORT_LOGOS = os.getenv("IMPORT_LOGOS", "true").strip().lower() in {"1", "true", "yes", "on"}
MA_AUTO_SYNC = os.getenv("MA_AUTO_SYNC", "false").strip().lower() in {"1", "true", "yes", "on"}
MA_SYNC_ON_START = os.getenv("MA_SYNC_ON_START", "true").strip().lower() in {"1", "true", "yes", "on"}
MA_SYNC_AFTER_CATALOG_REFRESH = os.getenv("MA_SYNC_AFTER_CATALOG_REFRESH", "true").strip().lower() in {"1", "true", "yes", "on"}
MA_REMOVE_MISSING = os.getenv("MA_REMOVE_MISSING", "false").strip().lower() in {"1", "true", "yes", "on"}
MA_SYNC_RETRY_SECONDS = max(60, int(os.getenv("MA_SYNC_RETRY_SECONDS", "3600")))
MA_SYNC_TIMEOUT = max(5.0, float(os.getenv("MA_SYNC_TIMEOUT", "90")))
MA_ADD_RETRIES = max(1, int(os.getenv("MA_ADD_RETRIES", "3")))
MA_ADD_RETRY_DELAY = max(0.0, float(os.getenv("MA_ADD_RETRY_DELAY", "3")))
MA_SYNC_CONCURRENCY = max(1, int(os.getenv("MA_SYNC_CONCURRENCY", "1")))
STREAM_WARMUP_ENABLED = os.getenv("STREAM_WARMUP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
STREAM_WARMUP_TIMEOUT = max(5.0, float(os.getenv("STREAM_WARMUP_TIMEOUT", "45")))
STREAM_WARMUP_RETRIES = max(1, int(os.getenv("STREAM_WARMUP_RETRIES", "2")))
STREAM_WARMUP_RETRY_DELAY = max(0.0, float(os.getenv("STREAM_WARMUP_RETRY_DELAY", "2")))
SOURCE_RW_TIMEOUT_SECONDS = max(15.0, float(os.getenv("SOURCE_RW_TIMEOUT_SECONDS", "60")))

# Playback startup protection. Dispatcharr can take time to establish/cache a cold
# upstream stream. While that happens, serve correctly paced encoded silence so
# Music Assistant receives audio immediately and does not hit its first-chunk
# timeout. Once real audio arrives, switch on the same HTTP connection.
STARTUP_SILENCE_ENABLED = os.getenv("STARTUP_SILENCE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
STARTUP_SILENCE_MAX_SECONDS = max(5.0, float(os.getenv("STARTUP_SILENCE_MAX_SECONDS", "60")))
STARTUP_REAL_AUDIO_RETRIES = max(1, int(os.getenv("STARTUP_REAL_AUDIO_RETRIES", "3")))
STARTUP_REAL_AUDIO_RETRY_DELAY = max(0.0, float(os.getenv("STARTUP_REAL_AUDIO_RETRY_DELAY", "2")))

# Shared per-channel stream sessions. Music Assistant may briefly disconnect/reopen
# a Radio URL while probing or handing playback to a player. Keep one upstream
# Dispatcharr/XC session per channel+format and let clients subscribe to that
# shared encoded stream. When the last listener leaves, retain the upstream for a
# short linger window so a reconnect does not tear down/recreate Dispatcharr.
STREAM_LINGER_SECONDS = max(0.0, float(os.getenv("STREAM_LINGER_SECONDS", "30")))
STREAM_SUBSCRIBER_QUEUE_CHUNKS = max(4, int(os.getenv("STREAM_SUBSCRIBER_QUEUE_CHUNKS", "16")))
STREAM_RESTART_DELAY = max(0.25, float(os.getenv("STREAM_RESTART_DELAY", "1")))
STREAM_READ_CHUNK_BYTES = max(1024, int(os.getenv("STREAM_READ_CHUNK_BYTES", "16384")))
# FFmpeg often returns tiny encoded reads even when read() requests 16 KB. Coalesce
# those small reads before fan-out so clients receive steadier internet-radio-sized
# writes. This does not alter codec frames or audio timing; it only changes network
# write granularity. A short max delay prevents extra latency on low-bitrate streams.
STREAM_COALESCE_BYTES = max(0, int(os.getenv("STREAM_COALESCE_BYTES", "8192")))
STREAM_COALESCE_MAX_SECONDS = max(0.0, float(os.getenv("STREAM_COALESCE_MAX_SECONDS", "0.50")))
STREAM_RING_BUFFER_SECONDS = max(0.0, float(os.getenv("STREAM_RING_BUFFER_SECONDS", "2")))
UPSTREAM_RETRY_INITIAL_SECONDS = max(0.25, float(os.getenv("UPSTREAM_RETRY_INITIAL_SECONDS", "1")))
UPSTREAM_RETRY_MAX_SECONDS = max(UPSTREAM_RETRY_INITIAL_SECONDS, float(os.getenv("UPSTREAM_RETRY_MAX_SECONDS", "5")))
UPSTREAM_RETRY_FOREVER_WHILE_LISTENING = os.getenv("UPSTREAM_RETRY_FOREVER_WHILE_LISTENING", "true").strip().lower() in {"1", "true", "yes", "on"}
REAL_AUDIO_STALL_SECONDS = max(1.0, float(os.getenv("REAL_AUDIO_STALL_SECONDS", "2.5")))
# v5 baseline: FFmpeg owns reconnect; bridge respawns only after FFmpeg exits.
UPSTREAM_MAX_CONNECTIONS = max(1, int(os.getenv("UPSTREAM_MAX_CONNECTIONS", "2")))
MA_IMPORT_RELEASE_GRACE_SECONDS = max(0.0, float(os.getenv("MA_IMPORT_RELEASE_GRACE_SECONDS", "11")))
BRIDGE_CACHE_FILE = os.getenv("BRIDGE_CACHE_FILE", "/data/bridge-cache.json").strip()


MA_MANAGED_URL_PREFIX = os.getenv("MA_MANAGED_URL_PREFIX", f"{PUBLIC_BASE_URL}/stream/").strip()

CHANNEL_ID_RE = re.compile(
    r"/proxy/(?:ts|fmp4)/stream/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def redact_secrets(value: Any) -> str:
    """Remove XC credentials and other configured secrets from log/error text."""
    text = str(value)
    secrets = [XC_PASSWORD, MUSIC_ASSISTANT_TOKEN, STELLAR_API_KEY]
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
            try:
                text = text.replace(quote(secret, safe=""), "***")
            except Exception:
                pass
    if XC_USERNAME:
        text = text.replace(f"/live/{XC_USERNAME}/", "/live/***/")
        try:
            text = text.replace(f"/live/{quote(XC_USERNAME, safe='')}/", "/live/***/")
        except Exception:
            pass
    return text


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str) -> str:
    """Normalize source/provider names for metadata matching only.

    Preserve the original Channel.name for display.  XC providers commonly add
    prefixes such as ``Radio:`` while metadata services publish the underlying
    SiriusXM station name.  Strip only delimiter-style provider prefixes so a
    real station such as ``Radio Margaritaville`` is not damaged.
    """
    value = value.casefold().replace("&", "and")
    # Examples: "Radio: 80s on 8", "US | SiriusXM Hits 1".
    # Do NOT strip a plain leading word "radio" without punctuation.
    value = re.sub(r"^\s*(?:radio|us|usa)\s*[:|>-]+\s*", "", value)
    value = re.sub(r"\bsirius\s*xm\b|\bsiriusxm\b|\bsxm\b", "", value)
    # Strip an explicit channel-number prefix only.  Do not treat decade-based
    # station names such as "70s on 7", "80s on 8", or "90s on 9" as a
    # channel-number prefix.
    value = re.sub(r"^\s*(?:ch(?:annel)?\s*|#\s*)\d{1,3}\s*[-:|.]?\s*", "", value)
    value = re.sub(r"^\s*\d{1,3}\s*[-:|]\s+", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def clean_channel_number(value: Any) -> str:
    if value is None:
        return ""
    match = re.search(r"\d+", str(value))
    return match.group(0) if match else ""


def extract_named_channel_number(value: str) -> str:
    """Extract a Sirius channel number only when it is part of the display name.

    XC's ``num`` field is usually provider ordering, not the SiriusXM channel
    number, so it is deliberately not used for metadata matching.
    """
    text = value.casefold()
    text = re.sub(r"\bsirius\s*xm\b|\bsiriusxm\b|\bsxm\b", "", text).strip()
    match = re.match(r"(?:ch(?:annel)?\s*)?#?\s*(\d{1,3})\b", text)
    return match.group(1) if match else ""


def extract_dispatcharr_id(*values: str) -> str:
    """Extract Dispatcharr's numeric Channel.id from known API/logo URLs."""
    for value in values:
        if not value:
            continue
        match = re.search(r"/api/channels/(?:logos/)?(\d+)(?:/|$)", str(value))
        if match:
            return match.group(1)
        match = re.search(r"/api/channels/logos/(\d+)/", str(value))
        if match:
            return match.group(1)
    return ""


@dataclass
class Channel:
    channel_id: str
    name: str
    group: str = ""
    logo: str = ""
    tvg_id: str = ""
    channel_number: str = ""
    source_url: str = ""
    dispatcharr_id: str = ""

    def bridge_url(self, fmt: str = DEFAULT_FORMAT) -> str:
        return f"{PUBLIC_BASE_URL}/stream/{self.channel_id}.{fmt}"


@dataclass
class NowPlaying:
    provider: str
    channel_name: str = ""
    channel_number: str = ""
    artist: str = ""
    title: str = ""
    album: str = ""
    artwork_url: str = ""
    timestamp: str = ""
    fetched_at: float = 0.0

    @property
    def stream_title(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.title or self.artist

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("fetched_at", None)
        data["stream_title"] = self.stream_title
        return data


def _matches_filter(channel: Channel) -> bool:
    if GROUP_FILTER and not re.search(GROUP_FILTER, channel.group, flags=re.IGNORECASE):
        return False
    if NAME_FILTER and not re.search(NAME_FILTER, channel.name, flags=re.IGNORECASE):
        return False
    return True


def parse_dispatcharr_m3u(m3u_text: str) -> list[Channel]:
    """Read names and attributes from Dispatcharr's exported M3U/M3U8.

    The display name after the comma in #EXTINF is intentionally preferred over
    tvg-name so the bridge always reflects the channel name exported by Dispatcharr.
    """
    channels: list[Channel] = []
    pending_extinf: str | None = None

    for raw_line in m3u_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending_extinf = line
            continue
        if line.startswith("#"):
            continue
        if pending_extinf is None:
            continue

        match = CHANNEL_ID_RE.search(line)
        if not match:
            pending_extinf = None
            continue

        attrs = dict(ATTR_RE.findall(pending_extinf))
        display_name = pending_extinf.rsplit(",", 1)[-1].strip()
        name = display_name or attrs.get("tvg-name", "") or match.group(1)
        channel = Channel(
            channel_id=match.group(1),
            name=name,
            group=attrs.get("group-title", ""),
            logo=attrs.get("tvg-logo", ""),
            tvg_id=attrs.get("tvg-id", ""),
            channel_number=clean_channel_number(attrs.get("tvg-chno", "")),
            source_url=line,
            dispatcharr_id=extract_dispatcharr_id(attrs.get("tvg-logo", ""), line),
        )
        if _matches_filter(channel):
            channels.append(channel)
        pending_extinf = None

    return channels


async def fetch_dispatcharr_m3u() -> str:
    url = DISPATCHARR_M3U_URL or urljoin(f"{DISPATCHARR_BASE_URL}/", DISPATCHARR_M3U_PATH.lstrip("/"))
    headers = {"User-Agent": DISPATCHARR_USER_AGENT}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT, read=HTTP_TIMEOUT),
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Dispatcharr M3U: {exc}") from exc


def build_xc_channels(categories: Any, streams: Any) -> list[Channel]:
    """Convert XC Player API responses into the bridge's channel model."""
    if not isinstance(categories, list):
        categories = []
    if not isinstance(streams, list):
        streams = []

    category_names = {
        str(row.get("category_id", "")): str(row.get("category_name", ""))
        for row in categories if isinstance(row, dict)
    }
    channels: list[Channel] = []
    for row in streams:
        if not isinstance(row, dict):
            continue
        stream_id = str(row.get("stream_id", "")).strip()
        if not stream_id or not stream_id.isdigit():
            continue
        category_id = str(row.get("category_id", "")).strip()
        if XC_CATEGORY_IDS and category_id not in XC_CATEGORY_IDS:
            continue
        name = str(row.get("name") or f"XC {stream_id}").strip()
        group = category_names.get(category_id, category_id)
        ext = "m3u8" if XC_OUTPUT in {"m3u8", "hls"} else "ts"
        source_url = (
            f"{XC_BASE_URL}/live/{quote(XC_USERNAME, safe='')}/"
            f"{quote(XC_PASSWORD, safe='')}/{stream_id}.{ext}"
        )
        channel = Channel(
            channel_id=f"xc-{stream_id}",
            name=name,
            group=group,
            logo=str(row.get("stream_icon") or ""),
            tvg_id=str(row.get("epg_channel_id") or ""),
            channel_number=extract_named_channel_number(name),
            source_url=source_url,
            dispatcharr_id=extract_dispatcharr_id(str(row.get("stream_icon") or "")),
        )
        if _matches_filter(channel):
            channels.append(channel)
    return channels


async def fetch_xc_channels() -> list[Channel]:
    if not XC_BASE_URL or not XC_USERNAME or not XC_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="XC mode requires XC_BASE_URL, XC_USERNAME and XC_PASSWORD",
        )
    api_url = f"{XC_BASE_URL}/player_api.php"
    base_params = {"username": XC_USERNAME, "password": XC_PASSWORD}
    headers = {"User-Agent": DISPATCHARR_USER_AGENT}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT, read=HTTP_TIMEOUT),
            follow_redirects=True,
            headers=headers,
            verify=XC_VERIFY_SSL,
        ) as client:
            auth_resp = await client.get(api_url, params=base_params)
            auth_resp.raise_for_status()
            auth = auth_resp.json()
            user_info = auth.get("user_info", {}) if isinstance(auth, dict) else {}
            if str(user_info.get("auth", "0")) != "1":
                raise HTTPException(status_code=502, detail="XC authentication failed")

            allowed = {str(x).lower() for x in user_info.get("allowed_output_formats", [])}
            requested = "m3u8" if XC_OUTPUT in {"m3u8", "hls"} else "ts"
            if allowed and requested not in allowed:
                LOGGER.warning("XC account does not advertise %s output; allowed=%s", requested, sorted(allowed))

            categories_resp, streams_resp = await asyncio.gather(
                client.get(api_url, params={**base_params, "action": "get_live_categories"}),
                client.get(api_url, params={**base_params, "action": "get_live_streams"}),
            )
            categories_resp.raise_for_status()
            streams_resp.raise_for_status()
            return build_xc_channels(categories_resp.json(), streams_resp.json())
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not load XC live channels: {exc}") from exc


class ChannelCatalog:
    def __init__(self) -> None:
        self.channels: list[Channel] = []
        self.by_id: dict[str, Channel] = {}
        self.last_refresh = 0.0
        self.last_refresh_at = ""
        self.last_error = ""
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self._load_persistent_cache()

    def _load_persistent_cache(self) -> None:
        if not BRIDGE_CACHE_FILE:
            return
        try:
            path = Path(BRIDGE_CACHE_FILE)
            if not path.exists():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("channels", []) if isinstance(payload, dict) else []
            channels = [Channel(**row) for row in rows if isinstance(row, dict)]
            if channels:
                self.channels = channels
                self.by_id = {ch.channel_id: ch for ch in channels}
                self.last_refresh_at = str(payload.get("saved_at") or "")
                LOGGER.info("Loaded persistent bridge cache from %s (%d channels)", path, len(channels))
        except Exception as exc:
            LOGGER.warning("Could not load persistent bridge cache: %s", redact_secrets(exc))

    def _save_persistent_cache(self) -> None:
        if not BRIDGE_CACHE_FILE or not self.channels:
            return
        try:
            path = Path(BRIDGE_CACHE_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps({"saved_at": utc_now_iso(), "channels": [asdict(ch) for ch in self.channels]}, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            LOGGER.warning("Could not save persistent bridge cache: %s", redact_secrets(exc))

    async def refresh(self, force: bool = False) -> list[Channel]:
        if not force and self.channels and time.monotonic() - self.last_refresh < CATALOG_REFRESH_SECONDS:
            return self.channels
        async with self.lock:
            if not force and self.channels and time.monotonic() - self.last_refresh < CATALOG_REFRESH_SECONDS:
                return self.channels
            try:
                if SOURCE_MODE == "xc":
                    channels = await fetch_xc_channels()
                    source_label = "XC Player API"
                elif SOURCE_MODE == "dispatcharr":
                    text = await fetch_dispatcharr_m3u()
                    channels = parse_dispatcharr_m3u(text)
                    source_label = "Dispatcharr M3U"
                else:
                    raise HTTPException(status_code=500, detail="SOURCE_MODE must be 'dispatcharr' or 'xc'")
                self.channels = channels
                self.by_id = {ch.channel_id: ch for ch in channels}
                self.last_refresh = time.monotonic()
                self.last_refresh_at = utc_now_iso()
                self.last_error = ""
                self._save_persistent_cache()
                LOGGER.info("Loaded %d filtered channel(s) from %s", len(channels), source_label)
                return channels
            except Exception as exc:
                self.last_error = str(exc)
                # Keep the last known-good catalog so existing streams continue to work.
                if self.channels:
                    LOGGER.warning("Catalog refresh failed; keeping %d cached channel(s): %s", len(self.channels), exc)
                    return self.channels
                raise

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="catalog-refresh")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _run(self) -> None:
        # Initial load is performed by lifespan(). Refresh once per configured
        # interval after that. On failure, retain the last good catalog and retry
        # sooner rather than waiting another full day.
        sleep_for = CATALOG_REFRESH_SECONDS
        while True:
            try:
                await asyncio.sleep(sleep_for)
                refreshed = await self.refresh(force=True)
                if MA_AUTO_SYNC and MA_SYNC_AFTER_CATALOG_REFRESH and not self.last_error:
                    ma_sync_service.request_sync(refreshed, reason="scheduled-catalog-refresh")
                sleep_for = CATALOG_REFRESH_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                LOGGER.warning("Scheduled catalog refresh failed: %s", exc)
                sleep_for = CATALOG_RETRY_SECONDS

    async def get(self, channel_id: str) -> Channel | None:
        await self.refresh()
        return self.by_id.get(channel_id)

    def status(self) -> dict[str, Any]:
        return {
            "mode": SOURCE_MODE,
            "channels_cached": len(self.channels),
            "last_refresh": self.last_refresh_at,
            "last_error": self.last_error,
            "refresh_seconds": CATALOG_REFRESH_SECONDS,
            "retry_seconds": CATALOG_RETRY_SECONDS,
        }


def _existing_radio_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or "").strip()


async def coalesce_audio_chunks(
    source: AsyncIterator[tuple[bytes, bool]],
    target_bytes: int | None = None,
    max_delay_seconds: float | None = None,
) -> AsyncIterator[tuple[bytes, bool]]:
    """Coalesce tiny encoded FFmpeg reads into steadier client writes.

    FFmpeg's asyncio pipe may return a few hundred bytes even when read() asks for
    much more. Broadcasting every tiny read produces bursty HTTP delivery that
    browsers tolerate well but some hardware radio clients do not. Keep encoded
    bytes untouched and only group adjacent chunks with the same real/silence
    classification. State transitions and stream end always flush immediately.
    """
    target = STREAM_COALESCE_BYTES if target_bytes is None else max(0, int(target_bytes))
    max_delay = STREAM_COALESCE_MAX_SECONDS if max_delay_seconds is None else max(0.0, float(max_delay_seconds))
    if target <= 0:
        async for chunk, real_audio in source:
            if chunk:
                yield chunk, real_audio
        return

    pending = bytearray()
    pending_real: bool | None = None
    pending_since = 0.0

    async for chunk, real_audio in source:
        if not chunk:
            continue
        now = time.monotonic()

        # Never combine startup silence and real audio in one network write.
        if pending and pending_real is not None and real_audio != pending_real:
            yield bytes(pending), pending_real
            pending.clear()
            pending_real = None
            pending_since = 0.0

        if not pending:
            pending_real = real_audio
            pending_since = now
        pending.extend(chunk)

        while len(pending) >= target:
            yield bytes(pending[:target]), bool(pending_real)
            del pending[:target]
            pending_since = now if pending else 0.0

        if pending and max_delay > 0 and (now - pending_since) >= max_delay:
            yield bytes(pending), bool(pending_real)
            pending.clear()
            pending_real = None
            pending_since = 0.0

    if pending:
        yield bytes(pending), bool(pending_real)


class SharedStreamHub:
    """One persistent upstream encoder shared by every listener of a channel/format."""

    def __init__(self, channel: Channel, fmt: str) -> None:
        self.channel = channel
        self.fmt = fmt
        self.lock = asyncio.Lock()
        self.subscribers: dict[str, asyncio.Queue[bytes | None]] = {}
        self.pins = 0
        self.task: asyncio.Task | None = None
        self.linger_task: asyncio.Task | None = None
        self.real_audio_ready = asyncio.Event()
        self.started_at = ""
        self.last_real_audio_at = ""
        self.last_error = ""
        self.restart_count = 0
        self.total_subscribers = 0
        # Live stream-health counters used to diagnose stutter without guessing.
        self.total_output_bytes = 0
        self.real_audio_bytes = 0
        self.total_chunks = 0
        self.real_audio_chunks = 0
        self.last_chunk_at = ""
        self.last_real_chunk_at = ""
        self._last_chunk_monotonic = 0.0
        self._last_real_chunk_monotonic = 0.0
        self._rate_window: list[tuple[float, int]] = []
        self.dropped_chunks = 0
        self.slow_subscriber_disconnects = 0
        self.max_queue_depth = 0
        self.upstream_http_503_count = 0
        self.upstream_http_error_count = 0
        self.recovery_count = 0
        self.silence_fallback_count = 0
        self._ring_buffer: deque[tuple[float, bytes]] = deque()

    @property
    def key(self) -> str:
        return f"{self.channel.channel_id}:{self.fmt}"

    def record_upstream_event(self, event: str) -> None:
        if event == "http_503":
            self.upstream_http_503_count += 1
            self.upstream_http_error_count += 1
        elif event == "http_error":
            self.upstream_http_error_count += 1
        elif event == "recovered":
            self.recovery_count += 1
        elif event == "silence_fallback":
            self.silence_fallback_count += 1

    def _cancel_linger_locked(self) -> None:
        if self.linger_task and not self.linger_task.done():
            self.linger_task.cancel()
        self.linger_task = None

    def _ensure_running_locked(self) -> None:
        self._cancel_linger_locked()
        if self.task is None or self.task.done():
            self.last_error = ""
            self.started_at = utc_now_iso()
            self.task = asyncio.create_task(self._run(), name=f"stream-hub:{self.key}")

    def _schedule_linger_locked(self) -> None:
        if self.subscribers or self.pins:
            return
        if self.task is None or self.task.done():
            return
        if self.linger_task and not self.linger_task.done():
            return
        self.linger_task = asyncio.create_task(self._linger_then_stop(), name=f"stream-linger:{self.key}")

    async def _linger_then_stop(self) -> None:
        try:
            if STREAM_LINGER_SECONDS:
                await asyncio.sleep(STREAM_LINGER_SECONDS)
            async with self.lock:
                if self.subscribers or self.pins:
                    return
                task = self.task
            if task and not task.done():
                LOGGER.info(
                    "Linger expired for %s (%s); closing shared upstream after %.0fs idle",
                    self.channel.name,
                    self.fmt,
                    STREAM_LINGER_SECONDS,
                )
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            async with self.lock:
                if self.linger_task is asyncio.current_task():
                    self.linger_task = None

    def _disconnect_slow_subscriber_locked(self, subscriber_id: str, queue: asyncio.Queue[bytes | None]) -> None:
        # Never let a slow client back-pressure the shared Dispatcharr session.
        # End only that client; Music Assistant may reconnect to the same hot hub.
        self.subscribers.pop(subscriber_id, None)
        queued = queue.qsize()
        # The chunk that could not be queued plus anything already queued for the
        # disconnected subscriber are counted as dropped for diagnostics.
        self.dropped_chunks += queued + 1
        self.slow_subscriber_disconnects += 1
        try:
            while True:
                queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        LOGGER.warning("Disconnected slow stream subscriber %s from %s", subscriber_id[:8], self.key)

    def _record_chunk(self, chunk: bytes, real_audio: bool) -> None:
        now_mono = time.monotonic()
        now_iso = utc_now_iso()
        size = len(chunk)
        self.total_output_bytes += size
        self.total_chunks += 1
        self.last_chunk_at = now_iso
        self._last_chunk_monotonic = now_mono
        if real_audio:
            self.real_audio_bytes += size
            self.real_audio_chunks += 1
            self.last_real_chunk_at = now_iso
            self._last_real_chunk_monotonic = now_mono
            self._rate_window.append((now_mono, size))
            cutoff = now_mono - 10.0
            while self._rate_window and self._rate_window[0][0] < cutoff:
                self._rate_window.pop(0)
        if STREAM_RING_BUFFER_SECONDS > 0:
            self._ring_buffer.append((now_mono, chunk))
            ring_cutoff = now_mono - STREAM_RING_BUFFER_SECONDS
            while self._ring_buffer and self._ring_buffer[0][0] < ring_cutoff:
                self._ring_buffer.popleft()

    async def _broadcast(self, chunk: bytes, real_audio: bool = False) -> None:
        if not chunk:
            return
        self._record_chunk(chunk, real_audio=real_audio)
        async with self.lock:
            slow: list[tuple[str, asyncio.Queue[bytes | None]]] = []
            for subscriber_id, queue in list(self.subscribers.items()):
                try:
                    queue.put_nowait(chunk)
                    self.max_queue_depth = max(self.max_queue_depth, queue.qsize())
                except asyncio.QueueFull:
                    slow.append((subscriber_id, queue))
            for subscriber_id, queue in slow:
                self._disconnect_slow_subscriber_locked(subscriber_id, queue)
            self._schedule_linger_locked()

    async def _run(self) -> None:
        LOGGER.info("Starting shared upstream hub for %s (%s)", self.channel.name, self.fmt)
        try:
            while True:
                self.real_audio_ready.clear()
                self.last_real_audio_at = ""
                try:
                    async def tagged_stream() -> AsyncIterator[tuple[bytes, bool]]:
                        async for chunk in startup_protected_audio_stream(
                            self.channel,
                            self.fmt,
                            real_audio_event=self.real_audio_ready,
                            diagnostic_callback=self.record_upstream_event,
                        ):
                            real_audio = self.real_audio_ready.is_set()
                            if real_audio and not self.last_real_audio_at:
                                self.last_real_audio_at = utc_now_iso()
                            yield chunk, real_audio

                    async for chunk, real_audio in coalesce_audio_chunks(tagged_stream()):
                        await self._broadcast(chunk, real_audio=real_audio)
                    self.last_error = "upstream stream ended"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = redact_secrets(exc)
                    LOGGER.warning("Shared upstream error for %s (%s): %s", self.channel.name, self.fmt, redact_secrets(exc))

                async with self.lock:
                    demand = bool(self.subscribers or self.pins)
                if not demand:
                    LOGGER.info("v5 hub %s ended with no demand; not respawning", self.key)
                    return
                self.restart_count += 1
                LOGGER.warning(
                    "v5 FFmpeg for %s (%s) exited; respawning in %.2fs because demand remains (restart #%d)",
                    self.channel.name, self.fmt, STREAM_RESTART_DELAY, self.restart_count,
                )
                await asyncio.sleep(STREAM_RESTART_DELAY)
        except asyncio.CancelledError:
            raise
        finally:
            self.real_audio_ready.clear()
            async with self.lock:
                if self.task is asyncio.current_task():
                    self.task = None
                subscribers = list(self.subscribers.values())
                self.subscribers.clear()
            for queue in subscribers:
                try:
                    while True:
                        queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            LOGGER.info("Stopped shared upstream hub for %s (%s)", self.channel.name, self.fmt)

    async def subscribe(self) -> AsyncIterator[bytes]:
        subscriber_id = uuid.uuid4().hex
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=STREAM_SUBSCRIBER_QUEUE_CHUNKS)
        async with self.lock:
            # Pre-roll a short encoded ring buffer so reconnecting clients start
            # on recent complete data instead of an arbitrary live burst.
            if self._ring_buffer:
                ring_chunks = [chunk for _, chunk in self._ring_buffer][-STREAM_SUBSCRIBER_QUEUE_CHUNKS:]
                for buffered in ring_chunks:
                    try:
                        queue.put_nowait(buffered)
                    except asyncio.QueueFull:
                        break
            self.subscribers[subscriber_id] = queue
            self.total_subscribers += 1
            self._ensure_running_locked()
            metadata_service.notify_stream_activity()
            LOGGER.info(
                "Subscriber %s joined %s; listeners=%d pins=%d",
                subscriber_id[:8], self.key, len(self.subscribers), self.pins,
            )
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            async with self.lock:
                self.subscribers.pop(subscriber_id, None)
                LOGGER.info(
                    "Subscriber %s left %s; listeners=%d pins=%d; linger=%.0fs",
                    subscriber_id[:8], self.key, len(self.subscribers), self.pins, STREAM_LINGER_SECONDS,
                )
                self._schedule_linger_locked()

    async def pin(self) -> None:
        async with self.lock:
            self.pins += 1
            self._ensure_running_locked()
            metadata_service.notify_stream_activity()
            LOGGER.info("Pinned %s; listeners=%d pins=%d", self.key, len(self.subscribers), self.pins)

    async def unpin(self) -> None:
        async with self.lock:
            self.pins = max(0, self.pins - 1)
            LOGGER.info("Unpinned %s; listeners=%d pins=%d", self.key, len(self.subscribers), self.pins)
            self._schedule_linger_locked()

    async def wait_for_real_audio(self, timeout: float) -> None:
        await asyncio.wait_for(self.real_audio_ready.wait(), timeout=timeout)

    async def stop(self) -> None:
        async with self.lock:
            self._cancel_linger_locked()
            task = self.task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict[str, Any]:
        now_mono = time.monotonic()
        recent_bytes = sum(size for ts, size in self._rate_window if ts >= now_mono - 10.0)
        if self._rate_window:
            span = max(1.0, now_mono - max(self._rate_window[0][0], now_mono - 10.0))
            recent_bps = recent_bytes / span
        else:
            recent_bps = 0.0
        queue_depths = [q.qsize() for q in self.subscribers.values()]
        return {
            "channel_id": self.channel.channel_id,
            "name": self.channel.name,
            "format": self.fmt,
            "running": bool(self.task and not self.task.done()),
            "listeners": len(self.subscribers),
            "pins": self.pins,
            "real_audio_ready": self.real_audio_ready.is_set(),
            "linger_seconds": STREAM_LINGER_SECONDS,
            "started_at": self.started_at,
            "last_real_audio_at": self.last_real_audio_at,
            "last_chunk_at": self.last_chunk_at,
            "last_real_chunk_at": self.last_real_chunk_at,
            "seconds_since_last_chunk": round(now_mono - self._last_chunk_monotonic, 3) if self._last_chunk_monotonic else None,
            "seconds_since_last_real_chunk": round(now_mono - self._last_real_chunk_monotonic, 3) if self._last_real_chunk_monotonic else None,
            "total_output_bytes": self.total_output_bytes,
            "real_audio_bytes": self.real_audio_bytes,
            "total_chunks": self.total_chunks,
            "real_audio_chunks": self.real_audio_chunks,
            "recent_real_bytes_per_second": round(recent_bps, 1),
            "recent_real_kbps": round((recent_bps * 8) / 1000, 1),
            "subscriber_queue_depth": max(queue_depths, default=0),
            "subscriber_queue_depths": queue_depths,
            "subscriber_queue_capacity": STREAM_SUBSCRIBER_QUEUE_CHUNKS,
            "max_queue_depth": self.max_queue_depth,
            "avg_chunk_bytes": round(self.total_output_bytes / self.total_chunks, 1) if self.total_chunks else 0.0,
            "coalesce_bytes": STREAM_COALESCE_BYTES,
            "coalesce_max_seconds": STREAM_COALESCE_MAX_SECONDS,
            "ring_buffer_seconds": STREAM_RING_BUFFER_SECONDS,
            "ring_buffer_chunks": len(self._ring_buffer),
            "upstream_http_503_count": self.upstream_http_503_count,
            "upstream_http_error_count": self.upstream_http_error_count,
            "recovery_count": self.recovery_count,
            "silence_fallback_count": self.silence_fallback_count,
            "dropped_chunks": self.dropped_chunks,
            "slow_subscriber_disconnects": self.slow_subscriber_disconnects,
            "restart_count": self.restart_count,
            "last_error": self.last_error,
        }


class StreamManager:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.hubs: dict[str, SharedStreamHub] = {}

    async def _hub(self, channel: Channel, fmt: str) -> SharedStreamHub:
        key = f"{channel.channel_id}:{fmt}"
        async with self.lock:
            hub = self.hubs.get(key)
            if hub is None:
                hub = SharedStreamHub(channel, fmt)
                self.hubs[key] = hub
            else:
                hub.channel = channel
            return hub

    async def subscribe(self, channel: Channel, fmt: str) -> AsyncIterator[bytes]:
        hub = await self._hub(channel, fmt)
        async for chunk in hub.subscribe():
            yield chunk

    @asynccontextmanager
    async def hold_warm(self, channel: Channel, fmt: str) -> AsyncIterator[SharedStreamHub]:
        hub = await self._hub(channel, fmt)
        await hub.pin()
        try:
            if STREAM_WARMUP_ENABLED:
                await hub.wait_for_real_audio(STREAM_WARMUP_TIMEOUT)
                LOGGER.info("Shared stream ready for %s; holding same upstream during MA add", channel.name)
            yield hub
        finally:
            await hub.unpin()

    def has_active_hubs(self) -> bool:
        return any(hub.task and not hub.task.done() for hub in self.hubs.values())

    async def stop(self) -> None:
        async with self.lock:
            hubs = list(self.hubs.values())
        await asyncio.gather(*(hub.stop() for hub in hubs), return_exceptions=True)

    def status(self) -> dict[str, Any]:
        rows = [hub.status() for hub in self.hubs.values() if hub.task and not hub.task.done()]
        return {
            "active_hubs": len(rows),
            "listeners": sum(row["listeners"] for row in rows),
            "pins": sum(row["pins"] for row in rows),
            "linger_seconds": STREAM_LINGER_SECONDS,
            "hubs": rows,
        }


class MusicAssistantSyncService:
    """Keep Music Assistant Builtin Radio entries aligned with the bridge catalog.

    The service only considers a Music Assistant radio "managed" when its Builtin
    provider URL begins with MA_MANAGED_URL_PREFIX. This prevents automatic cleanup
    from touching unrelated manually-added radio stations.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.pending_channels: list[Channel] | None = None
        self.pending_reason = ""
        self.last_sync_at = ""
        self.last_error = ""
        self.last_reason = ""
        self.last_desired_count = 0
        self.last_synced_count = 0
        self.last_unchanged_count = 0
        self.last_add_failures = 0
        self.last_failed_radios: list[dict[str, str]] = []
        self.last_removed_count = 0
        self.last_remove_failures = 0

    @property
    def enabled(self) -> bool:
        return MA_AUTO_SYNC

    def request_sync(self, channels: list[Channel], reason: str) -> None:
        if not self.enabled:
            return
        # Never schedule destructive reconciliation from an empty source catalog.
        if not channels:
            LOGGER.warning("Skipping Music Assistant sync because source catalog is empty")
            return
        self.pending_channels = list(channels)
        self.pending_reason = reason
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._worker(), name="music-assistant-sync")

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    async def _worker(self) -> None:
        while self.pending_channels is not None:
            channels = self.pending_channels
            reason = self.pending_reason
            self.pending_channels = None
            self.pending_reason = ""
            try:
                await self.sync(channels, reason=reason)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                LOGGER.warning("Music Assistant sync failed (%s): %s", reason, exc)
                # Retry the latest catalog after the configured delay. If another
                # catalog refresh arrives first it replaces this pending snapshot.
                await asyncio.sleep(MA_SYNC_RETRY_SECONDS)
                if self.pending_channels is None:
                    self.pending_channels = channels
                    self.pending_reason = f"retry:{reason}"

    async def _call(self, command: str, args: dict[str, Any]) -> Any:
        if not MUSIC_ASSISTANT_TOKEN:
            raise RuntimeError("MUSIC_ASSISTANT_TOKEN is empty")
        payload = {
            "command": command,
            "message_id": uuid.uuid4().hex,
            "args": args,
        }
        headers = {
            "Authorization": f"Bearer {MUSIC_ASSISTANT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": DISPATCHARR_USER_AGENT,
        }
        async with httpx.AsyncClient(timeout=MA_SYNC_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(f"{MUSIC_ASSISTANT_URL}/api", json=payload, headers=headers)
        if response.status_code >= 400:
            detail = response.text.strip()
            if len(detail) > 300:
                detail = detail[:300] + "..."
            raise RuntimeError(f"MA {command} failed HTTP {response.status_code}: {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _managed_url_from_item(item: dict[str, Any]) -> str:
        mappings = item.get("provider_mappings") or []
        if isinstance(mappings, dict):
            mappings = list(mappings.values())
        if not isinstance(mappings, list):
            return ""
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            domain = str(mapping.get("provider_domain") or mapping.get("provider") or "")
            instance = str(mapping.get("provider_instance") or "")
            item_id = str(mapping.get("item_id") or "")
            if domain == "builtin" or instance == "builtin":
                if item_id.startswith(MA_MANAGED_URL_PREFIX):
                    return item_id
        return ""

    async def _managed_library_radios(self) -> list[dict[str, Any]]:
        managed: list[dict[str, Any]] = []
        offset = 0
        limit = 500
        while True:
            rows = await self._call(
                "music/radios/library_items",
                {"limit": limit, "offset": offset, "summary": False},
            )
            if not isinstance(rows, list):
                raise RuntimeError("MA music/radios/library_items returned an unexpected response")
            for row in rows:
                if isinstance(row, dict) and self._managed_url_from_item(row):
                    managed.append(row)
            if len(rows) < limit:
                break
            offset += limit
        return managed

    async def _add_radio_with_warm_source(self, channel: Channel, args: dict[str, Any]) -> None:
        """Warm the source, keep it open through every MA add retry, then close it."""
        async with stream_manager.hold_warm(channel, IMPORT_FORMAT):
            last_exc: Exception | None = None
            for attempt in range(1, MA_ADD_RETRIES + 1):
                try:
                    await self._call("builtin/add_radio", args)
                    if MA_IMPORT_RELEASE_GRACE_SECONDS:
                        await asyncio.sleep(MA_IMPORT_RELEASE_GRACE_SECONDS)
                    return
                except Exception as exc:
                    last_exc = exc
                    LOGGER.warning(
                        "MA add_radio failed for %s (attempt %d/%d) while warm source remains open: %s",
                        channel.name, attempt, MA_ADD_RETRIES, exc,
                    )
                    if attempt < MA_ADD_RETRIES and MA_ADD_RETRY_DELAY:
                        await asyncio.sleep(MA_ADD_RETRY_DELAY)
            assert last_exc is not None
            raise last_exc

    async def sync(self, channels: list[Channel], reason: str = "manual") -> dict[str, Any]:
        if IMPORT_FORMAT not in {"aac", "mp3"}:
            raise RuntimeError("IMPORT_FORMAT must be aac or mp3")
        if not channels:
            raise RuntimeError("Refusing to sync an empty source catalog")
        async with self.lock:
            desired = {ch.bridge_url(IMPORT_FORMAT): ch for ch in channels}
            self.last_desired_count = len(desired)
            self.last_reason = reason

            # Always read bridge-managed MA radios so unchanged stations can be
            # skipped. This avoids warming/re-probing the entire catalog every day.
            existing_managed = await self._managed_library_radios()
            existing_by_url = {
                self._managed_url_from_item(item): item
                for item in existing_managed
                if self._managed_url_from_item(item)
            }

            synced = 0
            unchanged = 0
            add_failures = 0
            failed_radios: list[dict[str, str]] = []
            # Intentionally sequential by default. Dispatcharr may need to fill its
            # cache for a cold channel; parallel warm-ups can stampede upstream.
            if MA_SYNC_CONCURRENCY != 1:
                LOGGER.warning("MA_SYNC_CONCURRENCY=%d requested; v5 serializes warm/import operations for source safety", MA_SYNC_CONCURRENCY)
            for url, channel in desired.items():
                existing = existing_by_url.get(url)
                if existing and _existing_radio_name(existing) == channel.name:
                    unchanged += 1
                    continue
                args: dict[str, Any] = {"url": url, "name": channel.name}
                if IMPORT_LOGOS and channel.logo:
                    args["image_url"] = channel.logo
                try:
                    await self._add_radio_with_warm_source(channel, args)
                    synced += 1
                except Exception as exc:
                    add_failures += 1
                    if len(failed_radios) < 25:
                        failed_radios.append({"name": channel.name, "url": url, "error": str(exc)[:300]})
                    LOGGER.warning("Could not add/update MA radio %s: %s", channel.name, exc)

            removed = 0
            remove_failures = 0
            if MA_REMOVE_MISSING:
                desired_urls = set(desired)
                for item in existing_managed:
                    managed_url = self._managed_url_from_item(item)
                    if not managed_url or managed_url in desired_urls:
                        continue
                    library_id = item.get("item_id")
                    if library_id in (None, ""):
                        continue
                    try:
                        # Current MA radio controller exposes music/radios/remove.
                        # This requires Library Manage scope on the token.
                        await self._call(
                            "music/radios/remove",
                            {"item_id": library_id, "recursive": True},
                        )
                        removed += 1
                    except Exception as exc:
                        remove_failures += 1
                        LOGGER.warning("Could not remove stale MA radio %s: %s", managed_url, exc)

            self.last_synced_count = synced
            self.last_unchanged_count = unchanged
            self.last_add_failures = add_failures
            self.last_failed_radios = failed_radios
            self.last_removed_count = removed
            self.last_remove_failures = remove_failures
            self.last_sync_at = utc_now_iso()
            errors = []
            if add_failures:
                errors.append(f"{add_failures} radio add/update(s) failed")
            if remove_failures:
                errors.append(f"{remove_failures} stale radio removal(s) failed")
            self.last_error = "; ".join(errors)
            LOGGER.info(
                "Music Assistant sync complete: %d add/update, %d unchanged, %d add failure(s), %d removed, %d removal failure(s)",
                synced,
                unchanged,
                add_failures,
                removed,
                remove_failures,
            )
            return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": bool(MUSIC_ASSISTANT_URL and MUSIC_ASSISTANT_TOKEN),
            "url": MUSIC_ASSISTANT_URL,
            "format": IMPORT_FORMAT,
            "remove_missing": MA_REMOVE_MISSING,
            "managed_url_prefix": MA_MANAGED_URL_PREFIX,
            "warmup_enabled": STREAM_WARMUP_ENABLED,
            "warmup_timeout": STREAM_WARMUP_TIMEOUT,
            "warmup_retries": STREAM_WARMUP_RETRIES,
            "add_retries": MA_ADD_RETRIES,
            "last_sync": self.last_sync_at,
            "last_reason": self.last_reason,
            "last_error": self.last_error,
            "desired_count": self.last_desired_count,
            "synced_count": self.last_synced_count,
            "unchanged_count": self.last_unchanged_count,
            "add_failures": self.last_add_failures,
            "failed_radios": self.last_failed_radios,
            "removed_count": self.last_removed_count,
            "remove_failures": self.last_remove_failures,
            "sync_in_progress": bool(self.task and not self.task.done()),
        }


class MetadataService:
    def __init__(self) -> None:
        self.items_by_number: dict[str, NowPlaying] = {}
        self.items_by_name: dict[str, NowPlaying] = {}
        self.provider = self._select_provider()
        self.last_update = ""
        self.last_error = ""
        self.task: asyncio.Task | None = None
        self._xm_stations_by_id: dict[str, dict[str, str]] = {}
        self._xm_station_refresh = 0.0
        self.fallback_provider = METADATA_FALLBACK if METADATA_FALLBACK in {"xmplaylist", "stellar", "none", "disabled"} else "xmplaylist"
        self._ticker_channels: list[dict[str, Any]] = []
        self._ticker_channels_refreshed = 0.0
        self.active_source = self.provider
        self._activity_event = asyncio.Event()
        self._ticker_polling_active = False
        self._icy_by_number: dict[str, bytes] = {}
        self._icy_by_name: dict[str, bytes] = {}

    def _select_provider(self) -> str:
        if not METADATA_ENABLED:
            return "disabled"
        if METADATA_PROVIDER in {"ticker", "xmplaylist", "stellar"}:
            if METADATA_PROVIDER == "stellar" and not STELLAR_API_KEY:
                LOGGER.warning("METADATA_PROVIDER=stellar but STELLAR_API_KEY is empty; falling back to xmplaylist")
                return "xmplaylist"
            return METADATA_PROVIDER
        # auto keeps historical behavior; choose ticker explicitly to use its free
        # 15-second bulk now-playing feed.
        return "stellar" if STELLAR_API_KEY else "xmplaylist"

    async def start(self) -> None:
        if self.provider == "disabled":
            return
        self.task = asyncio.create_task(self._run(), name="metadata-poller")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def notify_stream_activity(self) -> None:
        """Wake the Ticker poller when a Sirius stream becomes active."""
        if self.provider == "ticker":
            self._activity_event.set()

    def _ticker_has_active_streams(self) -> bool:
        # A hub remains running during STREAM_LINGER_SECONDS, so this naturally
        # keeps fast metadata polling alive through the linger window.
        return stream_manager.has_active_hubs()

    async def _wait_for_ticker_activity(self) -> None:
        self._ticker_polling_active = False
        self.active_source = "ticker-idle"
        if TICKER_IDLE_POLL_SECONDS > 0:
            try:
                await asyncio.wait_for(self._activity_event.wait(), timeout=TICKER_IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                return
        else:
            await self._activity_event.wait()
        self._activity_event.clear()

    async def _run(self) -> None:
        while True:
            try:
                if self.provider == "ticker":
                    active = self._ticker_has_active_streams()
                    if not active:
                        await self._wait_for_ticker_activity()
                        active = self._ticker_has_active_streams()
                        if not active and TICKER_IDLE_POLL_SECONDS <= 0:
                            continue

                    self._ticker_polling_active = active
                    try:
                        await self._refresh_ticker()
                        self.active_source = "ticker" if active else "ticker-idle-refresh"
                        sleep_for = TICKER_ACTIVE_POLL_SECONDS if active else TICKER_IDLE_POLL_SECONDS
                    except Exception as ticker_exc:
                        self.last_error = str(ticker_exc)
                        LOGGER.warning("Ticker metadata refresh failed: %s", ticker_exc)
                        if self.fallback_provider == "stellar" and STELLAR_API_KEY:
                            await self._refresh_stellar()
                            self.active_source = "stellar-fallback"
                        elif self.fallback_provider == "xmplaylist":
                            await self._refresh_xmplaylist()
                            self.active_source = "xmplaylist-fallback"
                        sleep_for = min(TICKER_ACTIVE_POLL_SECONDS if active else max(TICKER_IDLE_POLL_SECONDS, 30.0), 60.0)
                elif self.provider == "stellar":
                    await self._refresh_stellar()
                    self.active_source = "stellar"
                    sleep_for = STELLAR_POLL_SECONDS
                else:
                    await self._refresh_xmplaylist()
                    self.active_source = "xmplaylist"
                    sleep_for = XMPLAYLIST_POLL_SECONDS
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep audio alive even if metadata provider is down
                self.last_error = str(exc)
                LOGGER.warning("Metadata refresh failed (%s): %s", self.provider, exc)
                sleep_for = 30
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def _replace_cache(self, items: list[NowPlaying]) -> None:
        by_number: dict[str, NowPlaying] = {}
        by_name: dict[str, NowPlaying] = {}
        for item in items:
            if item.channel_number:
                by_number[clean_channel_number(item.channel_number)] = item
            norm = normalize_name(item.channel_name)
            if norm:
                by_name[norm] = item
        # Build ICY blocks before the atomic cache swap. Stream delivery never
        # performs provider I/O or JSON parsing; it only reads immutable bytes.
        icy_by_number: dict[str, bytes] = {}
        icy_by_name: dict[str, bytes] = {}
        for item in items:
            block = _icy_block_for_now_playing(item)
            if item.channel_number:
                icy_by_number[clean_channel_number(item.channel_number)] = block
            norm = normalize_name(item.channel_name)
            if norm:
                icy_by_name[norm] = block
        self.items_by_number = by_number
        self.items_by_name = by_name
        self._icy_by_number = icy_by_number
        self._icy_by_name = icy_by_name
        self.last_update = utc_now_iso()
        LOGGER.info("Metadata cache updated from %s with %d channel(s)", self.provider, len(items))

    async def _refresh_xm_station_catalog(self, client: httpx.AsyncClient) -> None:
        if self._xm_stations_by_id and time.monotonic() - self._xm_station_refresh < 6 * 3600:
            return
        url = f"{XMPLAYLIST_BASE_URL}/api/station"
        stations: dict[str, dict[str, str]] = {}
        for _ in range(10):
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            for station in payload.get("results", []):
                station_id = str(station.get("id", ""))
                if station_id:
                    station_row = {
                        "name": str(station.get("name", "")),
                        "number": clean_channel_number(station.get("number", "")),
                        "deeplink": str(station.get("deeplink", "")),
                        "image": str(station.get("imageUrl") or ""),
                    }
                    # /api/station uses a UUID in `id`, while /api/feed uses
                    # the station `deeplink` string in `channelId` (for example
                    # "thepulse" or "poprocks").  Index by both values so live
                    # feed rows resolve correctly.
                    stations[station_id] = station_row
                    if station_row["deeplink"]:
                        stations[station_row["deeplink"]] = station_row
            next_url = payload.get("next")
            if not next_url:
                break
            url = urljoin(f"{XMPLAYLIST_BASE_URL}/", str(next_url))
        self._xm_stations_by_id = stations
        self._xm_station_refresh = time.monotonic()

    async def _refresh_xmplaylist(self) -> None:
        headers = {"User-Agent": DISPATCHARR_USER_AGENT}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            await self._refresh_xm_station_catalog(client)
            response = await client.get(f"{XMPLAYLIST_BASE_URL}/api/feed")
            response.raise_for_status()
            payload = response.json()

        newest: dict[str, dict[str, Any]] = {}
        for play in payload.get("results", []):
            channel_id = str(play.get("channelId", ""))
            if not channel_id or channel_id not in self._xm_stations_by_id:
                continue
            previous = newest.get(channel_id)
            if previous is None or str(play.get("timestamp", "")) > str(previous.get("timestamp", "")):
                newest[channel_id] = play

        fetched = time.time()
        items: list[NowPlaying] = []
        for channel_id, play in newest.items():
            station = self._xm_stations_by_id[channel_id]
            track = play.get("track") or {}
            spotify = play.get("spotify") or {}
            artists = track.get("artists") or []
            if isinstance(artists, str):
                artist = artists
            else:
                artist = ", ".join(str(x) for x in artists if x)
            artwork = (
                spotify.get("albumImageLarge")
                or spotify.get("albumImageMedium")
                or spotify.get("albumImageSmall")
                or station.get("image")
                or ""
            )
            items.append(
                NowPlaying(
                    provider="xmplaylist",
                    channel_name=station.get("name", ""),
                    channel_number=station.get("number", ""),
                    artist=artist,
                    title=str(track.get("title") or ""),
                    album="",  # xmplaylist public feed does not expose album name
                    artwork_url=str(artwork or ""),
                    timestamp=str(play.get("timestamp") or ""),
                    fetched_at=fetched,
                )
            )
        self._replace_cache(items)

    @staticmethod
    def _first(obj: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in obj and obj[key] not in (None, ""):
                return obj[key]
        return None

    @classmethod
    def _parse_stellar_item(cls, raw: dict[str, Any]) -> NowPlaying | None:
        channel = raw.get("channel") if isinstance(raw.get("channel"), dict) else {}
        now = raw.get("now_playing") if isinstance(raw.get("now_playing"), dict) else (raw.get("nowPlaying") if isinstance(raw.get("nowPlaying"), dict) else {})
        track = raw.get("track") if isinstance(raw.get("track"), dict) else (now.get("track") if isinstance(now.get("track"), dict) else now)
        artwork_obj = raw.get("artwork") if isinstance(raw.get("artwork"), dict) else {}

        channel_name = cls._first(raw, "channel_name", "channelName") or cls._first(channel, "name", "channel_name", "channelName")
        if not channel_name and not channel:
            candidate = raw.get("name")
            if candidate and any(k in raw for k in ("artist", "title", "song", "track")):
                channel_name = candidate
        channel_number = (
            cls._first(raw, "channel_number", "channelNumber", "number")
            or cls._first(channel, "number", "channel_number", "channelNumber")
            or ""
        )
        artist = cls._first(raw, "artist", "artist_name", "artistName") or cls._first(now, "artist", "artist_name", "artistName") or cls._first(track, "artist", "artist_name", "artistName") or ""
        if isinstance(artist, list):
            artist = ", ".join(str(x) for x in artist if x)
        title = cls._first(raw, "title", "song", "song_title", "songTitle") or cls._first(now, "title", "song", "song_title", "songTitle") or cls._first(track, "title", "name", "song") or ""
        album = cls._first(raw, "album", "album_name", "albumName") or cls._first(now, "album", "album_name", "albumName") or cls._first(track, "album", "album_name", "albumName") or ""
        if isinstance(album, dict):
            album = cls._first(album, "name", "title") or ""
        artwork = (
            cls._first(raw, "artwork_url", "artworkUrl", "image_url", "imageUrl", "album_art", "albumArt", "artwork")
            or cls._first(now, "artwork_url", "artworkUrl", "image_url", "imageUrl", "album_art", "albumArt", "artwork")
            or cls._first(artwork_obj, "url", "large", "medium", "small")
            or cls._first(track, "artwork_url", "artworkUrl", "image_url", "imageUrl")
            or ""
        )
        timestamp = cls._first(raw, "timestamp", "updated_at", "updatedAt", "played_at", "playedAt") or ""
        if not channel_name:
            return None
        return NowPlaying(
            provider="stellar",
            channel_name=str(channel_name),
            channel_number=clean_channel_number(channel_number),
            artist=str(artist),
            title=str(title),
            album=str(album),
            artwork_url=str(artwork),
            timestamp=str(timestamp),
            fetched_at=time.time(),
        )

    @staticmethod
    def _stellar_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("results", "channels", "nowplaying", "now_playing", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                for inner in ("results", "channels", "items"):
                    if isinstance(value.get(inner), list):
                        return [x for x in value[inner] if isinstance(x, dict)]
                mapped = [x for x in value.values() if isinstance(x, dict)]
                if mapped:
                    return mapped
        mapped = [x for x in payload.values() if isinstance(x, dict)]
        return mapped if mapped else []

    async def _refresh_stellar(self) -> None:
        headers = {"User-Agent": DISPATCHARR_USER_AGENT, "X-API-Key": STELLAR_API_KEY}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            response = await client.get(f"{STELLAR_BASE_URL}/nowplaying")
            response.raise_for_status()
            payload = response.json()
        items = []
        for raw in self._stellar_rows(payload):
            item = self._parse_stellar_item(raw)
            if item:
                items.append(item)
        if not items:
            raise RuntimeError("StellarTunerLog returned no parseable now-playing rows")
        self._replace_cache(items)

    async def _refresh_ticker_channels(self, client: httpx.AsyncClient) -> None:
        if self._ticker_channels and time.monotonic() - self._ticker_channels_refreshed < TICKER_CHANNEL_REFRESH_SECONDS:
            return
        response = await client.get(TICKER_CHANNEL_URL)
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("channels", {}) if isinstance(payload, dict) else {}
        if isinstance(raw, dict):
            rows = [value for value in raw.values() if isinstance(value, dict)]
        elif isinstance(raw, list):
            rows = [value for value in raw if isinstance(value, dict)]
        else:
            rows = []
        if not rows:
            raise RuntimeError("Ticker channels feed returned no channels")
        self._ticker_channels = rows
        self._ticker_channels_refreshed = time.monotonic()

    @staticmethod
    def _ticker_norm(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    @classmethod
    def _ticker_items(cls, channel_rows: list[dict[str, Any]], stations: dict[str, Any], fetched: float | None = None) -> list[NowPlaying]:
        fetched = fetched or time.time()
        items: list[NowPlaying] = []
        non_song = {"talk", "exp", "perm", "pgm_segment", "link", "spot", "promo"}
        useful_program = {"talk", "pgm_segment", "exp", "perm", "link"}
        for row in channel_rows:
            deeplink = str(row.get("deeplink_id") or row.get("deeplink") or row.get("id") or "").strip()
            if not deeplink:
                continue
            live = stations.get(deeplink)
            if not isinstance(live, dict):
                continue
            cut_type = str(live.get("cut_type") or "").lower()
            artist = str(live.get("artist") or "")
            title = str(live.get("title") or "")
            if cut_type in non_song and cut_type not in useful_program:
                artist = ""
                title = ""
            channel_name = str(row.get("name") or live.get("channel_name") or deeplink)
            number = clean_channel_number(row.get("channel_number") or row.get("number") or "")
            artwork = str(live.get("artwork_url") or live.get("image_url") or live.get("image") or row.get("artwork_url") or row.get("image_url") or row.get("logo") or "")
            items.append(NowPlaying(
                provider="ticker", channel_name=channel_name, channel_number=number,
                artist=artist, title=title, album=str(live.get("album") or ""),
                artwork_url=artwork, timestamp=str(live.get("timestamp") or live.get("updated_at") or ""),
                fetched_at=fetched,
            ))
        return items

    async def _refresh_ticker(self) -> None:
        headers = {"User-Agent": "Ticker/0.1 Dispatcharr-MA-Bridge/5.5"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            await self._refresh_ticker_channels(client)
            response = await client.get(TICKER_NOWPLAYING_URL)
            response.raise_for_status()
            payload = response.json()
        stations = payload.get("stations", {}) if isinstance(payload, dict) else {}
        if not isinstance(stations, dict) or not stations:
            raise RuntimeError("Ticker now-playing feed returned no stations")
        items = self._ticker_items(self._ticker_channels, stations, fetched=time.time())
        if not items:
            raise RuntimeError("Ticker now-playing feed had no matchable station rows")
        self._replace_cache(items)

    def get(self, channel: Channel) -> NowPlaying | None:
        item = None
        if channel.channel_number:
            item = self.items_by_number.get(clean_channel_number(channel.channel_number))
        if item is None:
            item = self.items_by_name.get(normalize_name(channel.name))
        if item is None:
            return None
        if item.fetched_at and time.time() - item.fetched_at > METADATA_STALE_SECONDS:
            return None
        return item

    def get_icy_block(self, channel: Channel) -> bytes | None:
        # Local, non-blocking cache lookup only. If the corresponding metadata
        # is stale, do not inject an old song into a newly playing stream.
        item = self.get(channel)
        if item is None:
            return None
        if channel.channel_number:
            block = self._icy_by_number.get(clean_channel_number(channel.channel_number))
            if block is not None:
                return block
        return self._icy_by_name.get(normalize_name(channel.name))

    def status(self) -> dict[str, Any]:
        return {
            "enabled": METADATA_ENABLED,
            "icy_metadata_enabled": ICY_METADATA_ENABLED,
            "provider": self.provider,
            "active_source": self.active_source,
            "fallback_provider": self.fallback_provider if self.provider == "ticker" else "",
            "ticker_active_poll_seconds": TICKER_ACTIVE_POLL_SECONDS if self.provider == "ticker" else None,
            "ticker_idle_poll_seconds": TICKER_IDLE_POLL_SECONDS if self.provider == "ticker" else None,
            "ticker_polling_active": self._ticker_polling_active if self.provider == "ticker" else None,
            "channels_cached": len(self.items_by_name),
            "last_update": self.last_update,
            "last_error": self.last_error,
        }


catalog = ChannelCatalog()
metadata_service = MetadataService()
stream_manager = StreamManager()
ma_sync_service = MusicAssistantSyncService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        initial_channels = await catalog.refresh(force=True)
        if MA_AUTO_SYNC and MA_SYNC_ON_START and not catalog.last_error:
            ma_sync_service.request_sync(initial_channels, reason="startup")
    except Exception as exc:
        LOGGER.warning("Initial channel catalog load failed: %s", exc)
    await catalog.start()
    await metadata_service.start()
    try:
        yield
    finally:
        await catalog.stop()
        await metadata_service.stop()
        await ma_sync_service.stop()
        await stream_manager.stop()


app = FastAPI(title=APP_NAME, version="5.5.0", lifespan=lifespan)


def _channel_source_url(channel: Channel) -> str:
    """Return the source URL selected by the active catalog provider."""
    source = channel.source_url.strip()
    if source.startswith(("http://", "https://")):
        return source
    base = XC_BASE_URL if SOURCE_MODE == "xc" else DISPATCHARR_BASE_URL
    return urljoin(f"{base}/", source.lstrip("/"))


_audio_codec_cache: dict[str, tuple[float, str]] = {}


async def probe_audio_codec(channel: Channel, force: bool = False) -> str:
    """Return the first audio codec name for a channel, cached for a day by default.

    XC/Dispatcharr catalogs describe channels but do not guarantee the audio codec.
    Music Assistant probes radio URLs when adding them, so an invalid AAC stream-copy
    fails immediately with EOF. Probe once and only use copy when the source is AAC.
    """
    now = time.monotonic()
    cached = _audio_codec_cache.get(channel.channel_id)
    if not force and cached and now - cached[0] < AUDIO_CODEC_CACHE_SECONDS:
        return cached[1]

    source = _channel_source_url(channel)
    cmd = [
        "ffprobe", "-v", "error",
        "-rw_timeout", str(int(SOURCE_RW_TIMEOUT_SECONDS * 1_000_000)),
        "-user_agent", DISPATCHARR_USER_AGENT,
        "-probesize", "128k",
        "-analyzeduration", "1000000",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        source,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=AAC_PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        LOGGER.warning("Audio codec probe timed out for %s", channel.channel_id)
        codec = ""
    else:
        codec = stdout.decode(errors="replace").strip().splitlines()[0].lower() if stdout.strip() else ""
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            if detail:
                LOGGER.warning("Audio codec probe failed for %s: %s", channel.channel_id, detail[-300:])
            codec = ""
    _audio_codec_cache[channel.channel_id] = (now, codec)
    return codec


_upstream_semaphore = asyncio.Semaphore(UPSTREAM_MAX_CONNECTIONS)


def build_ffmpeg_command(channel: Channel, fmt: str, source_codec: str = "", force_transcode: bool = False) -> list[str]:
    source = _channel_source_url(channel)
    common = [
        "ffmpeg", "-hide_banner", "-loglevel", FFMPEG_LOG_LEVEL, "-nostdin",
        # Input tuning for live MPEG-TS/HLS audio sources.
        "-fflags", "+nobuffer+discardcorrupt", "-flags", "low_delay",
        "-probesize", "128k", "-analyzeduration", "1000000",
        "-rw_timeout", str(int(SOURCE_RW_TIMEOUT_SECONDS * 1_000_000)),
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_at_eof", "1",
        "-reconnect_on_network_error", "1", "-reconnect_on_http_error", "408,429,5xx",
        "-reconnect_delay_max", "2", "-user_agent", DISPATCHARR_USER_AGENT,
        "-i", source, "-map", "0:a:0?", "-vn", "-sn", "-dn",
    ]
    if fmt == "aac":
        mode = AAC_MODE if AAC_MODE in {"auto", "copy", "transcode"} else "auto"
        use_copy = (not force_transcode) and (mode == "copy" or (mode == "auto" and source_codec == "aac"))
        if use_copy:
            return common + ["-c:a", "copy", "-f", "adts", "pipe:1"]
        # Reliable fallback for MP2/AC3/E-AC3/unknown XC sources.
        return common + [
            "-ac", CHANNELS, "-ar", SAMPLE_RATE,
            "-c:a", "aac", "-b:a", AAC_BITRATE, "-f", "adts", "pipe:1"
        ]
    if fmt == "mp3":
        return common + [
            "-ac", CHANNELS, "-ar", SAMPLE_RATE,
            "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-f", "mp3", "pipe:1"
        ]
    raise ValueError(f"Unsupported format: {fmt}")


async def _drain_stderr(stream: asyncio.StreamReader | None, channel_id: str, diagnostic_callback=None) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        raw = line.decode(errors="replace").rstrip()
        safe = redact_secrets(raw)
        # Intentional process termination during linger/container shutdown is not
        # an upstream failure and should not look alarming in normal logs.
        if "Immediate exit requested" in raw or "Exiting normally, received signal" in raw:
            LOGGER.debug("ffmpeg[%s]: %s", channel_id[:8], safe)
            continue
        if diagnostic_callback:
            if "HTTP error 503" in raw:
                diagnostic_callback("http_503")
            elif "HTTP error" in raw:
                diagnostic_callback("http_error")
        LOGGER.warning("ffmpeg[%s]: %s", channel_id[:8], safe)


async def ffmpeg_audio_stream(channel: Channel, fmt: str, force_transcode: bool = False, diagnostic_callback=None) -> AsyncIterator[bytes]:
    """Run one FFmpeg process for the life of an active v5 hub.

    FFmpeg owns HTTP reconnects internally. The bridge does not kill a healthy
    process because of a short read stall; it only respawns after FFmpeg exits.
    """
    channel_id = channel.channel_id
    source_codec = ""
    if fmt == "aac" and AAC_MODE == "auto" and not force_transcode:
        source_codec = await probe_audio_codec(channel)
    cmd = build_ffmpeg_command(channel, fmt, source_codec=source_codec, force_transcode=force_transcode)
    audio_mode = "copy" if fmt == "aac" and "copy" in cmd else "transcode"
    LOGGER.info(
        "Starting v5 %s stream for %s (%s): source_codec=%s mode=%s chunk=%d",
        fmt, channel.name, channel_id, source_codec or "unknown", audio_mode, STREAM_READ_CHUNK_BYTES
    )
    await _upstream_semaphore.acquire()
    proc = None
    stderr_task = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=256 * 1024
        )
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, channel_id, diagnostic_callback))
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(STREAM_READ_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
        return_code = await proc.wait()
        if return_code != 0:
            LOGGER.warning("ffmpeg exited with code %s for %s", return_code, channel_id)
    except asyncio.CancelledError:
        LOGGER.info("Stopping v5 stream for channel %s", channel_id)
        raise
    finally:
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
        _upstream_semaphore.release()
        LOGGER.info("Stopped v5 stream for channel %s", channel_id)


async def ffmpeg_silence_stream(fmt: str) -> AsyncIterator[bytes]:
    """Generate realtime-paced encoded silence matching the bridge output format."""
    if fmt not in {"aac", "mp3"}:
        raise ValueError(f"Unsupported silence format: {fmt}")
    common = [
        "ffmpeg", "-hide_banner", "-loglevel", FFMPEG_LOG_LEVEL, "-nostdin",
        "-re", "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
        "-vn", "-sn", "-dn", "-ac", CHANNELS, "-ar", SAMPLE_RATE,
    ]
    if fmt == "aac":
        cmd = common + ["-c:a", "aac", "-b:a", AAC_BITRATE, "-f", "adts", "pipe:1"]
    else:
        cmd = common + ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-f", "mp3", "pipe:1"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=256 * 1024
    )
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, "silence"))
    try:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(STREAM_READ_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    except asyncio.CancelledError:
        raise
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass


async def _first_real_audio(channel: Channel, fmt: str, delay: float = 0.0, diagnostic_callback=None) -> tuple[AsyncIterator[bytes], bytes]:
    """Open a real source and return its generator plus first audio chunk."""
    if delay:
        await asyncio.sleep(delay)
    # v5: Dispatcharr's recommended audio profile guarantees AAC-LC, 48 kHz,
    # stereo. Generate startup silence with the same parameters, then allow the
    # real AAC source to use stream-copy so we do not perform a second AAC encode.
    # MP3 still transcodes normally.
    gen = ffmpeg_audio_stream(channel, fmt, force_transcode=False, diagnostic_callback=diagnostic_callback)
    try:
        first = await anext(gen)
    except BaseException:
        await gen.aclose()
        raise
    return gen, first


async def startup_protected_audio_stream(
    channel: Channel,
    fmt: str,
    real_audio_event: asyncio.Event | None = None,
    diagnostic_callback=None,
) -> AsyncIterator[bytes]:
    """v5 startup protection: silence only until the one FFmpeg process has audio.

    After the first real chunk, FFmpeg remains alive and owns reconnect behavior.
    REAL_AUDIO_STALL_SECONDS is diagnostic-only in v5 and never kills/restarts FFmpeg.
    """
    if not STARTUP_SILENCE_ENABLED:
        async for chunk in ffmpeg_audio_stream(channel, fmt, diagnostic_callback=diagnostic_callback):
            if real_audio_event is not None:
                real_audio_event.set()
            yield chunk
        return

    silence_gen = ffmpeg_silence_stream(fmt)
    real_gen = ffmpeg_audio_stream(channel, fmt, diagnostic_callback=diagnostic_callback)
    real_task = asyncio.create_task(anext(real_gen))
    silence_task = asyncio.create_task(anext(silence_gen))
    LOGGER.info("Serving startup silence for %s while v5 FFmpeg starts", channel.name)
    try:
        while True:
            done, _ = await asyncio.wait({real_task, silence_task}, return_when=asyncio.FIRST_COMPLETED)
            if real_task in done:
                try:
                    first = real_task.result()
                except StopAsyncIteration as exc:
                    raise RuntimeError("FFmpeg exited before producing real audio") from exc
                if silence_task and not silence_task.done():
                    silence_task.cancel()
                    try:
                        await silence_task
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass
                await silence_gen.aclose()
                if real_audio_event is not None:
                    real_audio_event.set()
                LOGGER.info("Real audio ready for %s; switching from silence and keeping v5 FFmpeg alive", channel.name)
                yield first
                async for chunk in real_gen:
                    yield chunk
                return
            if silence_task in done:
                try:
                    chunk = silence_task.result()
                except StopAsyncIteration as exc:
                    raise RuntimeError("startup silence generator stopped unexpectedly") from exc
                yield chunk
                silence_task = asyncio.create_task(anext(silence_gen))
    finally:
        if real_task and not real_task.done():
            real_task.cancel()
            try:
                await real_task
            except asyncio.CancelledError:
                pass
        if silence_task and not silence_task.done():
            silence_task.cancel()
            try:
                await silence_task
            except asyncio.CancelledError:
                pass
        try:
            await real_gen.aclose()
        except Exception:
            pass
        try:
            await silence_gen.aclose()
        except Exception:
            pass


def _icy_escape(value: str) -> str:
    return str(value or "").replace("\'", "'").replace("'", "\\'").replace(";", ",")


def _encode_icy_payload(payload: bytes) -> bytes:
    payload = payload[:4080]
    blocks = min(255, (len(payload) + 15) // 16)
    payload = payload[: blocks * 16]
    return bytes([blocks]) + payload.ljust(blocks * 16, b"\0")


def _icy_block_for_now_playing(meta: NowPlaying) -> bytes:
    parts = [f"StreamTitle='{_icy_escape(meta.stream_title)}';"]
    if meta.album:
        parts.append(f"StreamAlbum='{_icy_escape(meta.album)}';")
    if meta.artwork_url:
        parts.append(f"StreamUrl='{_icy_escape(meta.artwork_url)}';")
    parts.append(f"StreamProvider='{_icy_escape(meta.provider)}';")
    return _encode_icy_payload("".join(parts).encode("utf-8"))


def build_icy_block(channel: Channel) -> bytes:
    # This path is intentionally network-free and allocation-light. Metadata
    # polling builds the blocks in the background and atomically swaps them in.
    cached = metadata_service.get_icy_block(channel)
    if cached is not None:
        return cached
    return _encode_icy_payload(f"StreamTitle='{_icy_escape(channel.name)}';".encode("utf-8"))


async def with_icy_metadata(source: AsyncIterator[bytes], channel: Channel) -> AsyncIterator[bytes]:
    """Frame ICY metadata without ever awaiting metadata-provider work.

    Audio byte accounting is exact: exactly ICY_METAINT audio bytes are emitted
    between metadata blocks. Provider updates only replace cached block bytes;
    they cannot pause, resize, or back-pressure the shared upstream hub.
    """
    buffer = bytearray()
    async for chunk in source:
        if not chunk:
            continue
        buffer.extend(chunk)
        while len(buffer) >= ICY_METAINT:
            yield bytes(buffer[:ICY_METAINT])
            del buffer[:ICY_METAINT]
            yield build_icy_block(channel)
    if buffer:
        yield bytes(buffer)

def channel_json(channel: Channel) -> dict[str, Any]:
    now_playing = metadata_service.get(channel)
    return {
        "id": channel.channel_id,
        "name": channel.name,
        "group": channel.group,
        "logo": channel.logo,
        "tvg_id": channel.tvg_id,
        "channel_number": channel.channel_number,
        "dispatcharr_id": channel.dispatcharr_id,
        "metadata_match_key": normalize_name(channel.name),
        "matched_metadata_channel": now_playing.channel_name if now_playing else "",
        "resolved_sirius_channel_number": now_playing.channel_number if now_playing else channel.channel_number,
        "mp3_url": channel.bridge_url("mp3"),
        "aac_url": channel.bridge_url("aac"),
        "now_playing": now_playing.public_dict() if now_playing else None,
    }



async def _timed_json_get(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, verify: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=API_CHECK_TIMEOUT, follow_redirects=True, headers=headers or {}, verify=verify) as client:
        response = await client.get(url, params=params)
    elapsed = round((time.monotonic() - started) * 1000, 1)
    result: dict[str, Any] = {"ok": response.is_success, "http_status": response.status_code, "latency_ms": elapsed}
    if response.is_success:
        try:
            result["json"] = response.json()
        except ValueError:
            result["content_type"] = response.headers.get("content-type", "")
            result["bytes"] = len(response.content)
    else:
        result["error"] = redact_secrets(response.text[:300])
    return result


async def _check_source_api() -> dict[str, Any]:
    if SOURCE_MODE == "xc":
        if not (XC_BASE_URL and XC_USERNAME and XC_PASSWORD):
            return {"status": "fail", "mode": "xc", "error": "XC_BASE_URL/XC_USERNAME/XC_PASSWORD not fully configured"}
        url = f"{XC_BASE_URL}/player_api.php"
        base = {"username": XC_USERNAME, "password": XC_PASSWORD}
        headers = {"User-Agent": DISPATCHARR_USER_AGENT}
        auth, categories, streams = await asyncio.gather(
            _timed_json_get(url, params=base, headers=headers, verify=XC_VERIFY_SSL),
            _timed_json_get(url, params={**base, "action": "get_live_categories"}, headers=headers, verify=XC_VERIFY_SSL),
            _timed_json_get(url, params={**base, "action": "get_live_streams"}, headers=headers, verify=XC_VERIFY_SSL),
        )
        user_info = auth.get("json", {}).get("user_info", {}) if isinstance(auth.get("json"), dict) else {}
        authenticated = str(user_info.get("auth", "0")) == "1"
        category_rows = categories.get("json") if isinstance(categories.get("json"), list) else []
        stream_rows = streams.get("json") if isinstance(streams.get("json"), list) else []
        ok = bool(auth.get("ok") and categories.get("ok") and streams.get("ok") and authenticated)
        return {
            "status": "ok" if ok else "fail", "mode": "xc", "base_url": XC_BASE_URL,
            "authenticated": authenticated,
            "auth": {k: v for k, v in auth.items() if k != "json"},
            "live_categories": {**{k: v for k, v in categories.items() if k != "json"}, "count": len(category_rows)},
            "live_streams": {**{k: v for k, v in streams.items() if k != "json"}, "count": len(stream_rows)},
            "filtered_catalog_count": len(catalog.channels),
        }
    source_url = DISPATCHARR_M3U_URL or f"{DISPATCHARR_BASE_URL}{DISPATCHARR_M3U_PATH}"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=API_CHECK_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(source_url, headers={"User-Agent": DISPATCHARR_USER_AGENT})
        return {
            "status": "ok" if response.is_success else "fail", "mode": "dispatcharr", "url": source_url,
            "http_status": response.status_code, "latency_ms": round((time.monotonic()-started)*1000,1),
            "playlist_bytes": len(response.content), "filtered_catalog_count": len(catalog.channels),
        }
    except Exception as exc:
        return {"status": "fail", "mode": "dispatcharr", "url": source_url, "error": redact_secrets(exc)}


async def _check_music_assistant() -> dict[str, Any]:
    if not MUSIC_ASSISTANT_URL or not MUSIC_ASSISTANT_TOKEN:
        return {"status": "disabled", "configured": False}
    started = time.monotonic()
    try:
        rows = await ma_sync_service._call("music/radios/library_items", {"limit": 1, "offset": 0, "summary": True})
        return {
            "status": "ok", "configured": True, "authenticated": True,
            "radio_library_read": isinstance(rows, list), "latency_ms": round((time.monotonic()-started)*1000,1),
            "builtin_add_radio": "not_mutated_by_health_check",
            "radio_remove_permission": "not_mutated_by_health_check",
        }
    except Exception as exc:
        return {"status": "fail", "configured": True, "authenticated": False, "latency_ms": round((time.monotonic()-started)*1000,1), "error": redact_secrets(exc)}


async def _check_ticker() -> dict[str, Any]:
    headers = {"User-Agent": DISPATCHARR_USER_AGENT}
    try:
        channels, now = await asyncio.gather(
            _timed_json_get(TICKER_CHANNEL_URL, headers=headers),
            _timed_json_get(TICKER_NOWPLAYING_URL, headers=headers),
        )
        cp = channels.get("json")
        np = now.get("json")
        raw_channels = cp.get("channels", {}) if isinstance(cp, dict) else {}
        raw_stations = np.get("stations", {}) if isinstance(np, dict) else {}
        channel_count = len(raw_channels) if isinstance(raw_channels, (dict, list)) else 0
        station_count = len(raw_stations) if isinstance(raw_stations, dict) else 0
        ok = bool(channels.get("ok") and now.get("ok") and channel_count and station_count)
        return {
            "status": "ok" if ok else "fail",
            "channels": {**{k:v for k,v in channels.items() if k != "json"}, "count": channel_count},
            "now_playing": {**{k:v for k,v in now.items() if k != "json"}, "count": station_count},
            "polling_active": metadata_service.status().get("ticker_polling_active"),
            "cached_matches": len(metadata_service.items_by_name),
        }
    except Exception as exc:
        return {"status": "fail", "error": redact_secrets(exc)}


async def _check_xmplaylist() -> dict[str, Any]:
    headers = {"User-Agent": DISPATCHARR_USER_AGENT}
    try:
        stations, feed = await asyncio.gather(
            _timed_json_get(f"{XMPLAYLIST_BASE_URL}/api/station", headers=headers),
            _timed_json_get(f"{XMPLAYLIST_BASE_URL}/api/feed", headers=headers),
        )
        sp = stations.get("json") if isinstance(stations.get("json"), dict) else {}
        fp = feed.get("json") if isinstance(feed.get("json"), dict) else {}
        sc = len(sp.get("results", [])) if isinstance(sp.get("results"), list) else 0
        fc = len(fp.get("results", [])) if isinstance(fp.get("results"), list) else 0
        ok = bool(stations.get("ok") and feed.get("ok"))
        return {"status": "ok" if ok else "fail", "stations": {**{k:v for k,v in stations.items() if k != "json"}, "page_count": sc}, "feed": {**{k:v for k,v in feed.items() if k != "json"}, "rows": fc}}
    except Exception as exc:
        return {"status": "fail", "error": redact_secrets(exc)}


async def _run_process_check(*cmd: str) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=API_CHECK_TIMEOUT)
        return proc.returncode == 0, out.decode(errors="replace")
    except Exception as exc:
        return False, str(exc)


async def _check_ffmpeg() -> dict[str, Any]:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if not ffmpeg_path:
        return {"status": "fail", "ffmpeg_present": False, "ffprobe_present": bool(ffprobe_path)}
    ok_ver, ver = await _run_process_check(ffmpeg_path, "-version")
    ok_enc, enc = await _run_process_check(ffmpeg_path, "-hide_banner", "-encoders")
    version_line = ver.splitlines()[0] if ver else ""
    return {
        "status": "ok" if ok_ver and ffprobe_path else "fail", "ffmpeg_present": True, "ffprobe_present": bool(ffprobe_path),
        "version": version_line, "aac_encoder": bool(ok_enc and re.search(r"\baac\b", enc)),
        "libmp3lame_encoder": bool(ok_enc and "libmp3lame" in enc), "aac_mode": AAC_MODE,
    }


def _check_bridge_local() -> dict[str, Any]:
    cache = Path(BRIDGE_CACHE_FILE) if BRIDGE_CACHE_FILE else None
    cache_parent = cache.parent if cache else None
    writable = bool(cache_parent and cache_parent.exists() and os.access(cache_parent, os.W_OK))
    return {
        "status": "ok" if (not cache or writable) else "warn", "version": "5.5.0",
        "catalog_channels": len(catalog.channels), "cache_file": str(cache) if cache else "", "cache_parent_writable": writable if cache else None,
        "metadata_provider": metadata_service.provider, "metadata_active_source": metadata_service.active_source,
        "icy_metadata_enabled": ICY_METADATA_ENABLED, "icy_metaint": ICY_METAINT,
        "stream_coalesce_bytes": STREAM_COALESCE_BYTES,
        "stream_coalesce_max_seconds": STREAM_COALESCE_MAX_SECONDS,
        "streams": stream_manager.status(),
    }


async def run_api_checks() -> dict[str, Any]:
    started = time.monotonic()
    source, ma, ticker, xm, ffmpeg = await asyncio.gather(
        _check_source_api(), _check_music_assistant(), _check_ticker(), _check_xmplaylist(), _check_ffmpeg()
    )
    checks = {"source": source, "music_assistant": ma, "ticker": ticker, "xmplaylist": xm, "ffmpeg": ffmpeg, "bridge": _check_bridge_local()}
    failed = [name for name, row in checks.items() if isinstance(row, dict) and row.get("status") == "fail"]
    return {"status": "ok" if not failed else "degraded", "checked_at": utc_now_iso(), "duration_ms": round((time.monotonic()-started)*1000,1), "failed": failed, "checks": checks}


@app.get("/")
async def index():
    source = {"mode": SOURCE_MODE}
    if SOURCE_MODE == "xc":
        source.update({"base_url": XC_BASE_URL, "output": XC_OUTPUT})
    else:
        source.update({"playlist": DISPATCHARR_M3U_URL or f"{DISPATCHARR_BASE_URL}{DISPATCHARR_M3U_PATH}"})
    return {
        "name": APP_NAME,
        "source": source,
        "public_base_url": PUBLIC_BASE_URL,
        "default_format": DEFAULT_FORMAT,
        "catalog": catalog.status(),
        "metadata": metadata_service.status(),
        "music_assistant_sync": ma_sync_service.status(),
        "endpoints": {
            "health": "/health",
            "channels": "/channels",
            "playlist_m3u8": "/playlist.m3u8",
            "playlist_m3u": "/playlist.m3u",
            "metadata": "/metadata/<channel-id>",
            "probe": "/probe/<channel-id>",
            "catalog_refresh": "/catalog/refresh",
            "music_assistant_sync": "/ma/sync",
            "music_assistant_status": "/ma/status",
            "stream_status": "/streams",
            "api_checks": "/api/checks",
            "api_checks_run": "/api/checks/run",
            "stream_check": "/api/checks/stream/<channel-id>",
            "mp3_stream": "/stream/<channel-id>.mp3",
            "aac_stream": "/stream/<channel-id>.aac",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "catalog": catalog.status(),
        "metadata": metadata_service.status(),
        "music_assistant_sync": ma_sync_service.status(),
        "playback": {
            "startup_silence_enabled": STARTUP_SILENCE_ENABLED,
            "icy_metadata_enabled": ICY_METADATA_ENABLED,
            "icy_metaint": ICY_METAINT,
            "startup_silence_max_seconds": STARTUP_SILENCE_MAX_SECONDS,
            "startup_real_audio_retries": STARTUP_REAL_AUDIO_RETRIES,
            "startup_real_audio_retry_delay": STARTUP_REAL_AUDIO_RETRY_DELAY,
            "source_rw_timeout_seconds": SOURCE_RW_TIMEOUT_SECONDS,
            "aac_mode_configured": AAC_MODE,
            "aac_playback_mode": AAC_MODE,
            "aac_bitrate": AAC_BITRATE,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "stream_linger_seconds": STREAM_LINGER_SECONDS,
            "subscriber_queue_chunks": STREAM_SUBSCRIBER_QUEUE_CHUNKS,
            "stream_read_chunk_bytes": STREAM_READ_CHUNK_BYTES,
            "stream_ring_buffer_seconds": STREAM_RING_BUFFER_SECONDS,
            "upstream_retry_initial_seconds": UPSTREAM_RETRY_INITIAL_SECONDS,
            "upstream_retry_max_seconds": UPSTREAM_RETRY_MAX_SECONDS,
            "upstream_retry_forever_while_listening": UPSTREAM_RETRY_FOREVER_WHILE_LISTENING,
            "real_audio_stall_seconds": REAL_AUDIO_STALL_SECONDS,
            "stream_restart_delay": STREAM_RESTART_DELAY,
            "upstream_max_connections": UPSTREAM_MAX_CONNECTIONS,
            "real_audio_stall_seconds_diagnostic_only": REAL_AUDIO_STALL_SECONDS,
            "ma_import_release_grace_seconds": MA_IMPORT_RELEASE_GRACE_SECONDS,
        },
        "streams": stream_manager.status(),
    }


@app.post("/catalog/refresh")
async def refresh_catalog():
    channels = await catalog.refresh(force=True)
    if MA_AUTO_SYNC and MA_SYNC_AFTER_CATALOG_REFRESH and not catalog.last_error:
        ma_sync_service.request_sync(channels, reason="manual-catalog-refresh")
    return {
        "status": "ok",
        "channels": len(channels),
        "catalog": catalog.status(),
        "music_assistant_sync": ma_sync_service.status(),
    }


@app.get("/ma/status")
async def ma_sync_status():
    return ma_sync_service.status()


@app.post("/ma/sync")
async def sync_music_assistant():
    channels = await catalog.refresh()
    if not MUSIC_ASSISTANT_TOKEN:
        raise HTTPException(status_code=400, detail="MUSIC_ASSISTANT_TOKEN is empty")
    try:
        result = await ma_sync_service.sync(channels, reason="manual-api")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "music_assistant_sync": result}


@app.get("/api/checks")
async def api_checks():
    return await run_api_checks()


@app.post("/api/checks/run")
async def api_checks_run():
    return await run_api_checks()


@app.get("/api/checks/stream/{channel_id}")
async def api_check_stream(channel_id: str, live: bool = Query(False)):
    channel = await catalog.get(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel is not present in the filtered source catalog")
    result: dict[str, Any] = {
        "status": "ok", "channel_id": channel.channel_id, "name": channel.name, "source_mode": SOURCE_MODE,
        "metadata": metadata_service.get(channel).public_dict() if metadata_service.get(channel) else None,
        "live_test_requested": live,
    }
    active = [row for row in stream_manager.status().get("hubs", []) if row.get("channel_id") == channel_id]
    result["active_hubs"] = active
    if not live:
        result["live_test"] = "not_run; add ?live=true to open/warm the source"
        return result
    started = time.monotonic()
    try:
        async with stream_manager.hold_warm(channel, IMPORT_FORMAT) as hub:
            result["startup_ms"] = round((time.monotonic()-started)*1000, 1)
            result["hub"] = hub.status()
            result["source_codec"] = await probe_audio_codec(channel)
    except Exception as exc:
        result["status"] = "fail"
        result["error"] = redact_secrets(exc)
    return result


@app.get("/streams")
async def stream_status():
    return stream_manager.status()


@app.get("/channels")
async def channels():
    parsed = await catalog.refresh()
    return JSONResponse([channel_json(item) for item in parsed])


@app.get("/probe/{channel_id}")
async def probe_channel(channel_id: str):
    channel = await catalog.get(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel is not present in the filtered source catalog")
    codec = await probe_audio_codec(channel, force=True)
    selected_mode = "transcode"
    if AAC_MODE == "copy" or (AAC_MODE == "auto" and codec == "aac"):
        selected_mode = "copy"
    return {
        "id": channel.channel_id,
        "name": channel.name,
        "source_codec": codec or "unknown",
        "aac_mode": AAC_MODE,
        "selected_aac_mode": selected_mode,
        "aac_bitrate": AAC_BITRATE,
        "startup_silence_enabled": STARTUP_SILENCE_ENABLED,
        "startup_silence_max_seconds": STARTUP_SILENCE_MAX_SECONDS,
    }


@app.get("/metadata/{channel_id}")
async def metadata(channel_id: str):
    channel = await catalog.get(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel is not present in the filtered source catalog")
    now_playing = metadata_service.get(channel)
    return {
        "channel": channel_json(channel),
        "metadata_status": metadata_service.status(),
        "now_playing": now_playing.public_dict() if now_playing else None,
    }


async def _playlist_response(fmt: str) -> PlainTextResponse:
    parsed = await catalog.refresh()
    lines = ["#EXTM3U", "#PLAYLIST:SiriusXM Radio"]
    for item in parsed:
        attrs = []
        if item.tvg_id:
            attrs.append(f'tvg-id="{item.tvg_id}"')
        attrs.append(f'tvg-name="{item.name}"')
        if item.channel_number:
            attrs.append(f'tvg-chno="{item.channel_number}"')
        if item.logo:
            attrs.append(f'tvg-logo="{item.logo}"')
        if item.group:
            attrs.append(f'group-title="{item.group}"')
        attr_text = " " + " ".join(attrs)
        lines.append(f"#EXTINF:-1{attr_text},{item.name}")
        lines.append(item.bridge_url(fmt))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="audio/x-mpegurl")


@app.get("/playlist.m3u")
async def playlist_m3u(fmt: str = Query(DEFAULT_FORMAT, pattern="^(mp3|aac)$")):
    return await _playlist_response(fmt)


@app.get("/playlist.m3u8")
async def playlist_m3u8(fmt: str = Query(DEFAULT_FORMAT, pattern="^(mp3|aac)$")):
    return await _playlist_response(fmt)


@app.get("/stream/{channel_id}.{fmt}")
async def stream(request: Request, channel_id: str, fmt: str):
    if not re.fullmatch(r"(?:[0-9a-fA-F-]{36}|xc-\d+)", channel_id):
        raise HTTPException(status_code=400, detail="Invalid channel id")
    fmt = fmt.lower()
    if fmt not in {"mp3", "aac"}:
        raise HTTPException(status_code=404, detail="Use .mp3 or .aac")

    channel = await catalog.get(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel is not present in the filtered source catalog")

    wants_icy = METADATA_ENABLED and ICY_METADATA_ENABLED and request.headers.get("icy-metadata", "0").strip() == "1"
    media_type = "audio/mpeg" if fmt == "mp3" else "audio/aac"
    headers = {
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
        "icy-name": channel.name,
        "icy-description": f"{channel.name} via {SOURCE_MODE.upper()}",
    }
    if channel.logo:
        headers["icy-logo"] = channel.logo
    # Subscribers share one persistent upstream per channel+format. A brief MA
    # disconnect/reconnect therefore does not tear down the Dispatcharr session.
    audio = stream_manager.subscribe(channel, fmt)
    if wants_icy:
        headers["icy-metaint"] = str(ICY_METAINT)
        body = with_icy_metadata(audio, channel)
    else:
        body = audio

    return StreamingResponse(body, media_type=media_type, headers=headers)

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
DEFAULT_FORMAT = os.getenv("DEFAULT_FORMAT", "aac").lower()
MP3_BITRATE = os.getenv("MP3_BITRATE", "192k")
AAC_BITRATE = os.getenv("AAC_BITRATE", "128k")
SAMPLE_RATE = os.getenv("SAMPLE_RATE", "48000")
CHANNELS = os.getenv("CHANNELS", "2")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
FFMPEG_LOG_LEVEL = os.getenv("FFMPEG_LOG_LEVEL", "warning")
DISPATCHARR_USER_AGENT = os.getenv("DISPATCHARR_USER_AGENT", "Dispatcharr-MA-Bridge/5.3")
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
                    async for chunk in startup_protected_audio_stream(
                        self.channel,
                        self.fmt,
                        real_audio_event=self.real_audio_ready,
                        diagnostic_callback=self.record_upstream_event,
                    ):
                        if self.real_audio_ready.is_set() and not self.last_real_audio_at:
                            self.last_real_audio_at = utc_now_iso()
                        await self._broadcast(chunk, real_audio=self.real_audio_ready.is_set())
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
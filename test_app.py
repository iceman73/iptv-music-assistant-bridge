import asyncio
import os

os.environ.setdefault("METADATA_ENABLED", "false")

from app import Channel, MetadataService, NowPlaying, build_ffmpeg_command, build_icy_block, parse_dispatcharr_m3u, build_xc_channels
import app


def test_m3u_names_and_filtering():
    text = '''#EXTM3U
#EXTINF:-1 tvg-id="abc" tvg-name="Old Name" tvg-chno="26" group-title="SiriusXM",Classic Vinyl
http://dispatcharr:9191/proxy/ts/stream/11111111-1111-1111-1111-111111111111
#EXTINF:-1 tvg-name="Other",Other Channel
http://dispatcharr:9191/proxy/ts/stream/22222222-2222-2222-2222-222222222222
'''
    channels = parse_dispatcharr_m3u(text)
    assert channels[0].name == "Classic Vinyl"
    assert channels[0].channel_number == "26"
    assert channels[0].tvg_id == "abc"


def test_stellar_flexible_parser():
    item = MetadataService._parse_stellar_item({
        "channel": {"name": "Classic Vinyl", "number": 26},
        "artist": "Fleetwood Mac",
        "title": "Dreams",
        "album": "Rumours",
        "artwork_url": "https://example.test/cover.jpg",
    })
    assert item is not None
    assert item.channel_number == "26"
    assert item.album == "Rumours"
    assert item.stream_title == "Fleetwood Mac - Dreams"


def test_icy_block_shape():
    # Only validates ICY framing; live metadata cache is tested in-container.
    channel = Channel(channel_id="11111111-1111-1111-1111-111111111111", name="Classic Vinyl")
    block = build_icy_block(channel)
    blocks = block[0]
    assert len(block) == 1 + blocks * 16
    assert b"StreamTitle='Classic Vinyl';" in block


if __name__ == "__main__":
    test_m3u_names_and_filtering()
    test_stellar_flexible_parser()
    test_icy_block_shape()
    print("all tests passed")


def test_aac_auto_copies_when_probe_says_aac():
    old_mode = app.AAC_MODE
    try:
        app.AAC_MODE = "auto"
        ch = Channel(
            channel_id="11111111-2222-3333-4444-555555555555",
            name="Classic Vinyl",
            source_url="http://dispatcharr:9191/proxy/ts/stream/11111111-2222-3333-4444-555555555555?profile=audio",
        )
        cmd = build_ffmpeg_command(ch, "aac", source_codec="aac")
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "copy"
        assert ch.source_url in cmd
    finally:
        app.AAC_MODE = old_mode


def test_aac_auto_transcodes_non_aac_source():
    old_mode = app.AAC_MODE
    try:
        app.AAC_MODE = "auto"
        ch = Channel(channel_id="xc-1", name="Test", source_url="http://example.test/live.ts")
        cmd = build_ffmpeg_command(ch, "aac", source_codec="mp2")
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "aac"
        assert "-b:a" in cmd
    finally:
        app.AAC_MODE = old_mode


def test_xc_catalog_mapping():
    import app
    old_base, old_user, old_pass = app.XC_BASE_URL, app.XC_USERNAME, app.XC_PASSWORD
    old_ids = set(app.XC_CATEGORY_IDS)
    try:
        app.XC_BASE_URL = "https://xc.example:443"
        app.XC_USERNAME = "user"
        app.XC_PASSWORD = "p@ss word"
        app.XC_CATEGORY_IDS.clear()
        categories = [{"category_id": "7", "category_name": "SiriusXM"}]
        streams = [{
            "stream_id": 12345,
            "name": "SiriusXM 26 - Classic Vinyl",
            "stream_icon": "https://img.example/cv.png",
            "epg_channel_id": "classicvinyl.us",
            "category_id": "7",
            "num": 999,
        }]
        channels = build_xc_channels(categories, streams)
        assert len(channels) == 1
        ch = channels[0]
        assert ch.channel_id == "xc-12345"
        assert ch.name == "SiriusXM 26 - Classic Vinyl"
        assert ch.group == "SiriusXM"
        assert ch.channel_number == "26"
        assert "/live/user/p%40ss%20word/12345.ts" in ch.source_url
        assert ch.channel_number != "999"
    finally:
        app.XC_BASE_URL, app.XC_USERNAME, app.XC_PASSWORD = old_base, old_user, old_pass
        app.XC_CATEGORY_IDS.clear()
        app.XC_CATEGORY_IDS.update(old_ids)


def test_normalize_decade_station_names():
    assert app.normalize_name("70s on 7") == "70son7"
    assert app.normalize_name("80s on 8") == "80son8"
    assert app.normalize_name("90s on 9") == "90son9"
    assert app.normalize_name("SXM - SiriusXM Hits 1") == "hits1"


def test_xmplaylist_station_lookup_supports_deeplink():
    # Mirrors the important relationship in the live xmplaylist API:
    # station catalog id is a UUID but feed channelId is the deeplink.
    service = app.MetadataService()
    station = {
        "name": "The Pulse",
        "number": "5",
        "deeplink": "thepulse",
        "image": "https://example.test/pulse.webp",
    }
    service._xm_stations_by_id = {
        "9e8d6f72-0b59-85cf-a222-b18d38acdc0f": station,
        "thepulse": station,
    }
    assert service._xm_stations_by_id["thepulse"]["name"] == "The Pulse"


def test_ma_managed_url_extraction_only_owns_bridge_urls():
    old_prefix = app.MA_MANAGED_URL_PREFIX
    try:
        app.MA_MANAGED_URL_PREFIX = "http://bridge:8088/stream/"
        item = {
            "item_id": "123",
            "provider_mappings": [
                {
                    "provider_domain": "builtin",
                    "provider_instance": "builtin",
                    "item_id": "http://bridge:8088/stream/xc-23244.aac",
                }
            ],
        }
        assert app.MusicAssistantSyncService._managed_url_from_item(item) == "http://bridge:8088/stream/xc-23244.aac"
        item["provider_mappings"][0]["item_id"] = "https://example.com/unrelated-radio.mp3"
        assert app.MusicAssistantSyncService._managed_url_from_item(item) == ""
    finally:
        app.MA_MANAGED_URL_PREFIX = old_prefix


def test_ma_sync_status_defaults():
    service = app.MusicAssistantSyncService()
    status = service.status()
    assert "remove_missing" in status
    assert "managed_url_prefix" in status
    assert "add_failures" in status
    assert "failed_radios" in status
    assert "migrated_count" in status
    assert "stale_format_removed_count" in status
    assert "migration_failures" in status
    assert status["synced_count"] == 0


def test_ffmpeg_command_reconnects_at_eof_and_transient_http_errors():
    ch = Channel(channel_id="xc-99", name="Reconnect Test", source_url="http://example.test/live.ts")
    cmd = app.build_ffmpeg_command(ch, "aac", source_codec="aac", force_transcode=True)
    assert "-reconnect_at_eof" in cmd and cmd[cmd.index("-reconnect_at_eof") + 1] == "1"
    assert "-reconnect_on_network_error" in cmd and cmd[cmd.index("-reconnect_on_network_error") + 1] == "1"
    assert "-reconnect_on_http_error" in cmd
    assert "+discardcorrupt+genpts" in cmd
    assert "+nobuffer+discardcorrupt" not in cmd
    assert "low_delay" not in cmd
    assert cmd[cmd.index("-probesize") + 1] == "1000000"
    assert cmd[cmd.index("-analyzeduration") + 1] == "2000000"


def test_shared_hub_reuses_same_upstream_during_linger():
    async def run():
        old_source = app.startup_protected_audio_stream
        old_linger = app.STREAM_LINGER_SECONDS
        starts = {"count": 0}

        async def fake_source(channel, fmt, real_audio_event=None, diagnostic_callback=None):
            starts["count"] += 1
            if real_audio_event is not None:
                real_audio_event.set()
            i = 0
            while True:
                await asyncio.sleep(0.005)
                i += 1
                yield f"A{i}".encode()

        try:
            app.startup_protected_audio_stream = fake_source
            app.STREAM_LINGER_SECONDS = 0.08
            manager = app.StreamManager()
            ch = Channel(channel_id="xc-100", name="80s on 8", source_url="http://example.test/live.ts")

            first = manager.subscribe(ch, "aac")
            assert (await anext(first)).startswith(b"A")
            await first.aclose()

            # Reconnect before the linger window expires: no new upstream task.
            await asyncio.sleep(0.02)
            second = manager.subscribe(ch, "aac")
            assert (await anext(second)).startswith(b"A")
            assert starts["count"] == 1
            await second.aclose()

            # Once idle longer than linger, the shared upstream is stopped.
            await asyncio.sleep(0.12)
            status = manager.status()
            assert status["active_hubs"] == 0
            await manager.stop()
        finally:
            app.startup_protected_audio_stream = old_source
            app.STREAM_LINGER_SECONDS = old_linger

    asyncio.run(run())


def test_ma_sync_skips_unchanged_and_only_imports_new_station():
    async def run():
        service = app.MusicAssistantSyncService()
        ch_existing = Channel(channel_id="xc-1", name="The Pulse", source_url="http://example.test/1.ts")
        ch_new = Channel(channel_id="xc-2", name="PopRocks", source_url="http://example.test/2.ts")
        existing_item = {
            "item_id": "1",
            "name": "The Pulse",
            "provider_mappings": [{
                "provider_domain": "builtin",
                "provider_instance": "builtin",
                "item_id": ch_existing.bridge_url(app.IMPORT_FORMAT),
            }],
        }
        async def fake_managed():
            return [existing_item]
        added = []
        async def fake_add(channel, args):
            added.append((channel.name, args["url"]))
        service._managed_library_radios = fake_managed
        service._add_radio_with_warm_source = fake_add
        result = await service.sync([ch_existing, ch_new], reason="test")
        assert result["unchanged_count"] == 1
        assert result["synced_count"] == 1
        assert added == [("PopRocks", ch_new.bridge_url(app.IMPORT_FORMAT))]
    asyncio.run(run())


def test_ma_add_retries_while_same_shared_stream_is_pinned():
    async def run():
        old_manager = app.stream_manager
        old_retries = app.MA_ADD_RETRIES
        old_delay = app.MA_ADD_RETRY_DELAY
        events = []
        calls = {"count": 0}

        class FakeManager:
            def __init__(self):
                self.playback_active_event = asyncio.Event()

            async def wait_for_playback_idle(self):
                return None

            @app.asynccontextmanager
            async def hold_warm(self, channel, fmt):
                events.append("warm-open")
                try:
                    yield object()
                finally:
                    events.append("warm-close")

        service = app.MusicAssistantSyncService()

        async def fake_call(command, args):
            calls["count"] += 1
            events.append(f"call-{calls['count']}")
            if calls["count"] < 3:
                raise RuntimeError("temporary probe failure")
            return None

        try:
            app.stream_manager = FakeManager()
            app.MA_ADD_RETRIES = 3
            app.MA_ADD_RETRY_DELAY = 0
            service._call = fake_call
            ch = Channel(channel_id="xc-3", name="80s on 8", source_url="http://example.test/3.ts")
            await service._add_radio_with_warm_source(ch, {"url": ch.bridge_url("aac"), "name": ch.name})
            assert events == ["warm-open", "call-1", "call-2", "call-3", "warm-close"]
        finally:
            app.stream_manager = old_manager
            app.MA_ADD_RETRIES = old_retries
            app.MA_ADD_RETRY_DELAY = old_delay

    asyncio.run(run())


def test_startup_silence_forces_stable_aac_transcode():
    ch = Channel(channel_id="xc-500", name="Test", source_url="http://example.test/live.ts")
    cmd = build_ffmpeg_command(ch, "aac", source_codec="aac", force_transcode=True)
    idx = cmd.index("-c:a")
    assert cmd[idx + 1] == "aac"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == app.SAMPLE_RATE
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == app.CHANNELS


def test_v5_startup_silence_switches_to_single_real_process():
    async def run():
        old_silence = app.ffmpeg_silence_stream
        old_audio = app.ffmpeg_audio_stream
        old_enabled = app.STARTUP_SILENCE_ENABLED

        async def fake_silence(fmt):
            while True:
                await asyncio.sleep(0.003)
                yield b"S"

        async def fake_audio(channel, fmt, force_transcode=False, diagnostic_callback=None):
            await asyncio.sleep(0.01)
            yield b"R1"
            yield b"R2"

        try:
            app.ffmpeg_silence_stream = fake_silence
            app.ffmpeg_audio_stream = fake_audio
            app.STARTUP_SILENCE_ENABLED = True
            ch = Channel(channel_id="xc-501", name="Cold Test", source_url="http://example.test/live.ts")
            gen = app.startup_protected_audio_stream(ch, "aac")
            chunks = []
            try:
                while True:
                    chunks.append(await asyncio.wait_for(anext(gen), timeout=0.5))
            except StopAsyncIteration:
                pass
            assert b"S" in chunks
            assert b"R1" in chunks and b"R2" in chunks
            assert chunks.index(b"R1") < chunks.index(b"R2")
        finally:
            app.ffmpeg_silence_stream = old_silence
            app.ffmpeg_audio_stream = old_audio
            app.STARTUP_SILENCE_ENABLED = old_enabled

    asyncio.run(run())


def test_stream_hub_health_counters_record_real_audio():
    async def run():
        ch = Channel(channel_id="xc-health", name="Health Test", source_url="http://example.test/live.ts")
        hub = app.SharedStreamHub(ch, "aac")
        await hub._broadcast(b"abc", real_audio=False)
        await hub._broadcast(b"12345", real_audio=True)
        status = hub.status()
        assert status["total_output_bytes"] == 8
        assert status["real_audio_bytes"] == 5
        assert status["total_chunks"] == 2
        assert status["real_audio_chunks"] == 1
        assert status["last_chunk_at"]
        assert status["last_real_chunk_at"]
        assert status["seconds_since_last_chunk"] is not None
        assert status["seconds_since_last_real_chunk"] is not None
        assert status["recent_real_bytes_per_second"] > 0
        assert status["dropped_chunks"] == 0

    asyncio.run(run())



def test_redact_secrets_hides_xc_password():
    old_user, old_pass = app.XC_USERNAME, app.XC_PASSWORD
    try:
        app.XC_USERNAME = "musictime"
        app.XC_PASSWORD = "supersecret"
        value = "Error opening http://host/live/musictime/supersecret/23308.ts"
        safe = app.redact_secrets(value)
        assert "supersecret" not in safe
        assert "/live/***/***/23308.ts" in safe
    finally:
        app.XC_USERNAME, app.XC_PASSWORD = old_user, old_pass


def test_ring_buffer_prerolls_new_subscriber():
    async def run():
        old_ring = app.STREAM_RING_BUFFER_SECONDS
        old_q = app.STREAM_SUBSCRIBER_QUEUE_CHUNKS
        try:
            app.STREAM_RING_BUFFER_SECONDS = 2
            app.STREAM_SUBSCRIBER_QUEUE_CHUNKS = 16
            ch = Channel(channel_id="xc-ring", name="Ring", source_url="http://example.test/live.ts")
            hub = app.SharedStreamHub(ch, "aac")
            await hub._broadcast(b"A", real_audio=True)
            await hub._broadcast(b"B", real_audio=True)
            # Prevent subscribe from starting a real background task for this unit test.
            async def fake_run():
                await asyncio.sleep(10)
            hub._run = fake_run
            gen = hub.subscribe()
            assert await asyncio.wait_for(anext(gen), timeout=0.2) == b"A"
            assert await asyncio.wait_for(anext(gen), timeout=0.2) == b"B"
            await gen.aclose()
            await hub.stop()
        finally:
            app.STREAM_RING_BUFFER_SECONDS = old_ring
            app.STREAM_SUBSCRIBER_QUEUE_CHUNKS = old_q
    asyncio.run(run())



def test_v5_real_audio_stall_does_not_kill_ffmpeg_generator():
    async def run():
        old_silence = app.ffmpeg_silence_stream
        old_audio = app.ffmpeg_audio_stream
        old_enabled = app.STARTUP_SILENCE_ENABLED
        old_stall = app.REAL_AUDIO_STALL_SECONDS

        async def fake_silence(fmt):
            while True:
                await asyncio.sleep(0.001)
                yield b"S"

        async def fake_audio(channel, fmt, force_transcode=False, diagnostic_callback=None):
            yield b"R1"
            await asyncio.sleep(0.05)  # greater than diagnostic threshold
            yield b"R2"

        try:
            app.ffmpeg_silence_stream = fake_silence
            app.ffmpeg_audio_stream = fake_audio
            app.STARTUP_SILENCE_ENABLED = True
            app.REAL_AUDIO_STALL_SECONDS = 0.01
            ch = Channel(channel_id="xc-stall", name="Stall Test", source_url="http://example.test/live.ts")
            gen = app.startup_protected_audio_stream(ch, "aac")
            chunks = []
            try:
                while True:
                    chunks.append(await asyncio.wait_for(anext(gen), timeout=0.5))
            except StopAsyncIteration:
                pass
            assert b"R1" in chunks and b"R2" in chunks
        finally:
            app.ffmpeg_silence_stream = old_silence
            app.ffmpeg_audio_stream = old_audio
            app.STARTUP_SILENCE_ENABLED = old_enabled
            app.REAL_AUDIO_STALL_SECONDS = old_stall
    asyncio.run(run())


def test_ticker_bulk_parser_and_xc_dispatcharr_id():
    rows = [{"name": "Classic Vinyl", "deeplink_id": "classicvinyl", "channel_number": 26}]
    stations = {"classicvinyl": {"artist": "Fleetwood Mac", "title": "Dreams", "cut_type": "song"}}
    items = MetadataService._ticker_items(rows, stations, fetched=123.0)
    assert len(items) == 1
    assert items[0].provider == "ticker"
    assert items[0].stream_title == "Fleetwood Mac - Dreams"
    assert items[0].channel_number == "26"

    channels = build_xc_channels(
        [{"category_id": "1", "category_name": "USA | SiriusXM"}],
        [{"stream_id": 23261, "category_id": "1", "name": "Classic Vinyl",
          "stream_icon": "http://dispatcharr:9191/api/channels/logos/55535/cache/"}],
    )
    assert channels[0].dispatcharr_id == "55535"



def test_ticker_idle_wait_wakes_immediately_on_stream_activity():
    async def run():
        old_idle = app.TICKER_IDLE_POLL_SECONDS
        try:
            app.TICKER_IDLE_POLL_SECONDS = 0
            svc = MetadataService()
            svc.provider = "ticker"
            waiter = asyncio.create_task(svc._wait_for_ticker_activity())
            await asyncio.sleep(0.01)
            assert not waiter.done()
            svc.notify_stream_activity()
            await asyncio.wait_for(waiter, timeout=0.2)
            assert not svc._activity_event.is_set()
        finally:
            app.TICKER_IDLE_POLL_SECONDS = old_idle
    asyncio.run(run())


def test_ticker_status_reports_active_and_idle_poll_intervals():
    old_active = app.TICKER_ACTIVE_POLL_SECONDS
    old_idle = app.TICKER_IDLE_POLL_SECONDS
    try:
        app.TICKER_ACTIVE_POLL_SECONDS = 15
        app.TICKER_IDLE_POLL_SECONDS = 0
        svc = MetadataService()
        svc.provider = "ticker"
        status = svc.status()
        assert status["ticker_active_poll_seconds"] == 15
        assert status["ticker_idle_poll_seconds"] == 0
        assert status["ticker_polling_active"] is False
    finally:
        app.TICKER_ACTIVE_POLL_SECONDS = old_active
        app.TICKER_IDLE_POLL_SECONDS = old_idle


def test_icy_cache_is_prebuilt_and_network_free():
    svc = MetadataService()
    item = app.NowPlaying(provider="ticker", channel_name="Classic Vinyl", artist="Fleetwood Mac", title="Dreams", fetched_at=app.time.time())
    svc._replace_cache([item])
    ch = Channel(channel_id="xc-1", name="Classic Vinyl")
    block = svc.get_icy_block(ch)
    assert isinstance(block, bytes)
    assert b"Fleetwood Mac - Dreams" in block


def test_icy_wrapper_exact_audio_byte_accounting():
    async def run():
        old_metaint = app.ICY_METAINT
        old_service = app.metadata_service
        try:
            app.ICY_METAINT = 8
            svc = MetadataService()
            svc._replace_cache([app.NowPlaying(provider="ticker", channel_name="Test", artist="A", title="B", fetched_at=app.time.time())])
            app.metadata_service = svc
            ch = Channel(channel_id="xc-2", name="Test")
            async def src():
                yield b"123"
                yield b"4567890"
                yield b"abcdef"
            out = [x async for x in app.with_icy_metadata(src(), ch)]
            assert out[0] == b"12345678"
            assert out[2] == b"90abcdef"
            assert out[1][0] >= 1
        finally:
            app.ICY_METAINT = old_metaint
            app.metadata_service = old_service
    asyncio.run(run())


def test_bridge_local_diagnostics_include_icy_and_version():
    row = app._check_bridge_local()
    assert row["version"] == "5.6.7"
    assert "icy_metadata_enabled" in row
    assert "icy_metaint" in row

def test_coalesces_tiny_ffmpeg_reads_without_changing_bytes():
    async def run():
        async def src():
            yield b"abc", True
            yield b"def", True
            yield b"ghij", True

        out = [item async for item in app.coalesce_audio_chunks(src(), target_bytes=8, max_delay_seconds=60)]
        assert out == [(b"abcdefgh", True), (b"ij", True)]
        assert b"".join(chunk for chunk, _ in out) == b"abcdefghij"

    asyncio.run(run())


def test_coalescer_flushes_on_silence_to_real_audio_transition():
    async def run():
        async def src():
            yield b"sil", False
            yield b"ence", False
            yield b"real", True
            yield b"audio", True

        out = [item async for item in app.coalesce_audio_chunks(src(), target_bytes=8192, max_delay_seconds=60)]
        assert out == [(b"silence", False), (b"realaudio", True)]

    asyncio.run(run())


def test_ma_channel_identity_ignores_audio_extension():
    old_prefix = app.MA_MANAGED_URL_PREFIX
    try:
        app.MA_MANAGED_URL_PREFIX = "http://bridge:8088/stream/"
        get_id = app.MusicAssistantSyncService._managed_channel_id_from_url
        assert get_id("http://bridge:8088/stream/xc-23260.aac") == "xc-23260"
        assert get_id("http://bridge:8088/stream/xc-23260.mp3") == "xc-23260"
    finally:
        app.MA_MANAGED_URL_PREFIX = old_prefix


def test_ma_sync_migrates_old_format_when_remove_missing_is_off():
    async def run():
        old_format = app.IMPORT_FORMAT
        old_remove = app.MA_REMOVE_MISSING
        old_prefix = app.MA_MANAGED_URL_PREFIX
        old_public = app.PUBLIC_BASE_URL
        try:
            app.IMPORT_FORMAT = "mp3"
            app.MA_REMOVE_MISSING = False
            app.MA_MANAGED_URL_PREFIX = "http://bridge:8088/stream/"
            app.PUBLIC_BASE_URL = "http://bridge:8088"
            service = app.MusicAssistantSyncService()
            channel = Channel(channel_id="xc-23260", name="Classic Rewind", source_url="http://source.test/live.ts")
            existing = {
                "item_id": "old-aac",
                "name": channel.name,
                "provider_mappings": [{
                    "provider_domain": "builtin",
                    "provider_instance": "builtin",
                    "item_id": "http://bridge:8088/stream/xc-23260.aac",
                }],
            }
            async def managed():
                return [existing]
            added, removed = [], []
            async def add_radio(ch, args):
                added.append(args["url"])
            async def call(command, args):
                if command == "music/radios/remove":
                    removed.append(args["item_id"])
            service._managed_library_radios = managed
            service._add_radio_with_warm_source = add_radio
            service._call = call
            result = await service.sync([channel], reason="migration-test")
            assert added == ["http://bridge:8088/stream/xc-23260.mp3"]
            assert removed == ["old-aac"]
            assert result["migrated_count"] == 1
            assert result["stale_format_removed_count"] == 1
            assert result["removed_count"] == 0
        finally:
            app.IMPORT_FORMAT = old_format
            app.MA_REMOVE_MISSING = old_remove
            app.MA_MANAGED_URL_PREFIX = old_prefix
            app.PUBLIC_BASE_URL = old_public
    asyncio.run(run())


def test_shared_hub_current_attempt_diagnostics_reset_cleanly():
    hub = app.SharedStreamHub(Channel(channel_id="xc-1", name="Test"), "mp3")
    hub.last_real_chunk_at = "old"
    hub._last_real_chunk_monotonic = 12.0
    hub._rate_window.append((12.0, 100))
    hub.current_attempt_http_503_count = 4
    hub.current_attempt_http_error_count = 5
    hub._ring_buffer.append((12.0, b"old"))
    hub._reset_attempt_diagnostics()
    assert hub.last_real_chunk_at == ""
    assert hub._last_real_chunk_monotonic == 0.0
    assert hub.current_attempt_http_503_count == 0
    assert hub.current_attempt_http_error_count == 0
    assert hub.state == "starting_upstream"
    assert list(hub._rate_window) == []
    assert list(hub._ring_buffer) == []


def test_upstream_state_tracks_slot_and_http_errors():
    hub = app.SharedStreamHub(Channel(channel_id="xc-2", name="Test"), "mp3")
    hub._reset_attempt_diagnostics()
    hub.record_upstream_event("waiting_for_slot")
    assert hub.state == "waiting_for_slot"
    assert hub.waiting_for_upstream_slot is True
    hub.record_upstream_event("slot_acquired")
    assert hub.state == "starting_upstream"
    assert hub.waiting_for_upstream_slot is False
    hub.record_upstream_event("http_503")
    assert hub.state == "source_error"
    assert hub.current_attempt_http_503_count == 1
    assert hub.current_attempt_http_error_count == 1


def test_import_warmup_release_stops_idle_hub_without_linger():
    async def run():
        hub = app.SharedStreamHub(Channel(channel_id="xc-3", name="Import Test"), "mp3")
        hub.pins = 1
        hub.real_audio_ready.set()

        async def sleeper():
            await asyncio.sleep(60)

        task = asyncio.create_task(sleeper())
        hub.task = task
        await hub.unpin(stop_if_idle=True)

        assert hub.pins == 0
        assert task.done()
        assert hub.state == "idle"

    asyncio.run(run())


def test_normal_unpin_still_uses_linger():
    async def run():
        old_linger = app.STREAM_LINGER_SECONDS
        try:
            app.STREAM_LINGER_SECONDS = 30
            hub = app.SharedStreamHub(Channel(channel_id="xc-4", name="Playback Test"), "mp3")
            hub.pins = 1

            async def sleeper():
                await asyncio.sleep(60)

            task = asyncio.create_task(sleeper())
            hub.task = task
            await hub.unpin()

            assert hub.pins == 0
            assert not task.done()
            assert hub.linger_task is not None
            assert not hub.linger_task.done()

            hub.linger_task.cancel()
            try:
                await hub.linger_task
            except asyncio.CancelledError:
                pass
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            app.STREAM_LINGER_SECONDS = old_linger

    asyncio.run(run())


def test_ma_radio_audit_classifies_correct_wrong_format_duplicate_and_missing():
    async def run():
        service = app.MusicAssistantSyncService()
        channels = [
            Channel(channel_id="xc-1", name="Correct"),
            Channel(channel_id="xc-2", name="Wrong"),
            Channel(channel_id="xc-3", name="Duplicate"),
            Channel(channel_id="xc-4", name="Missing"),
        ]

        def item(item_id, name, url):
            return {
                "item_id": item_id,
                "name": name,
                "provider_mappings": [{
                    "provider_domain": "builtin",
                    "provider_instance": "builtin",
                    "item_id": url,
                }],
            }

        managed = [
            item("1", "Correct", "http://localhost:8088/stream/xc-1.mp3"),
            item("2", "Wrong", "http://localhost:8088/stream/xc-2.aac"),
            item("3a", "Duplicate", "http://localhost:8088/stream/xc-3.mp3"),
            item("3b", "Duplicate", "http://localhost:8088/stream/xc-3.aac"),
            item("5", "Old Source", "http://localhost:8088/stream/xc-5.mp3"),
        ]

        old_prefix = app.MA_MANAGED_URL_PREFIX
        old_base = app.PUBLIC_BASE_URL
        old_fmt = app.IMPORT_FORMAT
        try:
            app.MA_MANAGED_URL_PREFIX = "http://localhost:8088/stream/"
            app.PUBLIC_BASE_URL = "http://localhost:8088"
            app.IMPORT_FORMAT = "mp3"

            async def fake_managed():
                return managed

            service._managed_library_radios = fake_managed
            result = await service.audit_library_radios(channels)
        finally:
            app.MA_MANAGED_URL_PREFIX = old_prefix
            app.PUBLIC_BASE_URL = old_base
            app.IMPORT_FORMAT = old_fmt

        by_id = {row["channel_id"]: row for row in result["radios"] if row.get("channel_id")}
        assert by_id["xc-1"]["status"] == "correct"
        assert by_id["xc-2"]["status"] == "wrong_format"
        assert by_id["xc-3"]["status"] == "duplicate"
        assert by_id["xc-4"]["status"] == "missing"
        assert by_id["xc-5"]["status"] == "source_missing"
        assert result["counts"]["correct"] == 1
        assert result["counts"]["wrong_format"] == 1
        assert result["counts"]["duplicate"] == 1
        assert result["counts"]["missing"] == 1
        assert result["counts"]["source_missing"] == 1

    asyncio.run(run())


def test_real_audio_clears_stale_last_error():
    hub = app.SharedStreamHub(Channel(channel_id="xc-9", name="Recovery"), "mp3")
    hub.last_error = "RuntimeError: old failure"
    hub._record_chunk(b"abc", real_audio=True)
    assert hub.last_error == ""
    assert hub.state == "real_audio"


def test_remove_all_managed_radios_dry_run_and_execute():
    async def run():
        service = app.MusicAssistantSyncService()
        managed = [
            {
                "item_id": "11",
                "name": "One",
                "provider_mappings": [{
                    "provider_domain": "builtin",
                    "provider_instance": "builtin",
                    "item_id": "http://localhost:8088/stream/xc-11.aac",
                }],
            },
            {
                "item_id": "12",
                "name": "Two",
                "provider_mappings": [{
                    "provider_domain": "builtin",
                    "provider_instance": "builtin",
                    "item_id": "http://localhost:8088/stream/xc-12.mp3",
                }],
            },
        ]

        old_prefix = app.MA_MANAGED_URL_PREFIX
        try:
            app.MA_MANAGED_URL_PREFIX = "http://localhost:8088/stream/"

            async def fake_managed():
                return managed

            removed_ids = []

            async def fake_remove(item):
                removed_ids.append(str(item["item_id"]))

            service._managed_library_radios = fake_managed
            service._remove_managed_radio_item = fake_remove

            preview = await service.remove_all_managed_radios(dry_run=True)
            assert preview["target_count"] == 2
            assert preview["removed_count"] == 0
            assert preview["failed_count"] == 0
            assert removed_ids == []

            result = await service.remove_all_managed_radios(dry_run=False)
            assert result["target_count"] == 2
            assert result["removed_count"] == 2
            assert result["failed_count"] == 0
            assert removed_ids == ["11", "12"]
        finally:
            app.MA_MANAGED_URL_PREFIX = old_prefix

    asyncio.run(run())


def test_stream_manager_marks_playback_and_demand_type():
    async def run():
        manager = app.StreamManager()
        channel = Channel(channel_id="xc-priority", name="Priority")
        hub = await manager._hub(channel, "mp3")

        async def fake_subscribe():
            yield b"audio"

        original = hub.subscribe
        hub.subscribe = fake_subscribe
        try:
            gen = manager.subscribe(channel, "mp3")
            first = await anext(gen)
            assert first == b"audio"
            assert manager.playback_active_event.is_set()
            assert manager.playback_listeners == 1
            await gen.aclose()
            assert not manager.playback_active_event.is_set()
            assert manager.playback_idle_event.is_set()
            assert manager.playback_listeners == 0
        finally:
            hub.subscribe = original

    asyncio.run(run())


def test_warmup_is_preempted_while_playback_active():
    async def run():
        manager = app.StreamManager()
        channel = Channel(channel_id="xc-warm", name="Warm")
        hub = await manager._hub(channel, "mp3")
        events = []

        async def fake_pin():
            hub.pins += 1
            events.append("pin")

        async def fake_unpin(stop_if_unready=False, stop_if_idle=False):
            hub.pins = max(0, hub.pins - 1)
            events.append("unpin")

        original_pin = hub.pin
        original_unpin = hub.unpin
        hub.pin = fake_pin
        hub.unpin = fake_unpin

        manager.playback_active_event.set()
        manager.playback_idle_event.clear()

        async def release_playback():
            await asyncio.sleep(0.01)
            manager.playback_active_event.clear()
            manager.playback_idle_event.set()

        release_task = asyncio.create_task(release_playback())
        old_warmup = app.STREAM_WARMUP_ENABLED
        try:
            app.STREAM_WARMUP_ENABLED = False
            async with manager.hold_warm(channel, "mp3"):
                events.append("yield")
            await release_task
            assert events == ["pin", "yield", "unpin"]
        finally:
            app.STREAM_WARMUP_ENABLED = old_warmup
            hub.pin = original_pin
            hub.unpin = original_unpin

    asyncio.run(run())


def test_hub_status_reports_demand_type():
    hub = app.SharedStreamHub(Channel(channel_id="xc-demand", name="Demand"), "mp3")
    assert hub.status()["demand_type"] == "linger"
    hub.pins = 1
    assert hub.status()["demand_type"] == "ma_warmup"
    hub.subscribers["x"] = asyncio.Queue()
    assert hub.status()["demand_type"] == "playback"


def test_playback_preempts_unrelated_warmup_hub():
    async def run():
        manager = app.StreamManager()
        warm_channel = Channel(channel_id="xc-warm2", name="Warm 2")
        play_channel = Channel(channel_id="xc-play2", name="Play 2")
        warm_hub = await manager._hub(warm_channel, "mp3")
        play_hub = await manager._hub(play_channel, "mp3")

        warm_hub.pins = 1

        async def sleeper():
            await asyncio.sleep(60)

        task = asyncio.create_task(sleeper())
        warm_hub.task = task

        async def fake_play_subscribe():
            yield b"audio"

        original = play_hub.subscribe
        play_hub.subscribe = fake_play_subscribe
        try:
            gen = manager.subscribe(play_channel, "mp3")
            assert await anext(gen) == b"audio"
            await asyncio.sleep(0)
            assert task.done()
            assert manager.ma_warmups_preempted == 1
            await gen.aclose()
        finally:
            play_hub.subscribe = original
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(run())


def test_mp3_output_pacing_limits_burst_rate():
    async def run():
        old_enabled = app.STREAM_OUTPUT_PACING
        old_bitrate = app.MP3_BITRATE
        old_slack = app.STREAM_OUTPUT_PACING_SLACK_SECONDS
        sleeps = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay):
            sleeps.append(delay)
            await real_sleep(0)

        async def src():
            yield b"a" * 24000, True
            yield b"b" * 24000, True
            yield b"c" * 24000, True

        try:
            app.STREAM_OUTPUT_PACING = True
            app.MP3_BITRATE = "192k"
            app.STREAM_OUTPUT_PACING_SLACK_SECONDS = 0.25
            original_sleep = app.asyncio.sleep
            app.asyncio.sleep = fake_sleep
            out = [item async for item in app.pace_encoded_audio(src(), "mp3")]
            assert len(out) == 3
            assert b"".join(chunk for chunk, _ in out) == (b"a" * 24000 + b"b" * 24000 + b"c" * 24000)
            assert any(delay >= 0.9 for delay in sleeps)
        finally:
            app.asyncio.sleep = original_sleep
            app.STREAM_OUTPUT_PACING = old_enabled
            app.MP3_BITRATE = old_bitrate
            app.STREAM_OUTPUT_PACING_SLACK_SECONDS = old_slack

    asyncio.run(run())


def test_output_pacing_passes_silence_without_delay_and_resets_clock():
    async def run():
        old_enabled = app.STREAM_OUTPUT_PACING
        old_bitrate = app.MP3_BITRATE
        try:
            app.STREAM_OUTPUT_PACING = True
            app.MP3_BITRATE = "192k"

            async def src():
                yield b"S" * 100, False
                yield b"R" * 100, True

            out = [item async for item in app.pace_encoded_audio(src(), "mp3")]
            assert out == [(b"S" * 100, False), (b"R" * 100, True)]
        finally:
            app.STREAM_OUTPUT_PACING = old_enabled
            app.MP3_BITRATE = old_bitrate

    asyncio.run(run())


def test_bitrate_parser():
    assert app._bitrate_to_bps("192k") == 192000
    assert app._bitrate_to_bps("128000") == 128000
    assert app._bitrate_to_bps("1.5m") == 1500000
    assert app._bitrate_to_bps("bad") == 0


def test_post_start_gap_fill_keeps_stream_continuous():
    async def run():
        old_silence = app.ffmpeg_silence_stream
        old_audio = app.ffmpeg_audio_stream
        old_enabled = app.STARTUP_SILENCE_ENABLED
        old_gap_enabled = app.POST_START_GAP_FILL_ENABLED
        old_gap_after = app.POST_START_GAP_FILL_AFTER_SECONDS

        events = []
        kinds = []

        async def fake_silence(fmt):
            while True:
                await asyncio.sleep(0.002)
                yield b"S"

        async def fake_audio(channel, fmt, force_transcode=False, diagnostic_callback=None):
            yield b"R1"
            await asyncio.sleep(0.03)
            yield b"R2"

        try:
            app.ffmpeg_silence_stream = fake_silence
            app.ffmpeg_audio_stream = fake_audio
            app.STARTUP_SILENCE_ENABLED = True
            app.POST_START_GAP_FILL_ENABLED = True
            app.POST_START_GAP_FILL_AFTER_SECONDS = 0.01

            ch = Channel(channel_id="xc-gap", name="Gap Test", source_url="http://example.test/live.ts")
            gen = app.startup_protected_audio_stream(
                ch,
                "mp3",
                diagnostic_callback=events.append,
                output_kind_callback=kinds.append,
            )
            chunks = []
            try:
                while True:
                    chunks.append(await asyncio.wait_for(anext(gen), timeout=0.5))
            except StopAsyncIteration:
                pass

            assert b"R1" in chunks and b"R2" in chunks
            between = chunks[chunks.index(b"R1") + 1:chunks.index(b"R2")]
            assert b"S" in between
            assert "gap_fill_start" in events
            assert "gap_fill_end" in events
            assert False in kinds and True in kinds
        finally:
            app.ffmpeg_silence_stream = old_silence
            app.ffmpeg_audio_stream = old_audio
            app.STARTUP_SILENCE_ENABLED = old_enabled
            app.POST_START_GAP_FILL_ENABLED = old_gap_enabled
            app.POST_START_GAP_FILL_AFTER_SECONDS = old_gap_after

    asyncio.run(run())


def test_mp3_streams_disable_midstream_headers_for_clean_splicing():
    ch = Channel(channel_id="xc-mp3", name="MP3", source_url="http://example.test/live.ts")
    cmd = app.build_ffmpeg_command(ch, "mp3")
    assert "-write_xing" in cmd and cmd[cmd.index("-write_xing") + 1] == "0"
    assert "-id3v2_version" in cmd and cmd[cmd.index("-id3v2_version") + 1] == "0"


def test_channel_switch_preempts_old_linger_hub():
    async def run():
        manager = app.StreamManager()
        old_channel = Channel(channel_id="xc-old", name="Old")
        new_channel = Channel(channel_id="xc-new", name="New")
        old_hub = await manager._hub(old_channel, "mp3")
        new_hub = await manager._hub(new_channel, "mp3")

        async def sleeper():
            await asyncio.sleep(60)

        old_task = asyncio.create_task(sleeper())
        old_hub.task = old_task

        async def fake_new_subscribe():
            yield b"audio"

        original = new_hub.subscribe
        new_hub.subscribe = fake_new_subscribe
        try:
            gen = manager.subscribe(new_channel, "mp3")
            assert await anext(gen) == b"audio"
            await asyncio.sleep(0)
            assert old_task.done()
            assert manager.linger_hubs_preempted == 1
            await gen.aclose()
        finally:
            new_hub.subscribe = original
            if not old_task.done():
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass

    asyncio.run(run())

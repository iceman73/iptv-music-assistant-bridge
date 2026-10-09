# Contributing

Thanks for contributing to IPTV Music Assistant Bridge.

## Development workflow

1. Fork the repository and create a branch from `main`.
2. Make the smallest focused change that solves the problem.
3. Preserve the v5 shared-hub playback model unless the change is intentionally architectural.
4. Do not commit credentials, tokens, passwords, provider URLs containing secrets, or a real `.env` file.
5. Update documentation when configuration, behavior, or API endpoints change.
6. Add or update tests for functional changes.
7. Open a pull request describing the problem, the change, and how it was tested.

## Testing

Run the Python tests before submitting a pull request:

```bash
pytest -q
```

For Docker-related changes:

```bash
docker compose build
```

For stream changes, include relevant observations from:

```text
/health
/streams
/api/checks
/api/checks/stream/{channel_id}
```

Do not include secrets or private provider credentials in logs attached to issues or pull requests.

## Compatibility

The project prioritizes stable continuous playback over minimum latency.

Changes to FFmpeg lifecycle management, shared stream hubs, reconnect behavior,
startup silence, subscriber queues, ICY metadata, or Music Assistant import
behavior should avoid creating unnecessary upstream connections or FFmpeg
respawns.

Keep configuration backward compatible where practical. New environment
variables should have safe defaults and should be documented in both
`.env.example` and `README.md`.

## Playback issue reports

Please include:

- bridge version
- source mode (`xc` or Dispatcharr M3U)
- output format (`aac` or `mp3`)
- player/client type
- relevant `/streams` output
- relevant `/api/checks` output
- whether direct Dispatcharr playback is clean
- whether Music Assistant browser playback is clean

Redact credentials and private URLs before posting.

## License of contributions

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in this project is provided under the Apache License, Version 2.0,
consistent with Section 5 of that license.

By submitting a contribution, you represent that you have the right to submit
it under those terms.

# Remote Cursor

A read-only, Tailscale-first web mirror for Cursor's Agent Window.

It shows local agent sessions and transcripts in a browser without modifying Cursor data. Message sending is intentionally unavailable in Phase 1.

## Security model

> [!WARNING]
> Remote Cursor has no login page. Tailscale Serve and your tailnet access policy are the authentication boundary.

The server binds to `127.0.0.1` by default. Keep this default and expose it through Tailscale Serve.

**Do not bind the server to `0.0.0.0`, expose it directly to a LAN, or use Tailscale Funnel.** A direct network client can spoof Tailscale identity headers and access sensitive conversations.

## Features

- Repository-grouped Cursor agent sessions
- Full local transcript rendering
- Session search and archived sessions
- Live updates when Cursor data changes
- Local Cursor profile details
- Read-only SQLite access and no write endpoints

## Requirements

- Cursor 3 with local Agent Window data
- Python 3.11 or later
- Tailscale for remote access

macOS is currently verified. Standard Cursor locations for Windows and Linux are detected but have not been tested in production.

## Quick start

Start the local server from the repository root:

```bash
python3 -m remote_cursor.server
```

In another terminal, expose the loopback server to your tailnet:

```bash
tailscale serve 4310
```

Open the HTTPS URL printed by Tailscale. Local access remains available at [http://127.0.0.1:4310](http://127.0.0.1:4310).

## Restrict users

You can optionally allow specific Tailscale logins:

```bash
REMOTE_CURSOR_ALLOWED_USERS="you@example.com" python3 -m remote_cursor.server
```

Separate multiple logins with commas. This check trusts the `Tailscale-User-Login` header added by Tailscale Serve, so it must not be used to secure a direct network binding.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `REMOTE_CURSOR_ALLOWED_USERS` | Empty | Comma-separated Tailscale logins |
| `REMOTE_CURSOR_HOST` | `127.0.0.1` | Local bind address; keep the default |
| `REMOTE_CURSOR_PORT` | `4310` | Local server port |
| `CURSOR_HOME` | `~/.cursor` | Cursor home override |
| `CURSOR_USER_DATA` | OS default | Cursor user-data override |

## Local data

Remote Cursor reads these Cursor-managed files:

- `User/globalStorage/conversation-search.db`
- `User/globalStorage/state.vscdb`
- `~/.cursor/projects/*/agent-transcripts/*/*.jsonl`

All SQLite connections use read-only mode and `PRAGMA query_only`. `POST`, `PUT`, and `DELETE` requests return `405 Method Not Allowed`.

## Development

```bash
make check
```

See [docs/architecture.md](docs/architecture.md) for the read-only boundary and Phase 2 design constraints.

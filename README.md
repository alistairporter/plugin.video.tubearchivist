# DOCUMENTATION.md

This file gives a place to note down the structure and particulars of this project for future refernece.

## Project Overview

This is a Kodi addon for browsing and playing content from a TubeArchivist server. TubeArchivist is a self-hosted YouTube backup and media server. The addon connects to the TubeArchivist API to browse channels, playlists, and videos, and plays them directly in Kodi.

## Architecture

### Two-Module Structure

The addon consists of two main Python modules:

1. **`default.py`** - Main entry point and UI logic
   - Handles all Kodi directory listings and navigation
   - Implements action routing (`action` parameter in URL queries)
   - Creates ListItems with metadata for videos, channels, and playlists
   - Manages pagination (client-side and server-side)
   - Handles playback resolution and progress tracking
   - Context menus for channels, playlists, and videos

2. **`resources/lib/tubearchivist.py`** - API client and server protocol utilities
   - `TubeArchivist` class: HTTP client for TubeArchivist API
   - `TAPlaybackTracker` class: Monitors playback and syncs progress/watched status to server
   - Handles authentication via API token
   - Converts relative server URLs to absolute URLs

### Addon Design

**Client-Side Sorting and Pagination**: The addon fetches all pages from the TubeArchivist API for many views (channels, playlists, videos in a channel/playlist) and then sorts and paginates locally. This is because the TubeArchivist API's server-side sorting is unreliable for filtered queries (e.g., `?channel=ID` or `?playlist=ID`).

**Action-Based Routing**: The addon uses URL query parameters to route actions:
- `action=list_channels` → Show all channels
- `action=list_channel_videos&id=CHANNEL_ID&page=1` → Show videos for a channel
- `action=play&yid=VIDEO_ID` → Play a video
- `action=play_all&list_id=channel:ID` → Play all videos from a channel/playlist
- `action=play_from_here&list_id=channel:ID&index=5` → Play from a specific video in a list

**Playback Metadata**: The `_apply_playback_meta()` function handles watched status and resume points across Kodi versions (Matrix/Nexus/Omega). It prefers `InfoTagVideo.setResumePoint()` for Omega+ but falls back to legacy properties for older versions.

**Progress Tracking**: The `TAPlaybackTracker` class (in `tubearchivist.py`) polls playback state every 2 seconds and syncs to the server when playback ends. If 90%+ watched or <60s remaining, it marks as watched; otherwise, it saves the resume position.

**Subtitle Support**: The addon automatically loads subtitles from TubeArchivist when available. The `get_subtitle_urls()` helper function extracts subtitle URLs from video data and converts them to absolute URLs. Subtitles are added using `li.setSubtitles()` in both single video playback (`handle_play()`) and playlist playback (`play_all_videos()`).

### TubeArchivist API Integration

The addon communicates with TubeArchivist API endpoints:
- `/api/video/` - List and search videos
- `/api/video/{youtube_id}/` - Get video details and media URL
- `/api/video/{youtube_id}/progress/` - Update playback position (POST)
- `/api/channel/` - List channels
- `/api/playlist/` - List playlists
- `/api/watched/` - Mark videos as watched/unwatched (POST)
- `/api/search/?query=QUERY` - Search all content types

**Authentication**: All API requests include `Authorization: Token {api_token}` header.

**URL Handling**: The TubeArchivist API returns relative URLs (e.g., `/media/videos/...`). The `fix_url()` method converts these to absolute URLs by prepending the server URL.

**Subtitles**: The API returns subtitle information in the `subtitles` array for each video (see `SubtitleItem` schema in `Tube Archivist API.yaml`). Each subtitle has:
- `lang` - Language code
- `media_url` - Path to subtitle file on server
- `ext` - File extension (vtt, srt, etc.)
- `source` - Either "auto" (auto-generated) or "user" (manually uploaded)
- `name` - Display name

## Development

### Testing the Addon

Since this is a Kodi addon, testing requires a running Kodi instance
This can be provided with `nix run "nixpkgs#kodi"` if nix is available or using flatpak or the system package manager if not.

Once that is satisfied:

1. Ensure Kodi can access this directory (either install it i.e: zip repo and install or symlink to Kodi's addons directory: usually at `~/.kodi/addons`)
2. Configure the addon settings in Kodi:
   - Server URL (default: `http://localhost:8000`)
   - API Token (get from TubeArchivist settings)
3. Check Kodi logs for debugging: `~/.kodi/temp/kodi.log` (Linux) or equivalent
4. Look for log entries prefixed with `TubeArchivist:` or `TA `

### Adding New Features

**Adding a new action**:
1. Add a new `elif` clause in the `if __name__ == "__main__"` block at the bottom of `default.py`
2. Create a corresponding function (e.g., `def handle_my_action():`)
3. Use `build_url()` to create URLs for the new action
4. Add to context menus if needed (see `add_*_context_menu()` functions)

**Adding API methods**:
1. Add methods to the `TubeArchivist` class in `resources/lib/tubearchivist.py`
2. Use `self.get()` for GET requests or `self.post()` for POST requests
3. Reference `Tube Archivist API.yaml` for endpoint details and schemas

**Video metadata**: Use `create_video_listitem()` function which handles all video metadata, thumbnails, context menus, and playback metadata in one place.

### Settings

User-configurable settings are defined in `resources/settings.xml`:
- `server_url` - TubeArchivist server URL
- `api_token` - API authentication token
- `sort_order` - Video sort preference (newest first, oldest first, A-Z, Z-A)
- `max_videos` - Videos per page (10-500, default 50)

Access settings in code: `addon.getSetting("setting_id")`

## API Documentation

The TubeArchivist api is available on a running instance at `/api/docs` with a schema at `/api/schema`

A copy of this schema is in `Tube Archivist API.yaml` (OpenAPI 3.0). Refer to this file for:
- Available endpoints and parameters
- Request/response schemas
- Authentication requirements
- Pagination structure

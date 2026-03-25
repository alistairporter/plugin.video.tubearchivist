# DOCUMENTATION.md

Notes on how this project is structured, for future reference (or when I inevitably forget how something works).

## Structure

There are two main modules:

`default.py` - Entry point and all the UI stuff
- Handles Kodi directory listings and navigation
- Routes actions via URL query parameters (`action=whatever`)
- Creates ListItems with video/channel/playlist metadata
- Pagination (mostly client-side, more on that below)
- Playback resolution and progress tracking
- Context menus

`resources/lib/tubearchivist.py` - API client and helpers
- `TubeArchivist` class for talking to the API
- `TAPlaybackTracker` for monitoring playback and syncing progress back to the server
- Auth via API token
- URL handling (converts relative API URLs to absolute)

### Design _quirks_ (bodges)

Client-side sorting/pagination: Fetch all the pages from the API and then sort + paginate locally. Why? Because TubeArchivist's server-side sorting doesn't work right when you filter by channel or playlist. So we just pull everything and handle it ourselves.

Action routing: URL params determine what happens:
- `action=list_channels` → show channels
- `action=list_channel_videos&id=CHANNEL_ID&page=1` → videos for a channel
- `action=play&yid=VIDEO_ID` → play a video
- `action=play_all&list_id=channel:ID` → play all videos from a channel/playlist
- `action=play_from_here&list_id=channel:ID&index=5` → start playing from a specific video

Playback metadata: `_apply_playback_meta()` deals with watched status and resume points. It tries to use `InfoTagVideo.setResumePoint()` for Kodi Omega and newer, but falls back to the old properties for Matrix/Nexus.

Progress tracking: `TAPlaybackTracker` polls every 2 seconds during playback and syncs when you're done. If you watched 90%+ or there's less than 60s left, it marks the video as watched. Otherwise it just saves your position.

Subtitles: If TubeArchivist has subtitles, we load them automatically. `get_subtitle_urls()` pulls them from the video data and fixes up the URLs. Works in both single video playback and playlists.

### API stuff

used endpoints:
- `/api/video/` - list/search videos
- `/api/video/{youtube_id}/` - get video details and media URL
- `/api/video/{youtube_id}/progress/` - update playback position (POST)
- `/api/channel/` - list channels
- `/api/playlist/` - list playlists
- `/api/watched/` - mark watched/unwatched (POST)
- `/api/search/?query=QUERY` - search everything

Auth: Every request needs `Authorization: Token {api_token}` in the headers.

URL handling: The API returns relative URLs like `/media/videos/...`, so `fix_url()` turns them into absolute URLs by inserting the server URL at the start.

Subtitles: The API includes a `subtitles` array for each video (check `Tube Archivist API.yaml` for the schema). Each entry has:
- `lang` - language code
- `media_url` - path to subtitle file
- `ext` - file extension (vtt, srt, etc.)
- `source` - "auto" or "user"
- `name` - display name

## Development stuff

### Releases

Handled by Forgejo Actions (`.forgejo/workflows/release.yml`). Just:

1. Bump the version in `addon.xml`:
   ```xml
   <addon id="plugin.video.tubearchivist"
          name="Tube Archivist"
          version="0.4.0"
          ...>
   ```

2. Commit and push:
   ```bash
   git add addon.xml
   git commit -m "Bump version to 0.4.0"
   git push origin main
   ```

The workflow does the rest:
- Tags the commit as `v0.4.0`
- Packages everything as `plugin.video.tubearchivist-0.4.0.zip`
- Generates a changelog from commits since last tag
- Creates a Forgejo release with the zip

The zip excludes dev files (`.git`, `.forgejo`, `__pycache__`, `*.yaml`, `DOCUMENTATION.md`) so it's ready to install.

### Testing

You need Kodi running. Get it with `nix run "nixpkgs#kodi"` if you have nix, otherwise use flatpak or your package manager.

Then:

1. Install the addon - either zip it up and install through Kodi, or clone into `~/.kodi/addons`
2. Configure settings in Kodi:
   - Server URL (defaults to `http://localhost:8000`)
   - API Token (grab from TubeArchivist settings)
3. Check logs at `~/.kodi/temp/kodi.log` (Linux)
4. Look for lines starting with `TubeArchivist:` or `TA `

### Adding features

New action:
1. Add an `elif` clause to the `if __name__ == "__main__"` block at the bottom of `default.py`
2. Write the handler function (like `def handle_my_action():`)
3. Use `build_url()` to make URLs for it
4. If you need it in context menus, check out the `add_*_context_menu()` functions

New API method:
1. Add it to the `TubeArchivist` class in `resources/lib/tubearchivist.py`
2. Use `self.get()` for GETs, `self.post()` for POSTs
3. Check `Tube Archivist API.yaml` for endpoint docs

Video metadata: Just use `create_video_listitem()` - it handles metadata, thumbnails, context menus, and playback stuff all in one shot.

### Settings

Defined in `resources/settings.xml`:
- `server_url` - where your TubeArchivist server is
- `api_token` - auth token
- `sort_order` - how to sort videos (newest, oldest, A-Z, Z-A)
- `max_videos` - videos per page (10-500, defaults to 50)

Get them in code with `addon.getSetting("setting_id")`

## API docs

TubeArchivist exposes API docs at `/api/docs` with the schema at `/api/schema` on your running instance.

There's a copy of the schema in `Tube Archivist API.yaml` (OpenAPI 3.0) - use it to check endpoints, request/response formats, auth, and pagination.

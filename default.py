import sys
import math
import urllib.parse
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from resources.lib.tubearchivist import TubeArchivist

# Constants
MAX_API_PAGES = 100  # Prevent infinite loops on malformed API responses
WATCHED_THRESHOLD_PERCENT = 95
FINISHED_THRESHOLD_PERCENT = 90
FINISHED_REMAINING_SECONDS = 60
MIN_PROGRESS_SECONDS = 5
PLAYBACK_START_TIMEOUT_MS = 15000
PLAYBACK_POLL_INTERVAL_MS = 1000
SEEK_STABILIZATION_MS = 1000
SEEK_VERIFICATION_ATTEMPTS = 20
SEEK_TOLERANCE_SECONDS = 5

# Global instances
addon = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
ta = TubeArchivist()

# Get the plugin base URL
PLUGIN_URL = sys.argv[0]

def build_url(query: Dict[str, str]) -> str:
    """Build a plugin URL from a query dict."""
    return sys.argv[0] + "?" + urllib.parse.urlencode(query)

def fetch_all_pages(endpoint: str, **params) -> List[Dict]:
    """
    Fetch all pages from a paginated TubeArchivist endpoint.

    Args:
        endpoint: API endpoint (e.g., "video/?channel=...")
        **params: Additional query parameters

    Returns:
        List of all items across all pages

    Raises:
        RuntimeError: If MAX_API_PAGES is exceeded (likely API issue)
    """
    all_items = []
    cur = 1

    while cur <= MAX_API_PAGES:
        try:
            data = ta.get(f"{endpoint}&page={cur}" if "?" in endpoint else f"{endpoint}?page={cur}")
            items = data.get("data", [])

            if not items:
                break

            all_items.extend(items)

            paginate = data.get("paginate") or {}
            last_page = int(paginate.get("last_page", cur))

            if cur >= last_page:
                break

            cur += 1
        except Exception as e:
            xbmc.log(f"TubeArchivist: Error fetching page {cur}: {e}", xbmc.LOGERROR)
            break

    if cur > MAX_API_PAGES:
        xbmc.log(f"TubeArchivist: Exceeded MAX_API_PAGES ({MAX_API_PAGES}), possible API issue", xbmc.LOGWARNING)

    return all_items

def get_max_videos() -> int:
    """Get the max videos per page from settings."""
    return int(addon.getSetting("max_videos") or 50)

def get_subtitle_urls(video_data: Dict) -> List[str]:
    """
    Extract subtitle URLs from video data.

    Args:
        video_data: Video dict from Tube Archivist API

    Returns:
        List of absolute subtitle URLs
    """
    subtitles = video_data.get("subtitles", [])
    if not subtitles:
        return []

    subtitle_urls = []
    for sub in subtitles:
        media_url = sub.get("media_url")
        if media_url:
            # Convert relative URL to absolute
            absolute_url = ta.server_url + media_url
            subtitle_urls.append(absolute_url)
            xbmc.log(f"TubeArchivist: Found subtitle - lang: {sub.get('lang')}, source: {sub.get('source')}, url: {absolute_url}", xbmc.LOGDEBUG)

    return subtitle_urls

def add_channel_context_menu(li: xbmcgui.ListItem, channel_id: str) -> None:
    """
    Add context menu items for a channel.

    Args:
        li: The ListItem to add context menu to
        channel_id: The channel ID
    """
    context_menu = []

    # Play All Videos from this channel
    play_all_url = build_url({"action": "play_all", "list_id": f"channel:{channel_id}"})
    context_menu.append(("Play All", f"RunPlugin({play_all_url})"))

    # View Channel Playlists
    playlists_url = build_url({"action": "list_channel_playlists", "id": channel_id})
    context_menu.append(("View Channel Playlists", f"Container.Update({playlists_url})"))

    li.addContextMenuItems(context_menu)

def add_playlist_context_menu(li: xbmcgui.ListItem, playlist_id: str) -> None:
    """
    Add context menu items for a playlist.

    Args:
        li: The ListItem to add context menu to
        playlist_id: The playlist ID
    """
    context_menu = []

    # Play All Videos from this playlist
    play_all_url = build_url({"action": "play_all", "list_id": f"playlist:{playlist_id}"})
    context_menu.append(("Play All", f"RunPlugin({play_all_url})"))

    li.addContextMenuItems(context_menu)

def add_video_context_menu(li, channel_id):
    """
    Add context menu items for a video.

    Args:
        li: The ListItem to add context menu to
        channel_id: The channel ID of the video
    """
    if not channel_id:
        return

    context_menu = []
    go_to_channel_url = build_url({"action": "list_channel_videos", "id": channel_id})
    context_menu.append(("Go to Channel", f"Container.Update({go_to_channel_url})"))
    channel_playlists_url = build_url({"action": "list_channel_playlists", "id": channel_id})
    context_menu.append(("View Channel Playlists", f"Container.Update({channel_playlists_url})"))
    li.addContextMenuItems(context_menu)

def create_video_listitem(video, add_play_from_here=False, video_list_id=None, video_index=None):
    """
    Create a Kodi ListItem for a video with all metadata and context menu.

    Args:
        video: Video dict from Tube Archivist API
        add_play_from_here: If True, add "Play from Here" context menu
        video_list_id: Identifier for the video list (e.g., "channel:ID" or "playlist:ID")
        video_index: Index of this video in the list

    Returns:
        tuple: (ListItem, play_url)
    """
    title = video.get("title", "Untitled")
    desc = video.get("description", "")
    thumb = ta.server_url + (video.get("vid_thumb_url") or "")
    video_id = video.get('youtube_id')
    play_url = build_url({"action": "play", "video_id": video_id})
    ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

    li = xbmcgui.ListItem(label=title)
    info = li.getVideoInfoTag()
    info.setTitle(title)
    info.setPlot(desc)
    if thumb:
        li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
    li.setProperty("IsPlayable", "true")

    _apply_playback_meta(li, video)
    add_video_context_menu(li, ch_id)

    # Add "Play from Here" if in a list context
    if add_play_from_here and video_list_id and video_index is not None:
        play_from_url = build_url({
            "action": "play_from_here",
            "list_id": video_list_id,
            "index": str(video_index)
        })
        li.addContextMenuItems([("Play from Here", f"RunPlugin({play_from_url})")], True)

    return li, play_url

def play_all_videos(videos: List[Dict], start_index: int = 0) -> None:
    """
    Create a Kodi playlist from videos and start playing.

    Args:
        videos: List of video dicts from Tube Archivist API
        start_index: Index to start playing from (default 0)
    """
    if not videos:
        xbmc.log("TubeArchivist: No videos to play", xbmc.LOGWARNING)
        return

    # Get the video playlist
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    playlist.clear()

    xbmc.log(f"TubeArchivist: Creating playlist with {len(videos)} videos, starting at {start_index}", xbmc.LOGINFO)

    # Collect all video IDs that need detailed info
    videos_needing_fetch = []
    for i, video in enumerate(videos[start_index:], start=start_index):
        video_id = video.get('youtube_id')
        if video_id and not video.get("media_url"):
            videos_needing_fetch.append((i, video_id))

    # Batch fetch if needed (currently TA API doesn't support batch, so we minimize)
    # For now, we'll fetch only when needed
    for i, video in enumerate(videos[start_index:], start=start_index):
        video_id = video.get('youtube_id')
        if not video_id:
            continue

        try:
            # Check if we already have media_url (from detailed video listing)
            media_url_path = video.get("media_url")

            if not media_url_path:
                # Need to fetch full video details
                data = ta.get(f"video/{video_id}/")
                video_data = (data.get("data") or data) if isinstance(data, dict) else {}
                media_url_path = video_data.get("media_url")
            else:
                # Use cached data
                video_data = video

            if not media_url_path:
                xbmc.log(f"TubeArchivist: No media_url for video {video_id}", xbmc.LOGWARNING)
                continue

            media_url = ta.server_url + media_url_path
            title = video.get("title", "Untitled")
            thumb = ta.server_url + (video.get("vid_thumb_url") or "")

            li = xbmcgui.ListItem(label=title)
            li.setPath(media_url)
            if thumb:
                li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})

            info = li.getVideoInfoTag()
            info.setTitle(title)
            info.setPlot(video.get("description", ""))

            # Add subtitles if available
            subtitle_urls = get_subtitle_urls(video_data)
            if subtitle_urls:
                li.setSubtitles(subtitle_urls)
                xbmc.log(f"TubeArchivist: Added {len(subtitle_urls)} subtitle(s) to playlist video {video_id}", xbmc.LOGDEBUG)

            playlist.add(media_url, li)
            xbmc.log(f"TubeArchivist: Added video {i}: {title}", xbmc.LOGDEBUG)

        except Exception as e:
            xbmc.log(f"TubeArchivist: Failed to add video {video_id} to playlist: {e}", xbmc.LOGWARNING)

    # Start playing the playlist
    if playlist.size() > 0:
        xbmc.Player().play(playlist)
        xbmc.log(f"TubeArchivist: Started playlist with {playlist.size()} videos", xbmc.LOGINFO)
    else:
        xbmc.log("TubeArchivist: Playlist is empty, nothing to play", xbmc.LOGWARNING)

def root_menu():
    items = [
        ("Channels", {"action": "list_channels"}),
        ("Playlists", {"action": "list_playlists"}),
        ("In Progress Videos", {"action": "list_partial_videos"}),
        ("Recent Videos", {"action": "list_videos"}),
        ("Search", {"action": "search_menu"}),
    ]
    for label, q in items:
        li = xbmcgui.ListItem(label=label)
        url = build_url(q)
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)

def get_sort_order() -> str:
    """Return the current sort order string for Tube Archivist API"""
    try:
        choice = int(addon.getSetting("sort_order") or 0)
    except ValueError:
        choice = 0

    mapping = {
        0: "-published",   # newest first
        1: "published",    # oldest first
        2: "title",        # alphabetical A-Z
        3: "-title",       # alphabetical Z-A
    }
    return mapping.get(choice, "-published")

def _apply_playback_meta(li: xbmcgui.ListItem, v: Dict) -> None:
    """
    Sets watched tick and progress, working on Matrix/Nexus/Omega.
    - Prefers InfoTagVideo.setResumePoint (Omega+), falls back to properties.
    - Ensures media type + duration are set.
    """
    p = (v.get("player") or {})
    info = li.getVideoInfoTag()

    # Mark as a video so skins treat it correctly
    try:
        info.setMediaType("video")
    except Exception:
        pass

    # Duration (seconds)
    dur = p.get("duration") or v.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        dur = int(dur)
        info.setDuration(dur)
    else:
        dur = None

    # Resume position (seconds)
    resume = None
    for k in ("position", "resume", "current", "time", "progress_seconds"):
        if isinstance(p.get(k), (int, float)):
            resume = int(p[k])
            break

    # Percent → seconds if needed
    percent = None
    for k in ("percentage", "progress", "watched_percent"):
        if isinstance(p.get(k), (int, float)):
            percent = float(p[k])
            break
    if resume is None and percent is not None and dur:
        resume = int(dur * (percent / 100.0))

    # Watched?
    watched = bool(p.get("watched") or v.get("watched"))
    if not watched and (percent is not None and percent >= WATCHED_THRESHOLD_PERCENT):
        watched = True

    # Apply playcount (the watched tick)
    info.setPlaycount(1 if watched else 0)

    # Last played (optional)
    wd = p.get("watched_date")
    if isinstance(wd, (int, float)) and wd > 0:
        try:
            info.setLastPlayed(datetime.fromtimestamp(int(wd), timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            xbmc.log(f"TubeArchivist: Failed to set last played date: {e}", xbmc.LOGDEBUG)

    # Apply resume/progress
    # If watched, clear resume so Kodi shows the tick instead of a resume bar
    if watched:
        # Omega+ has a way to set "finished" resume; otherwise do nothing
        try:
            # setResumePoint(position, total) — using total only marks as having metadata
            info.setResumePoint(0, dur if dur else 0)
        except Exception:
            # Clear legacy properties if you set them elsewhere
            li.setProperty("ResumeTime", "0")
            if dur:
                li.setProperty("TotalTime", str(dur))
        return

    # Not watched → set partial progress if sensible
    if dur and isinstance(resume, (int, float)) and 0 < resume < (dur - 3):
        try:
            # Preferred on Omega+
            info.setResumePoint(int(resume), int(dur))
        except Exception:
            # Legacy Matrix/Nexus
            li.setProperty("TotalTime", str(int(dur)))
            li.setProperty("ResumeTime", str(int(resume)))
            
def list_channels():
    """List all channels sorted alphabetically."""
    # Fetch all channel pages
    all_channels = fetch_all_pages("channel/")

    # Sort channels A→Z by name (case-insensitive)
    all_channels.sort(key=lambda c: (c.get("channel_name") or "").lower())

    # Render
    for ch in all_channels:
        label = ch.get("channel_name", "Unknown Channel")
        thumb = ta.server_url + (ch.get("channel_thumb_url") or "")
        banner = ta.server_url + (ch.get("channel_banner_url") or "")
        fanart = ta.server_url + (ch.get("channel_tvart_url") or "")
        ch_id = ch.get("channel_id")

        li = xbmcgui.ListItem(label=label)
        li.setArt({"thumb": thumb, "icon": thumb, "banner": banner, "fanart": fanart})

        if ch_id:
            add_channel_context_menu(li, ch_id)
            url = build_url({"action": "list_channel_videos", "id": ch_id})
            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)
    
def list_playlists(page=1):
    """
    List all playlists using API pagination.

    Args:
        page: Page number to fetch
    """
    # Fetch single page from API
    data = ta.get(f"playlist/?page={page}")
    items = data.get("data", [])
    paginate = data.get("paginate", {})

    current_page = paginate.get("current_page", 1)
    last_page = paginate.get("last_page", 1)
    total_hits = paginate.get("total_hits", len(items))

    for pl in items:
        label = pl.get("playlist_name", "Unnamed Playlist")
        pl_id = pl.get("playlist_id")
        # API returns playlist_thumbnail, not playlist_thumb_url
        thumb = ta.fix_url(pl.get("playlist_thumbnail") or pl.get("playlist_thumb_url"))
        li = xbmcgui.ListItem(label=label)
        if thumb:
            li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})

        # Add context menu
        if pl_id:
            add_playlist_context_menu(li, pl_id)

        url = build_url({"action": "list_playlist_videos", "id": pl_id})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Navigation - Next page button
    if current_page < last_page:
        next_url = build_url({
            "action": "list_playlists",
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)

def _local_sort(videos, sort_choice):
    """Sort TA video dicts locally using the user's setting."""
    reverse = sort_choice.startswith("-")
    keyname = sort_choice.lstrip("-")

    if keyname == "title":
        keyfunc = lambda v: (v.get("title") or "").lower()
    elif keyname == "published":
        # ISO-8601 sorts correctly as a string; no datetime parsing needed.
        keyfunc = lambda v: v.get("published") or ""
    else:
        keyfunc = lambda v: v.get(keyname, "")

    try:
        return sorted(videos, key=keyfunc, reverse=reverse)
    except Exception:
        return videos


def list_channel_videos(channel_id: str, page: int = 1):
    """
    List videos for a channel with client-side sorting and pagination.

    Args:
        channel_id: The channel ID
        page: Page number to display
    """
    if not channel_id:
        xbmc.log("TubeArchivist: channel_id is required for list_channel_videos", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    sort_choice = get_sort_order()
    max_per_page = get_max_videos()

    # 1) Pull ALL pages from TA for this channel (server-side sort is unreliable here)
    all_videos = fetch_all_pages(f"video/?channel={channel_id}")

    # 2) Sort locally
    all_videos = _local_sort(all_videos, sort_choice)

    # 3) Slice to show only the current page
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * max_per_page
    end = page * max_per_page
    page_slice = all_videos[start:end]

    current_page = page
    last_page = total_pages
    total_hits = total

    xbmcplugin.setContent(HANDLE, "videos")

    # 4) Render items with context menu
    list_id = f"channel:{channel_id}"
    for i, video in enumerate(page_slice, start=start):
        li, play_url = create_video_listitem(video, add_play_from_here=True, video_list_id=list_id, video_index=i)
        xbmcplugin.addDirectoryItem(HANDLE, play_url, li, isFolder=False)

    # Navigation - Next page button
    if current_page < last_page:
        next_url = build_url({
            "action": "list_channel_videos",
            "id": channel_id,
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)



def list_channel_playlists(channel_id: str):
    """List all playlists for a specific channel."""
    if not channel_id:
        xbmc.log("TubeArchivist: channel_id is required for list_channel_playlists", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    # Fetch all playlists and filter by channel
    all_playlists = fetch_all_pages("playlist/")

    # Filter playlists that belong to this channel
    channel_playlists = [
        pl for pl in all_playlists
        if pl.get("playlist_channel_id") == channel_id
    ]

    if not channel_playlists:
        li = xbmcgui.ListItem(label="No playlists found for this channel")
        xbmcplugin.addDirectoryItem(HANDLE, "", li, isFolder=False)
    else:
        for pl in channel_playlists:
            label = pl.get("playlist_name", "Unnamed Playlist")
            # API returns playlist_thumbnail, not playlist_thumb_url
            thumb = ta.fix_url(pl.get("playlist_thumbnail") or pl.get("playlist_thumb_url"))
            li = xbmcgui.ListItem(label=label)
            if thumb:
                li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
            url = build_url({"action": "list_playlist_videos", "id": pl.get("playlist_id")})
            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)

def list_playlist_videos(playlist_id: str, page: int = 1):
    """
    List videos for a playlist with client-side sorting and pagination.

    Args:
        playlist_id: The playlist ID
        page: Page number to display
    """
    if not playlist_id:
        xbmc.log("TubeArchivist: playlist_id is required for list_playlist_videos", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    sort_choice = get_sort_order()
    max_per_page = get_max_videos()

    # 1) Pull ALL pages from TA for this playlist
    all_videos = fetch_all_pages(f"video/?playlist={playlist_id}")

    # 2) Sort locally
    all_videos = _local_sort(all_videos, sort_choice)

    # 3) Slice to show only the current page
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * max_per_page
    end = page * max_per_page
    page_slice = all_videos[start:end]

    current_page = page
    last_page = total_pages
    total_hits = total

    xbmcplugin.setContent(HANDLE, "videos")

    # 4) Render items with context menu
    list_id = f"playlist:{playlist_id}"
    for i, video in enumerate(page_slice, start=start):
        li, play_url = create_video_listitem(video, add_play_from_here=True, video_list_id=list_id, video_index=i)
        xbmcplugin.addDirectoryItem(HANDLE, play_url, li, isFolder=False)

    # Navigation - Next page button
    if current_page < last_page:
        next_url = build_url({
            "action": "list_playlist_videos",
            "id": playlist_id,
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def list_partial_videos(page: int = 1):
    """
    List in-progress videos with client-side sorting and pagination.

    Args:
        page: Page number to display
    """
    sort_choice = get_sort_order()
    max_per_page = get_max_videos()

    # 1) Pull ALL pages from TA
    all_videos = fetch_all_pages("video/?watch=continue")

    # 2) Sort locally
    all_videos = _local_sort(all_videos, sort_choice)

    # 3) Slice to show only the current page
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * max_per_page
    end = page * max_per_page
    page_slice = all_videos[start:end]

    current_page = page
    last_page = total_pages
    total_hits = total

    xbmcplugin.setContent(HANDLE, "videos")

    # 4) Render items with context menu
    for video in page_slice:
        li, play_url = create_video_listitem(video)
        xbmcplugin.addDirectoryItem(HANDLE, play_url, li, isFolder=False)

    # Navigation - Next page button
    if current_page < last_page:
        next_url = build_url({
            "action": "list_partial_videos",
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def list_videos(page: int = 1):
    """
    List recent videos with client-side sorting and pagination.

    Args:
        page: Page number to display
    """
    sort_choice = get_sort_order()
    max_per_page = get_max_videos()

    xbmcplugin.setContent(HANDLE, "videos")

    # 1) Pull ALL pages from TA
    all_videos = fetch_all_pages("video/")

    # 2) Sort locally
    all_videos = _local_sort(all_videos, sort_choice)

    # 3) Slice to show only the current page
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))

    start = (page - 1) * max_per_page
    end = page * max_per_page
    page_slice = all_videos[start:end]

    current_page = page
    last_page = total_pages
    total_hits = total

    # Render items with context menu
    for video in page_slice:
        title = video.get("title", "Untitled")
        desc  = video.get("description", "")
        thumb = ta.server_url + (video.get("vid_thumb_url") or "")
        play = f"{sys.argv[0]}?action=play&video_id={video.get('youtube_id')}"
        ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

        li   = xbmcgui.ListItem(label=title)
        info = li.getVideoInfoTag()
        info.setTitle(title)
        info.setPlot(desc)
        if thumb:
            li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
        li.setProperty("IsPlayable", "true")

        _apply_playback_meta(li, video)

        # Add context menu for going to channel
        context_menu = []
        if ch_id:
            go_to_channel_url = build_url({"action": "list_channel_videos", "id": ch_id})
            context_menu.append(("Go to Channel", f"Container.Update({go_to_channel_url})"))
            channel_playlists_url = build_url({"action": "list_channel_playlists", "id": ch_id})
            context_menu.append(("View Channel Playlists", f"Container.Update({channel_playlists_url})"))

        if context_menu:
            li.addContextMenuItems(context_menu)

        xbmcplugin.addDirectoryItem(HANDLE, play, li, isFolder=False)

    # Navigation - Next page button
    if current_page < last_page:
        next_url = build_url({
            "action": "list_videos",
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def _get_first(d, *keys, default=None):
    """Safe helper: return first existing key from d."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default

def search_menu():
    """Show search type selection menu."""
    items = [
        ("Search All", {"action": "search", "type": "all"}),
        ("Search Videos", {"action": "search", "type": "video"}),
        ("Search Channels", {"action": "search", "type": "channel"}),
        ("Search Playlists", {"action": "search", "type": "playlist"}),
    ]
    for label, params in items:
        li = xbmcgui.ListItem(label=label)
        url = build_url(params)
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)

def search(page=1, search_type="all"):
    """
    Search Tube Archivist content.

    Args:
        page: Page number for pagination
        search_type: Type of content to search for ("all", "video", "channel", "playlist")
    """
    # read query from URL or prompt
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    q = params.get("q")
    if not q:
        search_type_label = {"all": "All", "video": "Videos", "channel": "Channels", "playlist": "Playlists"}.get(search_type, "All")
        q = xbmcgui.Dialog().input(f"Search {search_type_label}")
    if not q:
        xbmcplugin.endOfDirectory(HANDLE)
        return

    xbmc.log(f"TubeArchivist: searching for '{q}' (type: {search_type}, page {page})", xbmc.LOGINFO)

    sort_choice  = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    enc_q = urllib.parse.quote(q)
    try:
        payload = ta.get(f"search/?query={enc_q}&page={page}") or {}
        xbmc.log(f"TubeArchivist: search returned {len(payload.get('results', {}))} result types", xbmc.LOGDEBUG)
    except Exception as e:
        xbmc.log(f"TubeArchivist: search api error: {e}", xbmc.LOGERROR)
        li = xbmcgui.ListItem(label=f"Search error: {e}")
        xbmcplugin.addDirectoryItem(HANDLE, "", li, isFolder=False)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    results = payload.get("results") or {}
    video_results    = results.get("video_results") or []
    channel_results  = results.get("channel_results") or []
    playlist_results = results.get("playlist_results") or []

    xbmc.log(f"TubeArchivist: search found {len(channel_results)} channels, {len(playlist_results)} playlists, {len(video_results)} videos", xbmc.LOGINFO)

    xbmcplugin.setContent(HANDLE, "videos")

    # Filter results based on search type
    show_channels = search_type in ("all", "channel")
    show_playlists = search_type in ("all", "playlist")
    show_videos = search_type in ("all", "video")

    # --- channels (folders) ---
    if show_channels:
        for ch in channel_results:
            ch_id    = _get_first(ch, "channel_id", "id")
            if not ch_id:
                continue
            ch_name  = _get_first(ch, "channel_name", "title", default="Channel")
            ch_thumb = ta.server_url + (_get_first(ch, "channel_thumb_url", "thumb", default="") or "")
            ch_fan   = ta.server_url + (_get_first(ch, "channel_banner_url", "channel_tvart_url", "fanart", default="") or "")

            url = build_url({"action": "list_channel_videos", "id": ch_id})
            li = xbmcgui.ListItem(label=f"[CHANNEL] {ch_name}")
            li.setArt({"thumb": ch_thumb, "icon": ch_thumb, "fanart": ch_fan})

            # Add context menu for channel playlists
            context_menu = []
            playlists_url = build_url({"action": "list_channel_playlists", "id": ch_id})
            context_menu.append(("View Channel Playlists", f"Container.Update({playlists_url})"))
            li.addContextMenuItems(context_menu)

            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # --- playlists (folders) ---
    if show_playlists:
        for pl in playlist_results:
            pl_id    = _get_first(pl, "playlist_id", "id")
            if not pl_id:
                continue
            pl_name  = _get_first(pl, "playlist_name", "title", default="Playlist")
            pl_thumb = ta.server_url + (_get_first(pl, "playlist_thumbnail", "playlist_thumb_url", "thumb", default="") or "")
            pl_fan   = ta.server_url + (_get_first(pl, "playlist_banner_url", "fanart", default="") or "")
            # Optional: show number of items if available
            count    = _get_first(pl, "playlist_entries", "entries", "count")
            if isinstance(count, list):
                count = len(count)
            suffix = f" ({count})" if isinstance(count, int) and count > 0 else ""
            url = build_url({"action": "list_playlist_videos", "id": pl_id})
            li = xbmcgui.ListItem(label=f"[PLAYLIST] {pl_name}{suffix}")
            li.setArt({"thumb": pl_thumb, "icon": pl_thumb, "fanart": pl_fan})
            xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # --- videos (items) ---
    if show_videos:
        try:
            videos_sorted = _local_sort(video_results, sort_choice)
        except Exception:
            key_map = {
                "date":   lambda v: v.get("published") or "",
                "title":  lambda v: v.get("title") or "",
                "length": lambda v: (v.get("player") or {}).get("duration") or 0,
                "views":  lambda v: (v.get("stats") or {}).get("view_count") or 0,
            }
            reverse = sort_choice in ("date", "views", "length")
            key_fn  = key_map.get(sort_choice, key_map["date"])
            videos_sorted = sorted(video_results, key=key_fn, reverse=reverse)

        videos_to_render = videos_sorted[:max_per_page]

        for video in videos_to_render:
            li, play_url = create_video_listitem(video)
            xbmcplugin.addDirectoryItem(HANDLE, play_url, li, isFolder=False)

    # Pagination - check if there are more results
    paginate = payload.get("paginate") or {}
    next_pages = paginate.get("next_pages") or []
    current_page = paginate.get("current_page", page)

    if next_pages and len(next_pages) > 0:
        next_page = next_pages[0]
        url = build_url({"action": "search", "q": q, "type": search_type, "page": str(next_page)})
        li = xbmcgui.ListItem(label=f"Next Page ({next_page}) →")
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Show "no results" message if nothing found
    total_results = 0
    if show_channels:
        total_results += len(channel_results)
    if show_playlists:
        total_results += len(playlist_results)
    if show_videos:
        total_results += len(videos_to_render) if 'videos_to_render' in locals() else 0

    if total_results == 0:
        search_type_label = {"all": "", "video": " videos", "channel": " channels", "playlist": " playlists"}.get(search_type, "")
        li = xbmcgui.ListItem(label=f"No{search_type_label} results for: {q}")
        xbmcplugin.addDirectoryItem(HANDLE, "", li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

# ---------- TA scrobble helpers ----------
def _ta_mark_progress(video_id: str, position: int) -> None:
    """POST /api/video/{video_id}/progress/  ->  {"position": <seconds>}"""
    try:
        ta.post(f"video/{video_id}/progress/", {"position": int(position)})
    except Exception as e:
        xbmc.log(f"TA progress update failed for {video_id}: {e}", xbmc.LOGWARNING)

def _ta_mark_watched(video_id: str, is_watched: bool = True) -> None:
    """POST /api/watched/  ->  {"id": <video_id>, "is_watched": true|false}"""
    try:
        ta.post("watched/", {"id": video_id, "is_watched": bool(is_watched)})
    except Exception as e:
        xbmc.log(f"TA watched toggle failed for {video_id}: {e}", xbmc.LOGWARNING)
# ----------------------------------------

# ---------- SponsorBlock helpers ----------
def _get_sponsorblock_settings() -> Optional[Dict]:
    """Read SponsorBlock settings from addon configuration."""
    if not addon.getSettingBool("sponsorblock_enabled"):
        return None

    # Map category names to action codes: 0=Skip, 1=Mute, 2=Nothing
    categories = {
        "sponsor": int(addon.getSetting("sponsorblock_sponsor") or "0"),
        "intro": int(addon.getSetting("sponsorblock_intro") or "0"),
        "outro": int(addon.getSetting("sponsorblock_outro") or "2"),
        "selfpromo": int(addon.getSetting("sponsorblock_selfpromo") or "2"),
        "interaction": int(addon.getSetting("sponsorblock_interaction") or "2"),
        "music_offtopic": int(addon.getSetting("sponsorblock_music_offtopic") or "2"),
        "preview": int(addon.getSetting("sponsorblock_preview") or "2"),
        "filler": int(addon.getSetting("sponsorblock_filler") or "2"),
    }

    return {
        "enabled": True,
        "auto_skip": addon.getSettingBool("sponsorblock_auto_skip"),
        "show_notifications": addon.getSettingBool("sponsorblock_show_notifications"),
        "categories": categories,
    }

def _filter_sponsorblock_segments(video_data: Dict, settings: Optional[Dict]) -> List[Dict]:
    """Extract and filter SponsorBlock segments based on user settings."""
    if not settings or not settings.get("enabled"):
        return []

    sponsorblock = video_data.get("sponsorblock")
    if not sponsorblock or not sponsorblock.get("is_enabled"):
        return []

    segments = sponsorblock.get("segments", [])
    if not segments:
        return []

    filtered = []
    for seg in segments:
        category = seg.get("category", "")
        action_type = seg.get("actionType", "skip")
        segment_times = seg.get("segment", [])

        if len(segment_times) != 2:
            continue

        # Get user's action preference for this category
        user_action = settings["categories"].get(category, 2)  # Default to "Nothing"

        # 0=Skip, 1=Mute, 2=Nothing
        if user_action == 2:
            continue  # User doesn't want to handle this category

        filtered.append({
            "start": float(segment_times[0]),
            "end": float(segment_times[1]),
            "category": category,
            "action": "skip" if user_action == 0 else "mute",
            "uuid": seg.get("UUID", ""),
        })

    return filtered

def _check_segment_skip(
    player: xbmc.Player,
    segments: List[Dict],
    segment_state: Dict
) -> Tuple[Optional[Dict], Dict]:
    """
    Check if current position is in a segment that needs action.
    Based on the approach from script.service.sponsorblock by siku2.

    Args:
        player: Kodi player instance
        segments: List of SponsorBlock segments
        segment_state: State tracking dict

    Returns:
        Tuple of (segment_to_handle or None, new_segment_state)

    segment_state dict contains:
    - last_pos: last known position (to detect forward progress)
    - processed_uuids: set of segment UUIDs we've already skipped
    """
    if not segments:
        return None, segment_state

    try:
        current_pos = player.getTime()
    except Exception:
        return None, segment_state

    last_pos = segment_state.get("last_pos", 0)
    processed_uuids = segment_state.get("processed_uuids", set())

    # Update state with current position
    new_state = {
        "last_pos": current_pos,
        "processed_uuids": processed_uuids
    }

    xbmc.log(f"TA SponsorBlock: Checking segments - current_pos={current_pos:.1f}s, last_pos={last_pos:.1f}s", xbmc.LOGDEBUG)

    # Find if we're currently in any segment
    for seg in segments:
        seg_uuid = seg["uuid"]
        seg_start = seg["start"]
        seg_end = seg["end"]

        # Skip segments that are in the past
        if seg_end < current_pos:
            xbmc.log(f"TA SponsorBlock: Segment {seg_uuid[:8]} is in past (ends at {seg_end:.1f}s)", xbmc.LOGDEBUG)
            continue

        # Check if we're in this segment
        if seg_start <= current_pos < seg_end:
            xbmc.log(f"TA SponsorBlock: In segment {seg_uuid[:8]} ({seg_start:.1f}-{seg_end:.1f}s)", xbmc.LOGDEBUG)

            # Already processed this segment? Don't skip again
            if seg_uuid in processed_uuids:
                xbmc.log(f"TA SponsorBlock: Segment {seg_uuid[:8]} already processed - ignoring", xbmc.LOGINFO)
                return None, new_state

            # Are we already in the segment (didn't just enter it naturally)?
            # This means the user manually seeked into it
            if seg_start < last_pos < seg_end:
                xbmc.log(f"TA SponsorBlock: Already in segment {seg_uuid[:8]} (last_pos={last_pos:.1f}s was inside), user likely seeked here - not skipping", xbmc.LOGINFO)
                return None, new_state

            # We naturally entered this segment - skip it
            xbmc.log(f"TA SponsorBlock: Naturally entered segment {seg_uuid[:8]} (last_pos={last_pos:.1f}s, seg_start={seg_start:.1f}s), will skip", xbmc.LOGINFO)
            new_state["processed_uuids"] = processed_uuids | {seg_uuid}
            return seg, new_state

    # Not in any segment
    return None, new_state

def _show_sponsorblock_notification(category: str, action: str) -> None:
    """Show a toast notification for skipped/muted segment."""
    category_names = {
        "sponsor": "Sponsor",
        "intro": "Intro",
        "outro": "Outro",
        "selfpromo": "Self-promotion",
        "interaction": "Interaction reminder",
        "music_offtopic": "Music/Off-topic",
        "preview": "Preview/Recap",
        "filler": "Filler",
    }

    category_label = category_names.get(category, category.title())
    action_label = "Skipped" if action == "skip" else "Muted"

    xbmcgui.Dialog().notification(
        "SponsorBlock",
        f"{action_label} {category_label}",
        xbmcgui.NOTIFICATION_INFO,
        2000  # 2 second display
    )
# ----------------------------------------


def handle_play() -> None:
    """Handle video playback with progress tracking and SponsorBlock integration."""
    video_id = params.get("video_id")
    if not video_id:
        xbmc.log("TubeArchivist: video_id is required for playback", xbmc.LOGERROR)
        return

    # 1) Fetch fresh video object (so media_url is up to date)
    try:
        data = ta.get(f"video/{video_id}/")
        video = (data.get("data") or data) if isinstance(data, dict) else {}

        if not video:
            xbmc.log(f"TA play: No video data returned for {video_id}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                "TubeArchivist",
                "Failed to load video",
                xbmcgui.NOTIFICATION_ERROR,
                5000
            )
            return
    except Exception as e:
        xbmc.log(f"TA play: failed to fetch video {video_id}: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "TubeArchivist",
            f"Network error: {e}",
            xbmcgui.NOTIFICATION_ERROR,
            5000
        )
        return

    media_url = ta.server_url + (video.get("media_url") or "")
    thumb     = ta.server_url + (video.get("vid_thumb_url") or "")

    # 2) Resolve to Kodi
    li = xbmcgui.ListItem(path=media_url)
    if thumb:
        li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
    li.setProperty("IsPlayable", "true")

    info = li.getVideoInfoTag()
    info.setTitle(video.get("title") or "")
    info.setPlot(video.get("description") or "")

    # Add subtitles if available
    subtitle_urls = get_subtitle_urls(video)
    if subtitle_urls:
        li.setSubtitles(subtitle_urls)
        xbmc.log(f"TubeArchivist: Added {len(subtitle_urls)} subtitle(s) to video {video_id}", xbmc.LOGINFO)

    xbmcplugin.setResolvedUrl(HANDLE, True, li)

    # 3) Setup SponsorBlock
    sb_settings = _get_sponsorblock_settings()
    sb_segments = _filter_sponsorblock_segments(video, sb_settings) if sb_settings else []
    sb_state = {
        "last_pos": 0,
        "processed_uuids": set()
    }

    if sb_segments:
        xbmc.log(f"TA play: SponsorBlock found {len(sb_segments)} segments to monitor", xbmc.LOGINFO)

    # 4) Track playback with cached last-known pos/dur
    player  = xbmc.Player()
    monitor = xbmc.Monitor()

    # Wait for playback to actually start
    started = False
    max_attempts = PLAYBACK_START_TIMEOUT_MS // 100
    for _ in range(max_attempts):
        if monitor.abortRequested():
            return
        if player.isPlayingVideo():
            started = True
            break
        xbmc.sleep(100)

    if not started:
        xbmc.log("TA play: playback never started", xbmc.LOGWARNING)
        return

    last_pos = 0
    last_dur = 0
    muted_until = 0

    # Poll once per second while playing, caching position/duration
    while not monitor.abortRequested() and player.isPlayingVideo():
        try:
            pos = int(player.getTime() or 0)
            dur = int(player.getTotalTime() or 0)
            if pos >= 0:
                last_pos = pos
            if dur > 0:
                last_dur = dur

            # Check if we need to unmute
            if muted_until > 0 and pos >= muted_until:
                player.setMuted(False)
                muted_until = 0
                xbmc.log(f"TA SponsorBlock: unmuted at {pos}s", xbmc.LOGDEBUG)

            # Check for SponsorBlock segments (state will be updated inside)
            if sb_segments:
                segment, sb_state = _check_segment_skip(player, sb_segments, sb_state)
                if segment:
                    action = segment["action"]
                    category = segment["category"]
                    seg_uuid = segment["uuid"]
                    auto_skip_enabled = sb_settings.get("auto_skip", True)

                    xbmc.log(f"TA SponsorBlock: Detected {category} segment at {segment['start']:.1f}s (UUID: {seg_uuid[:8]}...) - marking as processed", xbmc.LOGINFO)

                    # Show notification
                    if sb_settings.get("show_notifications"):
                        if auto_skip_enabled:
                            _show_sponsorblock_notification(category, action)
                        else:
                            # Notification-only mode
                            category_names = {
                                "sponsor": "Sponsor", "intro": "Intro", "outro": "Outro",
                                "selfpromo": "Self-promotion", "interaction": "Interaction",
                                "music_offtopic": "Music/Off-topic", "preview": "Preview",
                                "filler": "Filler"
                            }
                            cat_label = category_names.get(category, category.title())
                            xbmcgui.Dialog().notification(
                                "SponsorBlock",
                                f"{cat_label} segment",
                                xbmcgui.NOTIFICATION_INFO,
                                3000
                            )

                    # Auto-skip is enabled - perform the action
                    if auto_skip_enabled:
                        if action == "skip":
                            # Use direct seekTime() like script.service.sponsorblock
                            try:
                                skip_to = float(segment["end"])
                                xbmc.log(f"TA SponsorBlock: Initiating seek to {skip_to:.1f}s", xbmc.LOGINFO)

                                player.seekTime(skip_to)

                                # Wait for seek to complete before continuing monitoring
                                # Give Kodi time to process the seek and stabilize the stream
                                xbmc.sleep(SEEK_STABILIZATION_MS)

                                # Wait until player reports a position close to where we seeked
                                # This ensures the seek actually completed
                                for _ in range(SEEK_VERIFICATION_ATTEMPTS):
                                    try:
                                        current = player.getTime()
                                        if abs(current - skip_to) < SEEK_TOLERANCE_SECONDS:
                                            xbmc.log(f"TA SponsorBlock: Seek completed, now at {current:.1f}s", xbmc.LOGINFO)
                                            break
                                    except Exception as e:
                                        xbmc.log(f"TA SponsorBlock: Error verifying seek: {e}", xbmc.LOGDEBUG)
                                    xbmc.sleep(100)

                                xbmc.log(f"TA SponsorBlock: Skip completed. Will not skip this segment again.", xbmc.LOGINFO)

                            except Exception as e:
                                xbmc.log(f"TA SponsorBlock: seek failed: {e}", xbmc.LOGWARNING)

                        elif action == "mute":
                            # Mute audio and remember when to unmute
                            player.setMuted(True)
                            muted_until = int(segment["end"])
                            xbmc.log(f"TA SponsorBlock: Muted until {muted_until}s. Will not mute this segment again.", xbmc.LOGINFO)
                    else:
                        xbmc.log(f"TA SponsorBlock: Notification-only mode - showing {category} segment", xbmc.LOGINFO)

        except Exception as e:
            xbmc.log(f"TA play: error during playback monitoring: {e}", xbmc.LOGDEBUG)
        xbmc.sleep(PLAYBACK_POLL_INTERVAL_MS)

    # 5) Decide what to send to TubeArchivist using cached values
    pos = int(last_pos or 0)
    dur = int(last_dur or 0)

    # Consider "finished" if threshold is met
    finished = False
    if dur > 0:
        remaining = max(0, dur - pos)
        finished = (pos >= int(FINISHED_THRESHOLD_PERCENT / 100.0 * dur)) or (remaining <= FINISHED_REMAINING_SECONDS)

    xbmc.log(f"TA play: end id={video_id} pos={pos}s dur={dur}s finished={int(finished)}", xbmc.LOGINFO)

    try:
        if finished and dur > 0:
            _ta_mark_watched(video_id, True)
            # Optional: clear progress on completion
            # _ta_mark_progress(video_id, 0)
        else:
            # Only post real progress (skip tiny or zero positions)
            if pos >= MIN_PROGRESS_SECONDS:
                _ta_mark_progress(video_id, pos)
            else:
                xbmc.log(f"TA play: skipping progress <{MIN_PROGRESS_SECONDS}s for {video_id}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"TA play: failed to sync TA for {video_id}: {e}", xbmc.LOGWARNING)

def handle_play_all():
    """Handle play_all action - fetch all videos from list and start playlist."""
    list_id = params.get("list_id")
    if not list_id:
        xbmc.log("TubeArchivist: No list_id for play_all", xbmc.LOGWARNING)
        return

    xbmc.log(f"TubeArchivist: play_all for list {list_id}", xbmc.LOGINFO)

    # Parse list_id (format: "channel:ID" or "playlist:ID")
    parts = list_id.split(":", 1)
    if len(parts) != 2:
        xbmc.log(f"TubeArchivist: Invalid list_id format: {list_id}", xbmc.LOGERROR)
        return

    list_type, entity_id = parts

    # Fetch all videos from the list
    if list_type == "channel":
        all_videos = fetch_all_pages(f"video/?channel={entity_id}")
    elif list_type == "playlist":
        all_videos = fetch_all_pages(f"video/?playlist={entity_id}")
    else:
        xbmc.log(f"TubeArchivist: Unknown list_type: {list_type}", xbmc.LOGERROR)
        return

    # Sort locally
    sort_choice = get_sort_order()
    all_videos = _local_sort(all_videos, sort_choice)

    # Play all videos
    play_all_videos(all_videos, start_index=0)

def handle_play_from_here():
    """Handle play_from_here action - fetch all videos and start from specified index."""
    list_id = params.get("list_id")
    index = params.get("index")

    if not list_id or index is None:
        xbmc.log("TubeArchivist: Missing list_id or index for play_from_here", xbmc.LOGWARNING)
        return

    try:
        start_index = int(index)
    except ValueError:
        xbmc.log(f"TubeArchivist: Invalid index: {index}", xbmc.LOGERROR)
        return

    xbmc.log(f"TubeArchivist: play_from_here for list {list_id}, index {start_index}", xbmc.LOGINFO)

    # Parse list_id (format: "channel:ID" or "playlist:ID")
    parts = list_id.split(":", 1)
    if len(parts) != 2:
        xbmc.log(f"TubeArchivist: Invalid list_id format: {list_id}", xbmc.LOGERROR)
        return

    list_type, entity_id = parts

    # Fetch all videos from the list
    if list_type == "channel":
        all_videos = fetch_all_pages(f"video/?channel={entity_id}")
    elif list_type == "playlist":
        all_videos = fetch_all_pages(f"video/?playlist={entity_id}")
    else:
        xbmc.log(f"TubeArchivist: Unknown list_type: {list_type}", xbmc.LOGERROR)
        return

    # Sort locally
    sort_choice = get_sort_order()
    all_videos = _local_sort(all_videos, sort_choice)

    # Play from specified index
    play_all_videos(all_videos, start_index=start_index)

if __name__ == "__main__":
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    action = params.get("action")
    page = int(params.get("page", 1))

    if action is None:
        root_menu()
    elif action == "list_channels":
        list_channels()
    elif action == "list_playlists":
        list_playlists(page)
    elif action == "list_channel_videos":
        list_channel_videos(params["id"], int(params.get("page", "1")))
    elif action == "list_channel_playlists":
        list_channel_playlists(params["id"])
    elif action == "list_playlist_videos":
        list_playlist_videos(params["id"], int(params.get("page", "1")))
    elif action == "list_partial_videos":
        list_partial_videos(int(params.get("page", "1")))
    elif action == "list_videos":
        list_videos(page)
    elif action == "search_menu":
        search_menu()
    elif action == "search":
        search_type = params.get("type", "all")
        search(int(params.get("page", "1")), search_type)
    elif action == "play_all":
        handle_play_all()
    elif action == "play_from_here":
        handle_play_from_here()
    elif action == "play":
        handle_play()
        sys.exit(0)


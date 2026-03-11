import sys
import xbmcplugin
import xbmcgui
from resources.lib.tubearchivist import TubeArchivist
import xbmcaddon
import xbmc
import math
import urllib.parse
import threading

addon = xbmcaddon.Addon()
MAX_VIDEOS = int(addon.getSetting("max_videos") or 50)

HANDLE = int(sys.argv[1])
ta = TubeArchivist()

def build_url(query):
    return sys.argv[0] + "?" + urllib.parse.urlencode(query)

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

def get_sort_order():
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

def _apply_playback_meta(li, v):
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
    if not watched and (percent is not None and percent >= 95):
        watched = True

    # Apply playcount (the watched tick)
    info.setPlaycount(1 if watched else 0)

    # Last played (optional)
    wd = p.get("watched_date")
    if isinstance(wd, (int, float)) and wd > 0:
        try:
            import datetime as _dt
            info.setLastPlayed(_dt.datetime.utcfromtimestamp(int(wd)).strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

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
    all_channels = []
    cur = 1

    # Pull every page from /api/channel/
    while True:
        data = ta.get(f"channel/?page={cur}")
        items = data.get("data", [])
        if not items:
            break
        all_channels.extend(items)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

    # Sort channels A→Z by name (case-insensitive)
    all_channels.sort(key=lambda c: (c.get("channel_name") or "").lower())

    # Render
    for ch in all_channels:
        label  = ch.get("channel_name", "Unknown Channel")
        thumb  = ta.server_url + (ch.get("channel_thumb_url") or "")
        banner = ta.server_url + (ch.get("channel_banner_url") or "")
        fanart = ta.server_url + (ch.get("channel_tvart_url") or "")
        ch_id  = ch.get("channel_id")

        li = xbmcgui.ListItem(label=label)
        li.setArt({"thumb": thumb, "icon": thumb, "banner": banner, "fanart": fanart})

        # Add context menu for playlists
        context_menu = []
        if ch_id:
            playlists_url = build_url({"action": "list_channel_playlists", "id": ch_id})
            context_menu.append(("View Channel Playlists", f"Container.Update({playlists_url})"))
            li.addContextMenuItems(context_menu)

        url = build_url({"action": "list_channel_videos", "id": ch_id})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)
    
def list_playlists(page=1, update_listing=False):
    """
    List all playlists using API pagination.
    Fetches only the requested page for better performance.

    Args:
        page: Page number to fetch
        update_listing: If True, append to existing list instead of replacing
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
        # API returns playlist_thumbnail, not playlist_thumb_url
        thumb = ta.fix_url(pl.get("playlist_thumbnail") or pl.get("playlist_thumb_url"))
        li = xbmcgui.ListItem(label=label)
        if thumb:
            li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
        url = build_url({"action": "list_playlist_videos", "id": pl.get("playlist_id")})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Navigation - Load More button that appends items
    if current_page < last_page:
        load_more_url = build_url({
            "action": "list_playlists",
            "page": str(current_page + 1),
            "update": "1"
        })
        load_more_label = f"[Load More... ({current_page + 1}/{last_page}) - {total_hits} total playlists]"
        xbmcplugin.addDirectoryItem(HANDLE, load_more_url, xbmcgui.ListItem(label=load_more_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, updateListing=update_listing)

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


def list_channel_videos(channel_id, page=1, update_listing=False):
    """
    List videos for a channel with client-side sorting and pagination.

    Args:
        channel_id: The channel ID
        page: Page number to display
        update_listing: Not used, kept for compatibility
    """
    sort_choice = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    # 1) Pull ALL pages from TA for this channel (server-side sort is unreliable here)
    all_videos, cur = [], 1
    while True:
        data = ta.get(f"video/?channel={channel_id}&page={cur}")
        vids = data.get("data", [])
        if not vids:
            break
        all_videos.extend(vids)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

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
        title = video.get("title", "Untitled")
        desc  = video.get("description", "")
        thumb = ta.server_url + (video.get("vid_thumb_url") or "")
        play = f"{sys.argv[0]}?action=play&yid={video.get('youtube_id')}"
        ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

        li = xbmcgui.ListItem(label=title)
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

        # Add playlists menu
        if ch_id:
            channel_playlists_url = build_url({"action": "list_channel_playlists", "id": ch_id})
            context_menu.append(("View Channel Playlists", f"Container.Update({channel_playlists_url})"))

        if context_menu:
            li.addContextMenuItems(context_menu)

        xbmcplugin.addDirectoryItem(HANDLE, play, li, isFolder=False)

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



def list_channel_playlists(channel_id):
    """List all playlists for a specific channel."""
    # Fetch all playlists and filter by channel
    all_playlists = []
    cur = 1

    while True:
        data = ta.get(f"playlist/?page={cur}")
        items = data.get("data", [])
        if not items:
            break
        all_playlists.extend(items)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

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

def list_playlist_videos(playlist_id, page=1, update_listing=False):
    """
    List videos for a playlist with client-side sorting and pagination.

    Args:
        playlist_id: The playlist ID
        page: Page number to display
        update_listing: Not used, kept for compatibility
    """
    sort_choice = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    # 1) Pull ALL pages from TA for this playlist
    all_videos, cur = [], 1
    while True:
        data = ta.get(f"video/?playlist={playlist_id}&page={cur}")
        vids = data.get("data", [])
        if not vids:
            break
        all_videos.extend(vids)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

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
        title = video.get("title", "Untitled")
        desc  = video.get("description", "")
        thumb = ta.server_url + (video.get("vid_thumb_url") or "")
        play = f"{sys.argv[0]}?action=play&yid={video.get('youtube_id')}"
        ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

        li = xbmcgui.ListItem(label=title)
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
            "action": "list_playlist_videos",
            "id": playlist_id,
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def list_partial_videos(page=1, update_listing=False):
    """
    List in-progress videos with client-side sorting and pagination.

    Args:
        page: Page number to display
        update_listing: Not used, kept for compatibility
    """
    sort_choice = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    # 1) Pull ALL pages from TA
    all_videos, cur = [], 1
    while True:
        data = ta.get(f"video/?watch=continue&page={cur}")
        vids = data.get("data", [])
        if not vids:
            break
        all_videos.extend(vids)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

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
        title = video.get("title", "Untitled")
        desc  = video.get("description", "")
        thumb = ta.server_url + (video.get("vid_thumb_url") or "")
        play = f"{sys.argv[0]}?action=play&yid={video.get('youtube_id')}"
        ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

        li = xbmcgui.ListItem(label=title)
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
            "action": "list_partial_videos",
            "page": str(current_page + 1)
        })
        next_label = f"Next Page ({current_page + 1}/{last_page}) →"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=next_label), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)

def list_videos(page=1, update_listing=False):
    """
    List recent videos with client-side sorting and pagination.

    Args:
        page: Page number to display
        update_listing: Not used, kept for compatibility
    """
    sort_choice = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    xbmcplugin.setContent(HANDLE, "videos")

    # 1) Pull ALL pages from TA
    all_videos, cur = [], 1
    while True:
        data = ta.get(f"video/?page={cur}")
        vids = data.get("data", [])
        if not vids:
            break
        all_videos.extend(vids)
        paginate = data.get("paginate") or {}
        last_page = int(paginate.get("last_page", cur))
        if cur >= last_page:
            break
        cur += 1

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
        play = f"{sys.argv[0]}?action=play&yid={video.get('youtube_id')}"
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
            title = video.get("title", "Untitled")
            desc  = video.get("description", "")
            thumb = ta.server_url + (video.get("vid_thumb_url") or "")
            yid   = video.get('youtube_id')
            play  = build_url({"action": "play", "yid": yid})
            ch_id = video.get("channel", {}).get("channel_id") if isinstance(video.get("channel"), dict) else None

            li = xbmcgui.ListItem(label=title)
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
def _ta_mark_progress(video_id: str, position: int):
    """POST /api/video/{video_id}/progress/  ->  {"position": <seconds>}"""
    try:
        ta.post(f"video/{video_id}/progress/", {"position": int(position)})
    except Exception as e:
        xbmc.log(f"TA progress update failed for {video_id}: {e}", xbmc.LOGWARNING)

def _ta_mark_watched(video_id: str, is_watched: bool = True):
    """POST /api/watched/  ->  {"id": <video_id>, "is_watched": true|false}"""
    try:
        ta.post("watched/", {"id": video_id, "is_watched": bool(is_watched)})
    except Exception as e:
        xbmc.log(f"TA watched toggle failed for {video_id}: {e}", xbmc.LOGWARNING)
# ----------------------------------------


def handle_play():
    yid = params.get("yid")
    if not yid:
        return

    # 1) Fetch fresh video object (so media_url is up to date)
    try:
        data  = ta.get(f"video/{yid}/")
        video = (data.get("data") or data) if isinstance(data, dict) else {}
    except Exception as e:
        xbmc.log(f"TA play: failed to fetch video {yid}: {e}", xbmc.LOGWARNING)
        video = {}

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

    xbmcplugin.setResolvedUrl(HANDLE, True, li)

    # 3) Track playback with cached last-known pos/dur
    player  = xbmc.Player()
    monitor = xbmc.Monitor()

    # Wait up to ~15s for playback to actually start
    started = False
    for _ in range(150):
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

    # Poll once per second while playing, caching position/duration
    while not monitor.abortRequested() and player.isPlayingVideo():
        try:
            pos = int(player.getTime() or 0)
            dur = int(player.getTotalTime() or 0)
            if pos >= 0:
                last_pos = pos
            if dur > 0:
                last_dur = dur
        except Exception:
            pass
        xbmc.sleep(1000)

    # 4) Decide what to send to TubeArchivist using cached values
    pos = int(last_pos or 0)
    dur = int(last_dur or 0)

    # Consider “finished” if >=90% watched or <=60s remaining
    finished = False
    if dur > 0:
        remaining = max(0, dur - pos)
        finished = (pos >= int(0.90 * dur)) or (remaining <= 60)

    xbmc.log(f"TA play: end id={yid} pos={pos}s dur={dur}s finished={int(finished)}", xbmc.LOGINFO)

    try:
        if finished and dur > 0:
            _ta_mark_watched(yid, True)
            # Optional: clear progress on completion
            # _ta_mark_progress(yid, 0)
        else:
            # Only post real progress (skip tiny or zero positions)
            if pos >= 5:
                _ta_mark_progress(yid, pos)
            else:
                xbmc.log(f"TA play: skipping progress <5s for {yid}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"TA play: failed to sync TA for {yid}: {e}", xbmc.LOGWARNING)

if __name__ == "__main__":
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    action = params.get("action")
    page = int(params.get("page", 1))

    if action is None:
        root_menu()
    elif action == "list_channels":
        list_channels()
    elif action == "list_playlists":
        update_listing = params.get("update") == "1"
        list_playlists(page, update_listing)
    elif action == "list_channel_videos":
        update_listing = params.get("update") == "1"
        list_channel_videos(params["id"], int(params.get("page", "1")), update_listing)
    elif action == "list_channel_playlists":
        list_channel_playlists(params["id"])
    elif action == "list_playlist_videos":
        update_listing = params.get("update") == "1"
        list_playlist_videos(params["id"], int(params.get("page", "1")), update_listing)
    elif action == "list_partial_videos":
        update_listing = params.get("update") == "1"
        list_partial_videos(int(params.get("page", "1")), update_listing)
    elif action == "list_videos":
        update_listing = params.get("update") == "1"
        list_videos(page, update_listing)
    elif action == "search_menu":
        search_menu()
    elif action == "search":
        search_type = params.get("type", "all")
        search(int(params.get("page", "1")), search_type)
    elif action == "play":
        handle_play()
        sys.exit(0)


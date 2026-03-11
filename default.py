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
        ("Search", {"action": "search"}),
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
    
def list_playlists(page=1):
    data = ta.paged("playlist/", page)
    items = data.get("data", [])
    for pl in items:
        label = pl.get("playlist_name", "Unnamed Playlist")
        # API returns playlist_thumbnail, not playlist_thumb_url
        thumb = ta.fix_url(pl.get("playlist_thumbnail") or pl.get("playlist_thumb_url"))
        li = xbmcgui.ListItem(label=label)
        if thumb:
            li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
        url = build_url({"action": "list_playlist_videos", "id": pl.get("playlist_id")})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    paginate = data.get("paginate", {})
    if paginate.get("next_pages"):
        next_page = paginate["next_pages"][0]
        li = xbmcgui.ListItem(label=f"Next Page → ({next_page})")
        url = build_url({"action": "list_playlists", "page": str(next_page)})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

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


def list_channel_videos(channel_id, page=1):
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

    # 3) Client-side paginate based on Max videos
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * max_per_page, (page - 1) * max_per_page + max_per_page
    page_slice = all_videos[start:end]

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

    # 5) Navigation
    if page < total_pages:
        next_url = f"{sys.argv[0]}?action=list_channel_videos&id={channel_id}&page={page+1}"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=f">> Next Page ({page+1}/{total_pages})"), isFolder=True)
    if page > 1:
        prev_url = f"{sys.argv[0]}?action=list_channel_videos&id={channel_id}&page={page-1}"
        xbmcplugin.addDirectoryItem(HANDLE, prev_url, xbmcgui.ListItem(label=f"<< Previous Page ({page-1}/{total_pages})"), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)



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

def list_playlist_videos(playlist_id, page=1):
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

    # 3) Client-side paginate based on Max videos
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * max_per_page, (page - 1) * max_per_page + max_per_page
    page_slice = all_videos[start:end]

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

    # 5) Navigation
    if page < total_pages:
        next_url = f"{sys.argv[0]}?action=list_playlist_videos&id={playlist_id}&page={page+1}"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=f">> Next Page ({page+1}/{total_pages})"), isFolder=True)
    if page > 1:
        prev_url = f"{sys.argv[0]}?action=list_playlist_videos&id={playlist_id}&page={page-1}"
        xbmcplugin.addDirectoryItem(HANDLE, prev_url, xbmcgui.ListItem(label=f"<< Previous Page ({page-1}/{total_pages})"), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)

def list_partial_videos(page=1):
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

    # 3) Client-side paginate based on Max videos
    total = len(all_videos)
    total_pages = max(1, math.ceil(total / max_per_page))
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * max_per_page, (page - 1) * max_per_page + max_per_page
    page_slice = all_videos[start:end]

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

    # 5) Navigation
    if page < total_pages:
        next_url = f"{sys.argv[0]}?action=list_videos&watch=continue&page={page+1}"
        xbmcplugin.addDirectoryItem(HANDLE, next_url, xbmcgui.ListItem(label=f">> Next Page ({page+1}/{total_pages})"), isFolder=True)
    if page > 1:
        prev_url = f"{sys.argv[0]}?action=list_videos&watch=continue&page={page-1}"
        xbmcplugin.addDirectoryItem(HANDLE, prev_url, xbmcgui.ListItem(label=f"<< Previous Page ({page-1}/{total_pages})"), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)

def list_videos(page=1):
    sort_choice   = get_sort_order()
    max_per_page  = int(addon.getSetting("max_videos") or 50)
    block_target  = 250  # ~items per fetch block
    want_start    = (page - 1) * max_per_page
    want_end      = want_start + max_per_page

    xbmcplugin.setContent(HANDLE, "videos")

    all_videos = []
    cur_page   = 1

    # First page (also learn page_size/last_page)
    # Try to ask the API nicely for a larger page size if it supports it.
    # If TA ignores unknown params, this is harmless.
    first = ta.get(f"video/?page=1&sort=published&page_size={block_target}")
    vids  = first.get("data", []) or []
    all_videos.extend(vids)

    paginate  = first.get("paginate") or {}
    page_size = int(paginate.get("page_size", len(vids) or 12))
    last_page = int(paginate.get("last_page", 1))

    # Compute how many API pages roughly make ~250 items
    pages_per_block = max(1, math.ceil(block_target / max(1, page_size)))

    # If we don’t yet have enough items to cover the current Kodi page,
    # fetch additional blocks (each ~250 items) until we do, or we run out.
    while len(all_videos) < want_end and cur_page < last_page:
        # Fetch up to pages_per_block pages per loop
        for _ in range(pages_per_block):
            cur_page += 1
            if cur_page > last_page:
                break
            data = ta.get(f"video/?sort=published&page={cur_page}")
            all_videos.extend(data.get("data", []) or [])

        # Update page_size/last_page if the API tells us different numbers mid-run
        paginate  = data.get("paginate") or paginate
        page_size = int(paginate.get("page_size", page_size))
        last_page = int(paginate.get("last_page", last_page))

    # Local sort the fetched window, then slice to the requested Kodi page.
    # NOTE: If you need globally perfect sort across ALL items, you must
    # fetch everything; this is a fast/partial strategy for speed.
    all_videos = _local_sort(all_videos, sort_choice)

    page_slice = all_videos[want_start:want_end]

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

    # Navigation (we can still show Next/Prev based on what *Kodi* page the user is on)
    # We won’t know the true total without crawling all pages; instead, be pragmatic:
    # show "Next" if API says there are more server pages past what we’ve consumed.
    # If you prefer the old page-count UI, keep your total_pages math and full crawl.
    have_more_server_pages = (cur_page < last_page) or (len(all_videos) >= want_end and cur_page <= last_page)
    if have_more_server_pages:
        next_url = f"{sys.argv[0]}?action=list_videos&page={page+1}"
        xbmcplugin.addDirectoryItem(HANDLE, next_url,
            xbmcgui.ListItem(label=f">> Next Page"), isFolder=True)
    if page > 1:
        prev_url = f"{sys.argv[0]}?action=list_videos&page={page-1}"
        xbmcplugin.addDirectoryItem(HANDLE, prev_url,
            xbmcgui.ListItem(label=f"<< Previous Page"), isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)
    
# def list_videos(page=1):
#     data = ta.paged("video/", page)
#     items = data.get("data", [])
#     for v in items:
#         play_video_item(v)

#     # Pagination
#     paginate = data.get("paginate", {})
#     if paginate.get("next_pages"):
#         next_page = paginate["next_pages"][0]
#         li = xbmcgui.ListItem(label=f"Next Page → ({next_page})")
#         url = build_url({"action": "list_videos", "page": str(next_page)})
#         xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

#     xbmcplugin.endOfDirectory(HANDLE)


def play_video_item(video):
    title = video.get("title") or video.get("video_title", "Untitled")
    plot = video.get("description") or video.get("video_description", "")
    thumb = ta.fix_url(video.get("thumbnail_url") or video.get("video_thumb_url"))
    url = ta.fix_url(video.get("url") or video.get("media_url") or video.get("video_url", ""))

    li = xbmcgui.ListItem(label=title)
    li.setInfo("video", {"title": title, "plot": plot})
    if thumb:
        li.setArt({"thumb": thumb, "icon": thumb, "fanart": thumb})
    li.setProperty("IsPlayable", "true")

    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)

def _get_first(d, *keys, default=None):
    """Safe helper: return first existing key from d."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default

def search(page=1):
    # read query from URL or prompt
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
    q = params.get("q")
    if not q:
        q = xbmcgui.Dialog().input("Search Tube Archivist")
    if not q:
        xbmcplugin.endOfDirectory(HANDLE)
        return

    sort_choice  = get_sort_order()
    max_per_page = int(addon.getSetting("max_videos") or 50)

    enc_q = urllib.parse.quote(q)
    try:
        payload = ta.get(f"search/?query={enc_q}&page={page}") or {}
    except Exception as e:
        xbmc.log(f"TubeArchivist: search api error: {e}", xbmc.LOGERROR)
        payload = {}

    results = payload.get("results") or {}
    video_results    = results.get("video_results") or []
    channel_results  = results.get("channel_results") or []
    playlist_results = results.get("playlist_results") or []

    # --- channels (folders) ---
    for ch in channel_results:
        ch_id    = _get_first(ch, "channel_id", "id")
        if not ch_id:
            continue
        ch_name  = _get_first(ch, "channel_name", "title", default="Channel")
        ch_thumb = ta.server_url + (_get_first(ch, "channel_thumb_url", "thumb", default="") or "")
        ch_fan   = ta.server_url + (_get_first(ch, "channel_banner_url", "channel_tvart_url", "fanart", default="") or "")

        url = f'{sys.argv[0]}?action=list_channel_videos&id={urllib.parse.quote(ch_id)}'
        li = xbmcgui.ListItem(label=f"[CHANNEL] {ch_name}")
        li.setArt({"thumb": ch_thumb, "icon": ch_thumb, "fanart": ch_fan})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # --- playlists (folders) ---
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
        url = f'{sys.argv[0]}?action=list_playlist_videos&id={urllib.parse.quote(pl_id)}'
        li = xbmcgui.ListItem(label=f"[PLAYLIST] {pl_name}{suffix}")
        li.setArt({"thumb": pl_thumb, "icon": pl_thumb, "fanart": pl_fan})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # --- videos (items) ---
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

    # optional: if the API ever sends a 'paginate' block, add simple next-page
    paginate = payload.get("paginate") or {}
    next_pages = paginate.get("next_pages") or []
    if next_pages:
        next_page = next_pages[0]
        url = f'{sys.argv[0]}?action=search&q={urllib.parse.quote(q)}&page={next_page}'
        li = xbmcgui.ListItem(label=f"Next page → ({next_page})")
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    if not (channel_results or playlist_results or videos_to_render):
        li = xbmcgui.ListItem(label=f"No results for: {q}")
        xbmcplugin.addDirectoryItem(HANDLE, "", li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)

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
    elif action == "search":
        search(int(params.get("page", "1")))
    elif action == "play":
        handle_play()
        sys.exit(0)


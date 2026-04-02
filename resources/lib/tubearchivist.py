import json
import urllib.request
import urllib.parse
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmc
import sys
import threading
from typing import Optional, Dict, Any

addon = xbmcaddon.Addon()

# Network timeout in seconds
DEFAULT_TIMEOUT = 20

class TubeArchivist:
    def __init__(self):
        self.server_url = addon.getSetting("server_url").rstrip("/")
        self.base_url = self.server_url + "/api/"
        self.token = addon.getSetting("api_token")

    def fix_url(self, path):
        """Convert relative Tube Archivist URLs into absolute URLs"""
        if path and path.startswith("/"):
            return self.server_url + path
        return path

    def get(self, endpoint: str, params: Optional[Dict] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """
        Perform GET request to TubeArchivist API.

        Args:
            endpoint: API endpoint path
            params: Optional query parameters
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            urllib.error.URLError: On network errors
            json.JSONDecodeError: On invalid JSON response
        """
        url = self.base_url + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url)
        if self.token:
            req.add_header("Authorization", f"Token {self.token}")

        xbmc.log(f"TubeArchivist API GET {url}", xbmc.LOGINFO)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                xbmc.log(f"TubeArchivist API response: {json.dumps(data)[:100]}", xbmc.LOGDEBUG)
                return data
        except urllib.error.URLError as e:
            xbmc.log(f"TubeArchivist API GET error {url}: {e}", xbmc.LOGERROR)
            raise
        except json.JSONDecodeError as e:
            xbmc.log(f"TubeArchivist API GET invalid JSON {url}: {e}", xbmc.LOGERROR)
            raise

    def post(self, endpoint: str, data: Optional[Dict] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """
        Perform POST request to TubeArchivist API.

        Args:
            endpoint: API endpoint path
            data: Optional request body data
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response (empty dict on no content or error)
        """
        url = self.base_url + endpoint
        body = json.dumps(data or {}).encode("utf-8")

        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Token {self.token}")

        xbmc.log(f"TubeArchivist API POST {url} {body}", xbmc.LOGINFO)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8").strip()
                if not raw:  # TA may return 204/empty on success
                    xbmc.log("TubeArchivist API response: <empty>", xbmc.LOGDEBUG)
                    return {}
                response = json.loads(raw)
                xbmc.log(f"TubeArchivist API response: {json.dumps(response)}", xbmc.LOGDEBUG)
                return response
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            xbmc.log(f"TubeArchivist API POST error {url}: {e.code} {e.reason} {msg}", xbmc.LOGERROR)
        except urllib.error.URLError as e:
            xbmc.log(f"TubeArchivist API POST network error {url}: {e}", xbmc.LOGERROR)
        except json.JSONDecodeError as e:
            xbmc.log(f"TubeArchivist API POST invalid JSON {url}: {e}", xbmc.LOGERROR)
        except Exception as e:
            xbmc.log(f"TubeArchivist API POST unexpected error {url}: {e}", xbmc.LOGERROR)
        return {}    

    def sort_param(self):
        """Map settings choice to Tube Archivist API sort_by param"""
        try:
            choice = int(addon.getSetting("sort_order") or 0)
        except ValueError:
            choice = 0

        mapping = {
            0: "-published",   # newest first
            1: "published",    # oldest first
            2: "title",        # alphabetical A-Z
            3: "-title",       # Z-A
        }
        return mapping.get(choice, "-published")

    def paged(self, endpoint, page=1, params=None):
        params = params or {}
        params["page"] = page

        if endpoint.startswith("video"):
            sort = self.sort_param()
            if "channel" in params or "playlist" in params:
                # TA quirk: channel/playlist videos need `ordering`
                params["ordering"] = sort
            else:
                # global video listing works with `sort_by`
                params["sort_by"] = sort

        return self.get(endpoint, params)


    def add_pagination(self, data, action, handle=None, extra_params=None):
        """Add Next/Prev page buttons for Kodi"""
        paginate = data.get("paginate") or {}
        current = int(paginate.get("current_page", 1))
        last = int(paginate.get("last_page", 1))
        handle = handle or int(sys.argv[1])
        extra_params = extra_params or {}

        if current < last:
            next_page = current + 1
            params = {"action": action, "page": next_page}
            params.update(extra_params)
            url = f"{sys.argv[0]}?{urllib.parse.urlencode(params)}"
            li = xbmcgui.ListItem(label=f"Next Page ({next_page}/{last}) →")
            xbmcplugin.addDirectoryItem(handle, url, li, isFolder=True)

        if current > 1:
            prev_page = current - 1
            params = {"action": action, "page": prev_page}
            params.update(extra_params)
            url = f"{sys.argv[0]}?{urllib.parse.urlencode(params)}"
            li = xbmcgui.ListItem(label=f"← Previous Page ({prev_page}/{last})")
            xbmcplugin.addDirectoryItem(handle, url, li, isFolder=True)

    def sort_videos_locally(self, videos):
        """Sort videos client-side when API ignores sort_by/ordering"""
        sort = self.sort_param()

        reverse = sort.startswith("-")
        key = sort.lstrip("-")

        def sort_key(v):
            if key == "published":
                return v.get("published") or ""
            if key == "title":
                return v.get("title") or v.get("video_title", "")
            return v.get(key, "")

        try:
            return sorted(videos, key=sort_key, reverse=reverse)
        except Exception as e:
            xbmc.log(f"TubeArchivist: local sort failed: {e}", xbmc.LOGERROR)
            return videos

    def videos(self, params=None):
        return self.get("video/", params)

    def channels(self, params=None):
        return self.get("channel/", params)

    def playlists(self, params=None):
        return self.get("playlist/", params)

    def search_videos(self, query):
        return self.videos({"search": query})

# --- playback_tracker.py ---
# NOTE: This class is currently unused. Playback tracking is implemented
# directly in default.py handle_play() function. This class provides a
# cleaner alternative implementation if a refactor is desired.
class TAPlaybackTracker(xbmc.Player):
    def __init__(self, ta):
        super().__init__()
        self.ta = ta
        self._video_id = None
        self._last_pos = 0
        self._duration = 0
        self._track = False
        self._thread = None

    def begin(self, video_id: str):
        """Call this right before you start playback."""
        self._video_id = video_id
        self._last_pos = 0
        self._duration = 0
        self._track = True
        # (Re)start polling thread
        if self._thread and self._thread.is_alive():
            self._track = False
            try: self._thread.join(timeout=0.2)
            except: pass
        self._track = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self):
        while self._track:
            if self.isPlayingVideo():
                try:
                    # cache latest values while playback is alive
                    self._last_pos = int(self.getTime() or 0)
                    tot = self.getTotalTime() or 0
                    if tot: self._duration = int(tot)
                except Exception:
                    pass
            xbmc.sleep(2000)  # every 2s

    def _finish(self, ended: bool):
        vid = self._video_id
        self._track = False
        if not vid:
            return
        pos = int(self._last_pos or 0)
        dur = int(self._duration or 0)

        try:
            # if ended OR >=90% (with 30s floor), mark watched + clear progress
            finished = ended or (dur and pos >= max(30, int(0.9 * dur)))
            if finished:
                self.ta.post("watched/", {"id": vid, "is_watched": True})
                self.ta.post(f"video/{vid}/progress/", {"position": 0})
            else:
                self.ta.post(f"video/{vid}/progress/", {"position": pos})
        except Exception as e:
            xbmc.log(f"TA progress/watched update failed for {vid}: {e}", xbmc.LOGWARNING)
        finally:
            self._video_id = None
            self._last_pos = 0
            self._duration = 0

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

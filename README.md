# Tube Archivist Kodi Addon

A rudimentary Kodi plugin for browsing and watching videos from your [Tube Archivist](https://github.com/tubearchivist/tubearchivist) server.

This was quickly created for my own personal use, and I have released this in the vague hope that it may be of use to somebody else.

This is Alpha quality code and contains more than a couple of bodges and workarounds. 
Here be Dragons, You have been warned.

## Contributions
If anybody wishes to submit a pull request for any bugs or features then I will probably accept assuming it is sane and doesn't go wildy out of scope.

To manage any expectations this is a very minor personal project so responses and development will be sporadic at best and I will not be offering any support,
nor accepting feature requests.

## Features

### Implemented/TODO:
- [X] Play Videos
- [X] Browse
   - [X] Channels
   - [X] Videos
   - [X] Playlists
- [X] Search
   - [X] Playlists
   - [X] Channels
   - [X] Videos
- [X] Sync Progress with TA
   - [X] Play from last position on TA
   - [X] Sync playback to TA
- [X] Subtitles
- [X] Poplate Kodi Video Metadata from TA
- [ ] Playlist enqueuing
- [ ] Non Token based auth e.g Header auth

### Not Planned:
Anything not related to video watching is probably better being done in the webui as Kodi is _really_ not designed for anything else.
This includes:
- Adding/Removing Playlists/Channels/Videos
- Account Management Stuff e.g.
   - Adding/Removing/Modifying Users
   - Cookies/PO Tokens etc.
- Triggering Tasks
- Anything related to TA settings
   - No. Just... _no_

## Setup

1. Install the addon (download repo as a zip and install or use git to clone into addons folder)
2. Go to addon settings and enter:
   - Your Tube Archivist server URL (e.g., `http://localhost:8000`)
   - API token (found in Tube Archivist settings)

## Requirements

- Kodi (Tested on 21 but 19.0 or newer should work)
- A running Tube Archivist server

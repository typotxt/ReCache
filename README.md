# ReCache

ReCache replaces Roblox game assets (textures, sounds, meshes, models, skyboxes) by editing Roblox's own local cache. There is no proxy, no certificate and no FastFlag tricks: it only writes entries into `%LOCALAPPDATA%\\Roblox\\rbx-storage.db`, so it works with the normal Roblox player.

**ReCache is built on** [**Fleasion**](https://github.com/fleasion/Fleasion)**.** Its config format, replacer, scraper, cache viewer, mesh and image conversion and a large part of the interface come from Fleasion and its contributors. Huge credit to them; see [CONTRIBUTORS.md](CONTRIBUTORS.md). ReCache is a modified version and is released under the same license, [GPL-3.0](LICENSE).

## 

## Fork notice

ReCache is a modified version of Fleasion (forked from **Fleasion 2.4.1**, October 2026). Original project: [https://github.com/fleasion/Fleasion](https://github.com/fleasion/Fleasion). What was changed, and when, is listed in [CHANGES.md](CHANGES.md); the full history is in this repository's Git log. Third-party packages and their licenses are in [THIRD\_PARTY\_LICENSES.md](THIRD_PARTY_LICENSES.md).

ReCache is not made by or endorsed by the Fleasion authors, and it is not affiliated with Roblox.

## 

## 

## What it does

* **Replacer:** build configs that swap assets by ID with an ID, a link, an image/sound/mesh/OBJ file on your PC.
* **Apply to Roblox:** writes your enabled configs into the cache. Works while Roblox is open; rejoin to see changes.
* **Game cache:** pick a game with a bundled key list (Phantom Forces) and apply its config without joining it first.
* **Scraper:** clear the cache, join a game, scan, and browse that game's assets.
* **Modifications:** game skyboxes through the cache, plus the Fleasion modification tools.
* **Tools:** reserved server rejoin, multi-instance, account manager, R6 / R15 animation converter.
* **Cache saving mode:** keep an empty cache database, or keep ReCache's entries, when you clear the cache.

## 

## 

## Run from source

Requires Windows, Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run fleasion
```

## 

## Build the executable

```bash
uv run build
```

The `.exe` is written to `dist/`.

## 

## 

## Key lists

A key list maps an asset ID to its cache key so ReCache can write a replacement without the game being loaded first. Lists live in `src/fleasion/cache/key\_lists/` (see the README there) and in `%LOCALAPPDATA%\\Recache\\cache\_key\_lists\\`.

## 

## Releases and source

Every `.exe` published on the Releases page is built from the matching tagged source in this repository, so the corresponding source for any download is that tag. Build it yourself with the steps above.

## 

## 

## 

## Safety

ReCache only edits files in your own Roblox cache. It does not touch the Roblox client or its memory and does not intercept network traffic. It only makes normal web requests to Roblox's asset servers, with your login, to download original assets for matching. Use it at your own risk and follow Roblox's Terms of Use.


# Changes from Fleasion

ReCache is a modified version of [Fleasion](https://github.com/fleasion/Fleasion), forked from **Fleasion 2.4.1** (the state of its source in October 2026). Fleasion is licensed under the GNU General Public License v3.0 and so is ReCache. This file is the notice of modification required by GPL-3.0 section 5(a): the files under `src/` were changed from the originals as described below, and the full line-by-line history is in this repository's Git log.

Original project and authors: https://github.com/fleasion/Fleasion (see [CONTRIBUTORS.md](CONTRIBUTORS.md)).

## 1.0.0 (2026-10-22 to 2026-10-23)

### Replacement engine
- **Added:** `src/fleasion/cache/roblox_cache_writer.py`. Replacements are written straight into Roblox's local cache (`rbx-storage.db` and `rbx-storage/`) instead of being served by Fleasion's local proxy. Includes cache-record (RBXH) reading and writing, key lists, original-asset matching, image to KTX2 and OBJ to mesh conversion, removal placeholders, and restoring earlier writes.
- **Added:** bundled key lists (`src/fleasion/cache/key_lists/`) so assets can be replaced without joining the game first.
- **Changed:** the proxy is never started. Proxy modes, the Proxy and Subplace Joiner tabs, the username spoofer, the subplace blacklist and the updater are removed from the interface (the proxy code itself is still in the source, unused).
- **Added:** manual **Apply to Roblox**, a quiet catch-up for newly loaded assets, a clickable result summary listing what failed, **Game cache** (apply one game's config), and a **Cache saving mode** for Clear Cache (keep an empty database, or keep ReCache's entries).

### Interface
- **Rewritten:** the main window, tabs, header, footer command bar and global stylesheet; Scraper now scans Roblox's cache; Modifications gained game skyboxes through the cache.
- **Added:** light and dark theme with a custom accent colour, toggle switches, and a minimum window size measured from the layout.
- **Rebranded:** name ReCache, new logo, own data folder (`%LOCALAPPDATA%\Recache`), own single-instance and autostart names.

### Kept from Fleasion
- Replacer config format, JSON viewer, cache viewer and exports, audio player, mesh and animation tools, FastFlags editor, rejoin, multi-instance, account manager and R6 / R15 converter.

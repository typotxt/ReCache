# Changes from Fleasion

ReCache is a modified version of [Fleasion](https://github.com/fleasion/Fleasion), forked from **Fleasion 2.4.1** (the state of its source in October 2026). Fleasion is licensed under the GNU General Public License v3.0 and so is ReCache. This file is the notice of modification required by GPL-3.0 section 5(a): the files under `src/` were changed from the originals as described below, and the full line-by-line history is in this repository's Git log.

Original project and authors: https://github.com/fleasion/Fleasion (see [CONTRIBUTORS.md](CONTRIBUTORS.md)).

## 1.0.1 (2026-10-26)

### Interface
- **Redesigned:** top navigation with a centred tab bar (dimmed until hovered), a footer command bar with **Apply to Roblox** and **Clear Cache** on every tab, card layouts for Scraper, Modifications, Tools and Settings, rounded cards and lists, and a new ReCache logo.
- **Added:** light, dark and system themes, a custom accent colour, toggle switches, and a window minimum size measured from the layout so no label or button is clipped.
- **Added:** live "Currently Applying n/total" status while applying, and a clickable result summary listing what failed.

### Cache
- **Added:** **Game cache** in the Replacer: pick a game and **Import** its recorded key list (writes nothing to the Roblox cache); **Apply to Roblox** then writes your changes.
- **Added:** **Cache saving mode** in Settings. *Keep cache database* wipes the cache when Roblox or ReCache closes but leaves an empty database; *Keep ReCache entries* deletes nothing. **Clear Cache** always leaves an empty database so you can write without reopening Roblox.
- **Changed:** Apply uses only the bundled key lists of imported games, plus your own and learned lists.

### Branding
- **Changed:** the application is named **ReCache** everywhere; the data folder stays `%LOCALAPPDATA%\Recache`.

## 1.0.0 (2026-10-22 to 2026-10-23)

### Replacement engine
- **Added:** `src/fleasion/cache/roblox_cache_writer.py`. Replacements are written straight into Roblox's local cache (`rbx-storage.db` and `rbx-storage/`) instead of being served by Fleasion's local proxy. Includes cache-record (RBXH) reading and writing, key lists, original-asset matching, image to KTX2 and OBJ to mesh conversion, removal placeholders, and restoring earlier writes.
- **Added:** bundled key lists (`src/fleasion/cache/key_lists/`) so assets can be replaced without joining the game first.
- **Changed:** the proxy is never started. Proxy modes, the Proxy and Subplace Joiner tabs, the username spoofer, the subplace blacklist and the updater are removed from the interface (the proxy code itself is still in the source, unused).
- **Added:** manual **Apply to Roblox** and a quiet catch-up that applies assets loaded later in game.

### Kept from Fleasion
- Replacer config format, JSON viewer, cache viewer and exports, audio player, mesh and animation tools, FastFlags editor, rejoin, multi-instance, account manager and R6 / R15 converter.

"""Cache mode: apply replacement rules by editing Roblox's local asset cache.

No proxy is involved. Roblox stores every download in ``rbx-storage.db`` (plus
``rbx-storage/<xx>/<key>`` files for larger bodies) as an RBXH record whose
header and body carry XXH32 checksums with a fixed seed. Roblox discards a
record whose checksum does not match, so every write recomputes it.

Cache keys cannot be derived from asset IDs, so each original asset is
downloaded once (with the user's own login, as Fleasion already does) and
located in the cache by content. Asset-type rules are not supported yet.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from ..utils import log_buffer
from ..utils.paths import APP_CACHE_DIR, CONFIG_DIR, STORAGE_DB

ROBLOX_DIR = STORAGE_DB.parent
BLOB_DIR = ROBLOX_DIR / 'rbx-storage'
BACKUP_DIR = CONFIG_DIR / 'cache_mode_backups'
FINGERPRINTS_FILE = APP_CACHE_DIR / 'cache_mode_fingerprints.json'
DOWNLOAD_DIR = APP_CACHE_DIR / 'cache_mode_downloads'
# Cache keys are identical on every PC and account, so asset ID -> key lists
# learned from one warmed-up cache let others insert entries without playing.
KEY_LISTS_DIR = CONFIG_DIR / 'cache_key_lists'
LEARNED_KEY_LIST = KEY_LISTS_DIR / 'learned.json'
INSERTED_SUFFIX = '.inserted'
CONTENT_CATEGORY = 10
RBXH_CHECKSUM_SEED = 0x00C4E564
USER_AGENT = 'Roblox/WinInet'

# Representations the Player requests from assetdelivery (decoded from its
# logs). None is the default representation.
REPRESENTATIONS = [
    None,
    [{'format': 'ktx2', 'majorVersion': '7rdo', 'fidelity': '/////AE='}],
    [{'format': 'fmd', 'majorVersion': '1.0.1', 'fidelity': 'ALL'}],
    [{'format': 'Residual', 'majorVersion': '2', 'fidelity': 'Original'}],
]

SIGNATURES = [
    (b'RIFF', '.webp'),
    (b'\x89PNG', '.png'),
    (b'\xff\xd8\xff', '.jpg'),
    (b'\xabKTX 20', '.ktx2'),
    (b'OggS', '.ogg'),
    (b'ID3', '.mp3'),
    (b'\xff\xfb', '.mp3'),
    (b'<roblox!', '.rbxm'),
    (b'<roblox', '.rbxmx'),
    (b'version ', '.mesh'),
]

# Bundled placeholder used when a rule removes an asset, by cached file type.
EMPTY_BY_KIND = {
    '.ogg': 'empty.ogg',
    '.mp3': 'empty.mp3',
    '.mesh': 'empty.mesh',
}
# Removed images need a transparent pixel; the bundled empty.tex is opaque black
IMAGE_SLOT_KINDS = ('.png', '.webp', '.jpg', '.ktx2')

_apply_lock = threading.Lock()


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one Cache mode apply."""

    summary: str  # full one-liner for the log
    short: str  # compact text shown next to the Apply button
    ok: bool
    applied: int = 0
    pending: int = 0  # rules not applied yet because the asset is not loaded/downloadable
    # (heading, rule names) groups shown when the result text is clicked
    details: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _problem(message: str) -> ApplyResult:
    return ApplyResult(message, message, ok=False)


_P1, _P2, _P3, _P4, _P5 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D, 0x27D4EB2F, 0x165667B1
_M32 = 0xFFFFFFFF


def _rotl(x: int, r: int) -> int:
    return ((x << r) | (x >> (32 - r))) & _M32


def xxh32(data: bytes, seed: int = RBXH_CHECKSUM_SEED) -> int:
    """XXH32, which RBXH records use (with a fixed seed) for their checksums."""
    n, i = len(data), 0
    if n >= 16:
        v = [(seed + _P1 + _P2) & _M32, (seed + _P2) & _M32, seed, (seed - _P1) & _M32]
        lanes = struct.unpack_from(f'<{(n // 16) * 4}I', data)
        for j in range(0, len(lanes), 4):
            for k in range(4):
                v[k] = (_rotl((v[k] + lanes[j + k] * _P2) & _M32, 13) * _P1) & _M32
        i = len(lanes) * 4
        h = (_rotl(v[0], 1) + _rotl(v[1], 7) + _rotl(v[2], 12) + _rotl(v[3], 18)) & _M32
    else:
        h = (seed + _P5) & _M32
    h = (h + n) & _M32
    while i + 4 <= n:
        h = (_rotl((h + struct.unpack_from('<I', data, i)[0] * _P3) & _M32, 17) * _P4) & _M32
        i += 4
    while i < n:
        h = (_rotl((h + data[i] * _P5) & _M32, 11) * _P1) & _M32
        i += 1
    h ^= h >> 15
    h = (h * _P2) & _M32
    h ^= h >> 13
    h = (h * _P3) & _M32
    return h ^ (h >> 16)


def parse_rbxh(blob: bytes) -> tuple[bytes, int, bytes, bytes] | None:
    """Split an RBXH record into (url, status, headers, body).

    Layout: 'RBXH', u32 version, u32 url_len, url, u8, then u32 status,
    headers_len, headers_checksum, body_len, body_checksum, reserved,
    followed by the headers and the body.
    """
    if blob[:4] != b'RBXH':
        return None
    (url_len,) = struct.unpack_from('<I', blob, 8)
    offset = 13 + url_len
    status, headers_len, _, body_len, _, _ = struct.unpack_from('<6I', blob, offset)
    offset += 24
    return (
        blob[12 : 12 + url_len],
        status,
        blob[offset : offset + headers_len],
        blob[offset + headers_len : offset + headers_len + body_len],
    )


def body_of(blob: bytes) -> bytes:
    """Return the asset bytes of an RBXH record, or the blob itself if it is raw."""
    record = parse_rbxh(blob)
    return blob if record is None else record[3]


def kind_of(body: bytes) -> str:
    return next((ext for magic, ext in SIGNATURES if body.startswith(magic)), '.bin')


def with_body(blob: bytes, body: bytes) -> bytes:
    """Return an RBXH record with its body replaced and its body checksum recomputed."""
    (url_len,) = struct.unpack_from('<I', blob, 8)
    offset = 13 + url_len
    status, headers_len, headers_ck, _, _, reserved = struct.unpack_from('<6I', blob, offset)
    headers = blob[offset + 24 : offset + 24 + headers_len]
    body_ck = xxh32(body) if body else 0
    fields = struct.pack('<6I', status, headers_len, headers_ck, len(body), body_ck, reserved)
    return blob[:offset] + fields + headers + body


def _blob_path(key: bytes) -> Path:
    return BLOB_DIR / key.hex()[:2] / key.hex()


def read_blob(db: sqlite3.Connection, key: bytes) -> bytes | None:
    row = db.execute('SELECT content FROM files WHERE id = ?', (key,)).fetchone()
    if row is None:
        return None
    if row[0] is not None:
        return row[0]
    path = _blob_path(key)
    return path.read_bytes() if path.is_file() else None


def write_blob(db: sqlite3.Connection, key: bytes, data: bytes) -> None:
    """Write data where the entry already keeps its content (inline or file)."""
    (inline,) = db.execute('SELECT content IS NOT NULL FROM files WHERE id = ?', (key,)).fetchone()
    if inline:
        db.execute('UPDATE files SET content = ?, size = ? WHERE id = ?', (data, len(data), key))
    else:
        path = _blob_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)  # folder may have been cleared
        path.write_bytes(data)
        db.execute('UPDATE files SET size = ? WHERE id = ?', (len(data), key))


def make_record(body: bytes) -> bytes:
    """Build a content RBXH record exactly as Roblox writes one (no URL, no headers, HTTP 200)."""
    fields = struct.pack('<6I', 200, 0, 0, len(body), xxh32(body) if body else 0, 0)
    return b'RBXH' + struct.pack('<II', 2, 0) + b'\x00' + fields + body


def insert_blob(db: sqlite3.Connection, key: bytes, body: bytes) -> int:
    """Create a new content entry, stored as a file like Roblox does for most assets.

    Returns the size recorded for the entry.
    """
    record = make_record(body)
    path = _blob_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(record)
    now = int(time.time() * 1000)
    db.execute(
        'INSERT INTO files (id, content, size, hits, atime, category, score, ttl) '
        'VALUES (?, NULL, ?, 0, ?, ?, ?, 0)',
        (key, len(record), now, CONTENT_CATEGORY, now),
    )
    return len(record)


SCHEMA_FILE = CONFIG_DIR / 'cache_schema.json'
CONFIG_FILE = CONFIG_DIR / 'settings.json'


def _remember_schema(db: sqlite3.Connection) -> None:
    """Keep the cache database layout (and its id file) so an empty one can be recreated."""
    try:
        statements = [
            row[0]
            for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            )
        ]
        id_file = STORAGE_DB.parent / 'rbx-storage.id'
        data = {
            'statements': statements,
            'id': id_file.read_bytes().hex() if id_file.is_file() else '',
        }
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SCHEMA_FILE.write_text(json.dumps(data), encoding='utf-8')
    except (sqlite3.Error, OSError) as exc:
        log_buffer.log('CacheMode', f'Could not remember the cache layout: {exc}')


# Roblox's cache layout, used when no copy of it was remembered yet
_DEFAULT_SCHEMA = [
    'CREATE TABLE files (id BLOB PRIMARY KEY NOT NULL, content BLOB, size INTEGER DEFAULT 0 NOT NULL,'
    'hits INTEGER DEFAULT 0 NOT NULL, atime INTEGER DEFAULT 0 NOT NULL, category INTEGER DEFAULT 0 NOT NULL,'
    ' score INTEGER DEFAULT 0 NOT NULL, ttl INTEGER DEFAULT 0 NOT NULL)',
    'CREATE INDEX files_atime_idx ON files (atime)',
    'CREATE INDEX files_size_idx ON files (size)',
    'CREATE INDEX files_category_idx ON files (category)',
    'CREATE INDEX files_score_idx ON files (score)',
]


def _create_empty_db() -> bool:
    """Recreate an empty rbx-storage.db from the remembered layout."""
    try:
        try:
            data = json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        statements = data.get('statements') or _DEFAULT_SCHEMA
        STORAGE_DB.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(STORAGE_DB, timeout=10)
        try:
            for statement in statements:
                db.execute(statement)
            db.commit()
        finally:
            db.close()
        if data.get('id'):
            (STORAGE_DB.parent / 'rbx-storage.id').write_bytes(bytes.fromhex(data['id']))
        return True
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        log_buffer.log('CacheMode', f'Could not recreate the cache database: {exc}')
        return False


def cache_saving_settings() -> tuple[bool, bool]:
    """(keep database, keep ReCache entries) from settings.json; database defaults on, entries off."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    return bool(data.get('cache_keep_db', False)), bool(data.get('cache_keep_entries', False))


def clear_cache_keeping(delete_fn, force_db: bool = False) -> list[str]:
    """Run ``delete_fn`` (the normal Clear Cache) but honour the cache saving mode.

    Keep database: an empty rbx-storage.db is recreated afterwards.
    Keep entries: entries ReCache wrote are put back after the wipe.
    """
    keep_db, keep_entries = cache_saving_settings()
    keep_db = keep_db or force_db  # the Clear Cache button always leaves a database
    if not (keep_db or keep_entries):
        return delete_fn()
    protected: list[tuple[bytes, bytes, int]] = []
    if STORAGE_DB.is_file():
        try:
            db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
            try:
                _remember_schema(db)
                if keep_entries and BACKUP_DIR.is_dir():
                    for backup in BACKUP_DIR.glob('*'):
                        try:
                            key = bytes.fromhex(backup.name.removesuffix(INSERTED_SUFFIX))
                        except ValueError:
                            continue
                        record = read_blob(db, key)
                        row = db.execute('SELECT category FROM files WHERE id = ?', (key,)).fetchone()
                        if record is not None and row is not None:
                            protected.append((key, record, row[0]))
            finally:
                db.close()
        except sqlite3.Error as exc:
            log_buffer.log('CacheMode', f'Could not read the cache before clearing: {exc}')
    messages = delete_fn()
    if STORAGE_DB.is_file() or not _create_empty_db():
        return messages  # the wipe was aborted, or there is no layout to rebuild from
    messages.append('Empty cache database kept for Roblox')
    if protected:
        db = sqlite3.connect(STORAGE_DB, timeout=10)
        try:
            now = int(time.time() * 1000)
            for key, record, category in protected:
                path = _blob_path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(record)
                db.execute(
                    'INSERT OR REPLACE INTO files (id, content, size, hits, atime, category, score, ttl) '
                    'VALUES (?, NULL, ?, 0, ?, ?, ?, 0)',
                    (key, len(record), now, category, now),
                )
            db.commit()
        finally:
            db.close()
        messages.append(f'Kept {len(protected)} ReCache entries')
    return messages


def restore_backups(db: sqlite3.Connection) -> int:
    """Undo every earlier Cache mode change, so rules can be re-applied cleanly.

    Overwritten entries get their original back; entries Cache mode inserted
    (marked by an empty ``<key>.inserted`` file) are removed again.
    """
    restored = 0
    for backup in BACKUP_DIR.glob('*') if BACKUP_DIR.is_dir() else []:
        name = backup.name.removesuffix(INSERTED_SUFFIX)
        try:
            key = bytes.fromhex(name)
        except ValueError:
            continue
        if backup.name.endswith(INSERTED_SUFFIX):
            db.execute('DELETE FROM files WHERE id = ?', (key,))
            _blob_path(key).unlink(missing_ok=True)
            restored += 1
        elif db.execute('SELECT 1 FROM files WHERE id = ?', (key,)).fetchone():
            write_blob(db, key, backup.read_bytes())
            restored += 1
        backup.unlink()
    return restored


def bundled_key_lists_dir() -> Path:
    """Key lists shipped inside Fleasion (src/fleasion/cache/key_lists)."""
    meipass = getattr(sys, '_MEIPASS', None)
    if getattr(sys, 'frozen', False) and meipass:
        return Path(meipass) / 'fleasion' / 'cache' / 'key_lists'
    return Path(__file__).parent / 'key_lists'


def available_games() -> list[tuple[str, str]]:
    """Games with a key list (bundled or the user's) as (list name, display name)."""
    names = {
        path.stem
        for folder in (bundled_key_lists_dir(), KEY_LISTS_DIR)
        if folder.is_dir()
        for path in folder.glob('*.json')
        if path.stem != 'learned'
    }
    return [(name, name.replace('_', ' ').title()) for name in sorted(names)]


def folder_is_user(path: Path) -> bool:
    return path.parent == KEY_LISTS_DIR


def imported_games() -> set[str]:
    """Games imported in the Replacer (settings.json); only their bundled key lists are used."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    return {str(g) for g in data.get('imported_games', [])}


def key_list_asset_count(game: str) -> int:
    """How many assets a game's recorded key list covers."""
    total = 0
    for folder in (bundled_key_lists_dir(), KEY_LISTS_DIR):
        path = folder / f'{game}.json'
        if path.is_file():
            try:
                total = max(total, len(json.loads(path.read_text(encoding='utf-8')).get('assets', {})))
            except (OSError, ValueError, AttributeError):
                pass
    return total


def load_key_lists(imported_only: bool = False) -> dict[int, list[tuple[bytes, str]]]:
    """Merge key lists into asset ID -> [(key, kind)].

    With ``imported_only`` the bundled lists of games that were not imported are
    skipped; your own lists and what ReCache learned are always used.
    """
    allowed = imported_games() if imported_only else None
    merged: dict[int, dict[bytes, str]] = {}
    paths = [
        path
        for folder in (bundled_key_lists_dir(), KEY_LISTS_DIR)
        if folder.is_dir()
        for path in sorted(folder.glob('*.json'))
        if allowed is None or folder_is_user(path) or path.stem in allowed
    ]
    for path in paths:
        try:
            assets = json.loads(path.read_text(encoding='utf-8')).get('assets', {})
            for asset_id, entries in assets.items():
                for entry in entries:
                    key = bytes.fromhex(entry['key'])
                    if len(key) == 16:
                        merged.setdefault(int(asset_id), {})[key] = str(entry.get('kind', '.bin'))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log_buffer.log('CacheMode', f'Skipping unreadable key list {path.name}: {exc}')
    return {aid: list(keys.items()) for aid, keys in merged.items()}


def _save_learned_keys(learned: dict[int, list[tuple[bytes, str]]]) -> None:
    """Merge newly matched keys into learned.json, the key list players can share."""
    try:
        data = json.loads(LEARNED_KEY_LIST.read_text(encoding='utf-8'))
    except OSError, ValueError:
        data = {}
    assets = data.get('assets', {}) if isinstance(data, dict) else {}
    for asset_id, entries in learned.items():
        known = {e['key'] for e in assets.get(str(asset_id), []) if isinstance(e, dict)}
        for key, kind in entries:
            if key.hex() not in known:
                assets.setdefault(str(asset_id), []).append({'key': key.hex(), 'kind': kind})
    KEY_LISTS_DIR.mkdir(parents=True, exist_ok=True)
    LEARNED_KEY_LIST.write_text(
        json.dumps({'version': 1, 'assets': assets}, indent=1), encoding='utf-8'
    )


def last_joined_place_id() -> str | None:
    """Return the place ID from the newest 'Joining game' line in the Player logs."""
    logs = sorted(
        (ROBLOX_DIR / 'logs').glob('*_Player_*_last.log'), key=os.path.getmtime, reverse=True
    )
    for log in logs[:20]:
        matches = re.findall(r'Joining game .* place (\d+)', log.read_text(errors='replace'))
        if matches:
            return matches[-1]
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _decode(data: bytes, encoding: str | None) -> bytes:
    if encoding == 'gzip' or data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)
    if encoding == 'zstd' or data[:4] == b'\x28\xb5\x2f\xfd':
        import zstandard

        return zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 30)
    return data


def _http_get(url: str) -> bytes:
    # CDN URLs come from shared configs; never let one read local files (file:)
    if not url.lower().startswith(('https://', 'http://')):
        raise OSError(f'unsupported URL scheme: {url[:40]}')
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return _decode(resp.read(), resp.headers.get('Content-Encoding'))


UNAUTHORIZED_STATUSES = (403, 409)
MAX_CREATOR_PLACES = 30
_scraper_instance = None


def _scraper():
    """Fleasion's CacheScraper, used only for its creator -> places lookups."""
    global _scraper_instance
    if _scraper_instance is None:
        from ..proxy.addons.cache_scraper import CacheScraper

        _scraper_instance = CacheScraper(SimpleNamespace(clear_memory_cache=lambda: None))
    return _scraper_instance


def _candidate_places(asset_id: int, place_id: str | None):
    """Yield (place ID, creator ID) pairs to request an asset under.

    The creator lookup only runs if the caller keeps iterating (first place refused).
    """
    yield place_id, None
    scraper = _scraper()
    creator_id, creator_type = scraper._fetch_creator_info(str(asset_id))
    if creator_id is None:
        return
    places = scraper._fetch_place_ids_for_creator(creator_id, creator_type)
    last = scraper._creator_last_success.get(creator_id)
    ordered = ([last] if last in places else []) + [p for p in places if p != last]
    for pid in ordered[:MAX_CREATOR_PLACES]:
        if str(pid) != place_id:
            yield str(pid), creator_id


def _locate(
    opener: urllib.request.OpenerDirector,
    asset_id: int,
    rep: list[dict] | None,
    headers: dict[str, str],
) -> tuple[str | None, int | None]:
    """Ask assetdelivery where a representation lives: (CDN location, HTTP status)."""
    url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
    if rep is not None:
        encoded = base64.b64encode(json.dumps(rep).encode()).decode()
        url += f'&contentRepresentationPriorityList={encoded}'
    try:
        opener.open(urllib.request.Request(url, headers=headers), timeout=30).close()
        return None, 200  # no redirect means no downloadable body for this representation
    except urllib.error.HTTPError as exc:
        redirect = exc.code in (301, 302, 303, 307, 308)
        return (exc.headers.get('Location') if redirect else None), exc.code
    except OSError:
        return None, None


def download_representations(
    asset_id: int, cookie: str, place_id: str | None, *, first_only: bool = False
) -> list[bytes]:
    """Download the representations of an asset the Player may have cached.

    The login cookie only goes to assetdelivery: redirects are not followed
    automatically (urllib would forward the Cookie header), and the CDN hop is
    fetched without it.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    base_headers = {'Cookie': f'.ROBLOSECURITY={cookie}', 'User-Agent': USER_AGENT}
    # Game-restricted assets answer 403 unless the request names a game that may
    # use them: try the last joined game, then the asset creator's games
    headers: dict[str, str] | None = None
    default_location: str | None = None
    for candidate, creator_id in _candidate_places(asset_id, place_id):
        attempt = dict(base_headers)
        if candidate:
            attempt['Roblox-Place-Id'] = candidate
        default_location, status = _locate(opener, asset_id, None, attempt)
        if default_location or status not in UNAUTHORIZED_STATUSES:
            headers = attempt
            if creator_id is not None and candidate:
                # The creator's next asset tries this game first
                _scraper()._creator_last_success[creator_id] = int(candidate)
            break
    if headers is None:
        return []
    bodies: list[bytes] = []
    for rep in REPRESENTATIONS:
        location = default_location if rep is None else _locate(opener, asset_id, rep, headers)[0]
        if not location:
            continue
        try:
            bodies.append(_http_get(location))
        except OSError:
            continue
        if first_only:
            break
    return bodies


def _load_fingerprints() -> dict[str, list[str]]:
    try:
        return json.loads(FINGERPRINTS_FILE.read_text(encoding='utf-8'))
    except OSError, ValueError:
        return {}


def _ensure_fingerprints(
    asset_ids: list[int], cookie: str, place_id: str | None
) -> dict[str, list[str]]:
    """Return content fingerprints, downloading originals not fingerprinted before.

    ponytail: fingerprints never expire; an asset re-uploaded under the same ID
    stops matching until cache_mode_fingerprints.json is deleted.
    """
    fingerprints = _load_fingerprints()
    now = time.monotonic()
    missing = [
        aid
        for aid in asset_ids
        if str(aid) not in fingerprints
        and now - _failed_downloads.get(f'orig_{aid}', -FAILED_RETRY_SECONDS)
        >= FAILED_RETRY_SECONDS
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for aid, bodies in zip(
            missing,
            pool.map(lambda aid: download_representations(aid, cookie, place_id), missing),
            strict=True,
        ):
            if bodies:
                fingerprints[str(aid)] = sorted(
                    {hashlib.md5(body, usedforsecurity=False).hexdigest() for body in bodies}
                )
            else:
                _failed_downloads[f'orig_{aid}'] = now
    APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FINGERPRINTS_FILE.write_text(json.dumps(fingerprints), encoding='utf-8')
    return fingerprints


# (key, size) -> body fingerprint, so repeat applies skip re-reading unchanged entries.
# Saved to DIGESTS_FILE so the first apply after a restart does not re-hash the cache
_digest_memo: dict[tuple[bytes, int], str] = {}
_digest_memo_loaded = False
DIGESTS_FILE = APP_CACHE_DIR / 'cache_mode_digests.json'


def _load_digest_memo() -> None:
    global _digest_memo_loaded
    _digest_memo_loaded = True
    try:
        data = json.loads(DIGESTS_FILE.read_text(encoding='utf-8'))
        for entry, digest in data.items():
            key, size = entry.split(':')
            _digest_memo.setdefault((bytes.fromhex(key), int(size)), str(digest))
    except (OSError, ValueError, AttributeError):
        pass


def _save_digest_memo(present: list[tuple[bytes, int]]) -> None:
    """Save the digests of entries still in the cache (older ones are dropped)."""
    data = {f'{k.hex()}:{s}': _digest_memo[k, s] for k, s in present if (k, s) in _digest_memo}
    try:
        APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        DIGESTS_FILE.write_text(json.dumps(data), encoding='utf-8')
    except OSError as exc:
        log_buffer.log('CacheMode', f'Could not save cache digests: {exc}')


def _content_index(db: sqlite3.Connection) -> dict[str, list[bytes]]:
    """Map the fingerprint of every cached asset body to its cache keys."""
    if not _digest_memo_loaded:
        _load_digest_memo()
    index: dict[str, list[bytes]] = {}
    rows = db.execute('SELECT id, size FROM files WHERE category = ?', (CONTENT_CATEGORY,))
    present = rows.fetchall()
    hashed = False
    for key, size in present:
        digest = _digest_memo.get((key, size))
        if digest is None:
            blob = read_blob(db, key)
            if blob is None:
                continue
            digest = hashlib.md5(body_of(blob), usedforsecurity=False).hexdigest()
            _digest_memo[key, size] = digest
            hashed = True
        index.setdefault(digest, []).append(key)
    if hashed:
        _save_digest_memo(present)
    return index


def _named_asset_ids(tree, path: tuple[str, ...] = ()) -> dict[int, str]:
    """Flatten a nested ID list (Fleasion PreJsons format) into asset ID -> 'A / B / C'."""
    found: dict[int, str] = {}
    items = (
        tree.items()
        if isinstance(tree, dict)
        else enumerate(tree)
        if isinstance(tree, list)
        else ()
    )
    for label, value in items:
        child = (*path, str(label))
        if isinstance(value, dict | list):
            found.update(_named_asset_ids(value, child))
        elif str(value).strip().isdigit():
            # Bare list indexes add nothing; keep the parent's name for those
            name = ' / '.join(child[:-1] if isinstance(label, int) else child)
            asset_id = int(str(value).strip())
            # Shared assets (e.g. M4 and M4A1 sounds) list every name using them
            if asset_id in found and name not in found[asset_id].split(' + '):
                name = f'{found[asset_id]} + {name}'
            found[asset_id] = name
    return found


def _write_report(named: dict[int, str], assets: dict, path: Path, title: str) -> None:
    """Write a readable checklist: every asset by category, with its ID and cache status."""
    sections: dict[str, list[tuple[str, int]]] = {}
    for asset_id, name in named.items():
        parts = name.split(' + ')[0].split(' / ')
        # Config rules: one section per rule group (e.g. per custom gun); lists: per category
        section = ' / '.join(parts[:-1] if parts[0] == 'Configs' else parts[:2])
        label = name.replace(section + ' / ', '')
        sections.setdefault(section, []).append((label, asset_id))
    learned = sum(str(aid) in assets for aid in named)
    lines = [
        f'# {title} assets',
        '',
        f'{learned} of {len(named)} cached. Use the asset ID in a Fleasion config rule.',
        'Missing ones need to be loaded in game once, then run `learn` again.',
    ]
    for section in sorted(sections, key=lambda s: (not s.startswith('Sounds'), s)):
        rows = sorted(sections[section])
        done = sum(str(aid) in assets for _, aid in rows)
        lines += [
            '',
            f'## {section} ({done}/{len(rows)} cached)',
            '',
            '| Name | Asset ID | Cached |',
        ]
        lines.append('|---|---|---|')
        lines += [
            f'| {label} | {aid} | {"yes" if str(aid) in assets else "**no**"} |'
            for label, aid in rows
        ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _named_rule_ids(entries: list, path: tuple[str, ...]) -> dict[int, str]:
    """Name every asset ID a Fleasion config targets after its group/rule path."""
    from ..config.manager import _RESERVED_ASSET_TYPE_ID_MAX

    found: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get('name') or '?')
        if entry.get('type') == 'group':
            found.update(_named_rule_ids(entry.get('children', []), (*path, label)))
            continue
        for value in entry.get('replace_ids', []):
            text = str(value).strip()
            if text.isdigit() and int(text) > _RESERVED_ASSET_TYPE_ID_MAX:
                found.setdefault(int(text), ' / '.join((*path, label)))
    return found


def _named_ids_from_file(source: Path) -> dict[int, str]:
    """Read asset IDs and names from a scraped-game list or a Fleasion config file."""
    data = json.loads(source.read_text(encoding='utf-8'))
    if isinstance(data, dict) and isinstance(data.get('replacement_rules'), list):
        return _named_rule_ids(data['replacement_rules'], ('Configs', source.stem))
    return _named_asset_ids(data)


def learn_key_list(
    id_lists: list[Path], out_path: Path, cookie: str, place_id: str | None
) -> tuple[int, int, dict[str, list[str]]]:
    """Record cache keys for every asset in id_lists found in the local Roblox cache.

    Sources can be scraped-game lists (PreJsons) or Fleasion config files, whose
    rules name the exact gun/melee/model part IDs. Assets already in out_path are
    kept, so the list grows over several play sessions. Returns (learned, total,
    missing names grouped by category).
    """
    named: dict[int, str] = {}
    for source in id_lists:
        for asset_id, name in _named_ids_from_file(source).items():
            if asset_id in named and name not in named[asset_id].split(' + '):
                name = f'{named[asset_id]} + {name}'
            named[asset_id] = name
    try:
        assets: dict[str, list[dict]] = json.loads(out_path.read_text(encoding='utf-8'))['assets']
    except OSError, ValueError, KeyError:
        assets = {}
    todo = [aid for aid in named if str(aid) not in assets]
    fingerprints = _ensure_fingerprints(todo, cookie, place_id)
    db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
    try:
        index = _content_index(db)
        for aid in todo:
            keys = [k for fp in fingerprints.get(str(aid), []) for k in index.get(fp, [])]
            if keys:
                assets[str(aid)] = [
                    {
                        'key': k.hex(),
                        'kind': kind_of(body_of(read_blob(db, k) or b'')),
                        'name': named[aid],
                    }
                    for k in dict.fromkeys(keys)
                ]
    finally:
        db.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({'version': 1, 'assets': assets}, indent=1), encoding='utf-8')
    _write_report(named, assets, out_path.with_suffix('.md'), id_lists[0].stem)
    missing: dict[str, list[str]] = {}
    for aid, name in named.items():
        if str(aid) not in assets:
            group, _, leaf = name.rpartition(' / ')
            missing.setdefault(group or name, []).append(leaf or str(aid))
    return sum(str(aid) in assets for aid in named), len(named), missing


def record_key_list(source: Path, cookie: str | None) -> tuple[int, int, Path]:
    """Save which cache keys hold a scraped game's named assets, as a shareable key list.

    Written to the user's key lists folder, where the Replacer's game import
    finds it; anyone with the file can import the game without joining it.
    Returns (assets recorded, named assets in the source, the list's path).
    """
    out = KEY_LISTS_DIR / (source.stem.lower().replace(' ', '_') + '.json')
    with _apply_lock:
        learned, total, _missing = learn_key_list(
            [source], out, cookie or '', last_joined_place_id()
        )
    return learned, total, out


# Download name -> time of last failure; retried only after FAILED_RETRY_SECONDS
_failed_downloads: dict[str, float] = {}
FAILED_RETRY_SECONDS = 600


def _cached_download(name: str, fetch) -> bytes:
    """Return bytes from Fleasion's download cache, fetching them once."""
    path = DOWNLOAD_DIR / name
    if path.is_file():
        return path.read_bytes()
    if time.monotonic() - _failed_downloads.get(name, -FAILED_RETRY_SECONDS) < FAILED_RETRY_SECONDS:
        raise OSError('failed recently; will retry later')
    try:
        data = fetch()
    except OSError:
        _failed_downloads[name] = time.monotonic()
        raise
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _fit_to_slot(body: bytes, slot_kind: str) -> bytes:
    """Convert a replacement into the format the cache slot holds.

    Game textures are cached as KTX2 and meshes as Roblox .mesh files; a PNG/JPG/
    WebP or an OBJ written there as-is is not usable. The proxy converted the
    same way when serving those requests. Other replacements pass through.
    """
    from ..modifications.manager import ModificationManager

    if slot_kind == '.mesh' and ModificationManager._looks_like_obj(body):
        try:
            return ModificationManager._convert_obj_to_mesh(body)
        except Exception as exc:
            log_buffer.log(
                'CacheMode', f'Could not convert OBJ to mesh; writing it unchanged: {exc}'
            )
            return body
    image_kind = kind_of(body)
    if slot_kind != '.ktx2' or image_kind not in ('.png', '.jpg', '.webp'):
        return body
    from .tools.image_to_ktx2.converter import get_or_create_ktx2_from_image

    digest = hashlib.md5(body, usedforsecurity=False).hexdigest()
    source = DOWNLOAD_DIR / f'img_{digest}{image_kind}'
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        source.write_bytes(body)
    converted = get_or_create_ktx2_from_image(source)
    if converted.suffix.lower() == '.ktx2' and converted.is_file():
        return converted.read_bytes()
    log_buffer.log('CacheMode', f'Could not convert {source.name} to KTX2; writing it unchanged')
    return body


def _bundled_empty(kind: str) -> bytes | None:
    """Return the placeholder that makes an asset of this type disappear."""
    if kind in IMAGE_SLOT_KINDS:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new('RGBA', (1, 1), (0, 0, 0, 0)).save(buffer, format='PNG')
        return buffer.getvalue()  # _fit_to_slot turns it into KTX2 where needed
    from ..modifications.manager import _bundled_path

    name = EMPTY_BY_KIND.get(kind)
    return _bundled_path(name).read_bytes() if name else None


def _collect_targets(config_manager) -> dict[int, tuple[str, object]]:
    """Map each concrete asset ID from the enabled configs to its replacement source."""
    from ..config.manager import _RESERVED_ASSET_TYPE_ID_MAX

    replacements, removals, cdn_replacements, local_replacements = (
        config_manager.get_all_replacements()
    )
    targets: dict[int, tuple[str, object]] = {}
    for kind, mapping in (
        ('remove', dict.fromkeys(removals)),
        ('id', replacements),
        ('cdn', cdn_replacements),
        ('local', local_replacements),
    ):
        for asset_id, source in mapping.items():
            # Asset-type rules (small ints / type names) and slot keys are proxy-only.
            if isinstance(asset_id, int) and asset_id > _RESERVED_ASSET_TYPE_ID_MAX:
                targets[asset_id] = (kind, source)
    # Modifications tab "Game skyboxes": one image/ID for every known skybox texture.
    # Config rules for the same asset take priority
    sky = str(getattr(config_manager, 'cache_skybox', '') or '').strip()
    if sky:
        source = ('id', int(sky)) if sky.isdigit() else ('local', sky)
        for asset_id in skybox_asset_ids():
            targets.setdefault(asset_id, source)
    return targets


def skybox_asset_ids() -> set[int]:
    """Asset IDs that key lists name as skyboxes (e.g. Phantom Forces' Textures / Skyboxes)."""
    found: set[int] = set()
    for folder in (bundled_key_lists_dir(), KEY_LISTS_DIR):
        for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            try:
                assets = json.loads(path.read_text(encoding='utf-8')).get('assets', {})
            except OSError, ValueError, AttributeError:
                continue
            for asset_id, entries in assets.items():
                if any('skybox' in str(e.get('name', '')).lower() for e in entries):
                    found.add(int(asset_id))
    return found


def _rule_names(config_manager) -> dict[int, list[str]]:
    """Map each asset ID to the names of the enabled rules that replace it."""
    from ..config.manager import ConfigManager, _parse_config_asset_id

    names: dict[int, list[str]] = {}
    for config_name in getattr(config_manager, 'enabled_configs', []):
        rules = config_manager.get_replacement_rules(config_name)
        for rule in ConfigManager._iter_replacement_rules(rules):
            if not isinstance(rule, dict) or not rule.get('enabled', True):
                continue
            for value in rule.get('replace_ids', []):
                asset_id = _parse_config_asset_id(value)
                if asset_id is not None:
                    names.setdefault(asset_id, []).append(str(rule.get('name') or asset_id))
    return names


def _describe(asset_ids: list[int], names: dict[int, list[str]], limit: int = 0) -> str:
    """Return the distinct rule names behind asset_ids, optionally truncated."""
    unique = list(dict.fromkeys(n for aid in asset_ids for n in names.get(aid, [str(aid)])))
    if limit and len(unique) > limit:
        return ', '.join(unique[:limit]) + f' (+{len(unique) - limit} more)'
    return ', '.join(unique)


def _replacement_bytes(kind: str, source, cookie: str, place_id: str | None) -> bytes | None:
    if kind == 'id':
        # ponytail: default representation only; Roblox accepted a PNG in a WebP
        # slot, but a KTX2-only slot may need the matching representation.
        def fetch() -> bytes:
            bodies = download_representations(int(source), cookie, place_id, first_only=True)
            if not bodies:
                raise OSError(f'asset {source} could not be downloaded')
            return bodies[0]

        return _cached_download(f'id_{source}', fetch)
    if kind == 'cdn':
        name = 'cdn_' + hashlib.md5(str(source).encode(), usedforsecurity=False).hexdigest()
        return _cached_download(name, lambda: _http_get(str(source)))
    if kind == 'local':
        return Path(str(source)).read_bytes()
    return None  # removals depend on the cached file's type


@contextlib.contextmanager
def exclusive_cache_access():
    """Hold off Cache mode applies while something else (Clear Cache) changes the cache."""
    global _incremental
    with _apply_lock:
        _digest_memo.clear()
        _incremental = None
        yield


# Roblox asset type IDs (CacheManager.ASSET_TYPES) by file kind, for the Scraper tab
TYPE_BY_KIND = {
    '.ogg': 3,
    '.mp3': 3,
    '.png': 1,
    '.webp': 1,
    '.jpg': 1,
    '.ktx2': 1,
    '.mesh': 4,
    '.rbxm': 10,
    '.rbxmx': 10,
}


def known_asset_ids(db: sqlite3.Connection) -> dict[bytes, int]:
    """Map cache keys to the asset IDs we know them by (key lists and fingerprints)."""
    known = {key: aid for aid, entries in load_key_lists().items() for key, _ in entries}
    index = _content_index(db)
    for aid, digests in _load_fingerprints().items():
        for digest in digests:
            for key in index.get(digest, []):
                known.setdefault(key, int(aid))
    return known


def key_list_names() -> dict[bytes, tuple[str, str]]:
    """Map cache keys to (game, name) from every key list that names its assets."""
    names: dict[bytes, tuple[str, str]] = {}
    for folder in (bundled_key_lists_dir(), KEY_LISTS_DIR):
        for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            game = path.stem.replace('_', ' ').title()
            try:
                assets = json.loads(path.read_text(encoding='utf-8')).get('assets', {})
                for entries in assets.values():
                    for entry in entries:
                        if entry.get('name'):
                            names.setdefault(bytes.fromhex(entry['key']), (game, str(entry['name'])))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
    return names


@dataclass(frozen=True)
class CacheEntry:
    """One asset in Roblox's cache, as the Cache tab lists it."""

    key: bytes
    size: int
    inline: bool  # stored in rbx-storage.db rather than as an rbx-storage file
    kind: str
    asset_id: int | None
    game: str
    name: str
    status: str  # '', 'replaced' or 'inserted' (by ReCache)


def cache_entries() -> list[CacheEntry]:
    """List every asset in Roblox's cache with what ReCache knows about it."""
    if not STORAGE_DB.is_file():
        return []
    names = key_list_names()
    replaced = {p.name for p in BACKUP_DIR.glob('*')} if BACKUP_DIR.is_dir() else set()
    entries: list[CacheEntry] = []
    with _apply_lock:
        db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
        try:
            known = known_asset_ids(db)
            rows = db.execute(
                'SELECT id, size, substr(content, 1, 4096) FROM files WHERE category = ?',
                (CONTENT_CATEGORY,),
            ).fetchall()
            for key, size, head in rows:
                inline = head is not None
                if not inline:
                    try:
                        with _blob_path(key).open('rb') as f:
                            head = f.read(4096)
                    except OSError:
                        head = b''
                hex_key = key.hex()
                status = (
                    'inserted'
                    if hex_key + INSERTED_SUFFIX in replaced
                    else 'replaced' if hex_key in replaced else ''
                )
                game, name = names.get(key, ('', ''))
                entries.append(
                    CacheEntry(
                        key, size, inline, kind_of(body_of(head)), known.get(key), game, name, status
                    )
                )
        finally:
            db.close()
    return entries


def _cached_original(
    db: sqlite3.Connection, asset_id: int, index: dict[str, list[bytes]] | None = None
) -> bytes | None:
    """An asset's original bytes from Roblox's cache, found by key list or fingerprint.

    Entries ReCache replaced give their backup; entries it inserted are skipped.
    """
    keys = [key for key, _ in load_key_lists().get(asset_id, [])]
    digests = _load_fingerprints().get(str(asset_id), [])
    if digests:
        index = index if index is not None else _content_index(db)
        keys += [key for digest in digests for key in index.get(digest, [])]
        # Entries ReCache replaced no longer match; their backups hold the original.
        # ponytail: hashes every backup per lookup; memoize if replacements get large
        for backup in BACKUP_DIR.glob('*') if BACKUP_DIR.is_dir() else []:
            if backup.name.endswith(INSERTED_SUFFIX):
                continue
            body = body_of(backup.read_bytes())
            if hashlib.md5(body, usedforsecurity=False).hexdigest() in digests:
                with contextlib.suppress(ValueError):
                    keys.append(bytes.fromhex(backup.name))
    for key in dict.fromkeys(keys):
        if (BACKUP_DIR / (key.hex() + INSERTED_SUFFIX)).exists():
            continue
        backup = BACKUP_DIR / key.hex()
        blob = backup.read_bytes() if backup.is_file() else read_blob(db, key)
        if blob:
            return body_of(blob)
    return None


def cached_asset_body(asset_id: int) -> bytes | None:
    """An asset's bytes from Roblox's local cache, for previews when downloading fails."""
    if not STORAGE_DB.is_file() or not _apply_lock.acquire(timeout=5):
        return None
    try:
        db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
        try:
            return _cached_original(db, asset_id)
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        return None
    finally:
        _apply_lock.release()


def read_cache_body(key: bytes, original: bool = False) -> bytes | None:
    """Return an entry's asset bytes; with ``original``, what ReCache replaced."""
    backup = BACKUP_DIR / key.hex()
    if original and backup.is_file():
        return body_of(backup.read_bytes())
    if not STORAGE_DB.is_file():
        return None
    db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
    try:
        blob = read_blob(db, key)
    finally:
        db.close()
    return None if blob is None else body_of(blob)


def import_roblox_cache(cache_manager) -> tuple[int, int]:
    """Snapshot Roblox's asset cache into the Scraper tab's store.

    Clear the Roblox cache, join a game, then import: the Scraper then lists just
    that game's assets. Entries Recache replaced show their original (backup).
    Assets get their real ID when a key list or fingerprint knows it, otherwise
    their cache key. Returns (assets imported, of which with a known asset ID).
    """
    if not STORAGE_DB.is_file():
        return 0, 0
    with _apply_lock:
        db = sqlite3.connect(f'file:{STORAGE_DB}?mode=ro', uri=True)
        try:
            known = known_asset_ids(db)
            cache_manager.clear_cache()
            imported = with_id = 0
            rows = db.execute('SELECT id FROM files WHERE category = ?', (CONTENT_CATEGORY,))
            for (key,) in rows.fetchall():
                backup = BACKUP_DIR / key.hex()
                blob = backup.read_bytes() if backup.is_file() else read_blob(db, key)
                if blob is None:
                    continue
                body = body_of(blob)
                asset_type = TYPE_BY_KIND.get(kind_of(body))
                if asset_type is None:  # CSG unions and other formats the viewer cannot show
                    continue
                asset_id = known.get(key)
                if cache_manager.store_asset(
                    str(asset_id) if asset_id else key.hex(),
                    asset_type,
                    body,
                    metadata={'cache_key': key.hex(), 'source': 'roblox-cache'},
                ):
                    imported += 1
                    with_id += asset_id is not None
        finally:
            db.close()
    return imported, with_id


def on_roblox_closed() -> None:
    """Cache saving mode after Roblox closes (you left the game).

    Keep database: the cache is wiped but an empty database stays, so you can write
    to it without reopening Roblox. Otherwise nothing is deleted.
    """
    keep_db, keep_entries = cache_saving_settings()
    if keep_entries or not keep_db:
        return
    from ..utils import delete_cache

    with exclusive_cache_access():
        for message in clear_cache_keeping(delete_cache, force_db=True):
            log_buffer.log('CacheMode', message)


def on_recache_exit(roblox_running: bool) -> None:
    """Cache saving mode when ReCache closes.

    Keep entries: nothing is deleted or undone. Keep database: the cache is wiped but an
    empty database stays (only when Roblox is closed). Otherwise ReCache just undoes its changes.
    """
    keep_db, keep_entries = cache_saving_settings()
    if keep_entries:
        log_buffer.log('CacheMode', 'ReCache exiting: cache and entries kept')
        return
    if keep_db and not roblox_running:
        on_roblox_closed()
        return
    restored = restore_cache_mode()
    log_buffer.log('CacheMode', f'ReCache exiting: restored {restored} cache entries')


def restore_cache_mode() -> int:
    """Undo every Cache mode change so Roblox looks normal without Fleasion."""
    global _incremental
    with _apply_lock:
        _incremental = None
        if not STORAGE_DB.is_file():
            return 0
        db = sqlite3.connect(STORAGE_DB, timeout=10)
        try:
            restored = restore_backups(db)
            db.commit()
        finally:
            db.close()
    _digest_memo.clear()
    return restored


def clear_recache_data() -> int:
    """Undo ReCache's entries in Roblox's cache and delete ReCache's working files.

    Removes inserted entries and restores replaced ones, then deletes downloaded
    replacements, original fingerprints and cache digests. Key lists (bundled and
    learned) stay, so games can be imported again without joining. Returns the
    number of Roblox cache entries undone.
    """
    global _incremental
    restored = restore_cache_mode()
    with _apply_lock:
        shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
        for path in (FINGERPRINTS_FILE, DIGESTS_FILE):
            path.unlink(missing_ok=True)
        _digest_memo.clear()
        _failed_downloads.clear()
        _incremental = None
    return restored


# State of the last apply, so a pending-only apply can skip what is already written:
# (targets, imported games, cache key -> (asset ID, size of the record written, inserted))
_incremental: tuple[dict, set[str], dict[bytes, tuple[int, int, bool]]] | None = None


def apply_cache_rules(config_manager, progress=None, *, pending_only: bool = False) -> ApplyResult:
    """Write every enabled rule into Roblox's cache. Returns a one-line summary.

    Safe while Roblox runs (verified in game): it reads cache entries when it
    loads an asset. Earlier Cache-mode writes are restored first, so disabled
    rules disappear and changed rules are re-applied.

    With ``pending_only`` (the catch-up loop) nothing is restored and entries the
    last apply wrote are left alone unless Roblox replaced them since. It falls
    back to a full apply when the rules changed after the last apply.
    """
    from ..utils.roblox_auth import get_roblosecurity

    global _incremental
    # Overlapping triggers (Fleasion and Roblox starting together) run one after
    # the other, so the latest state is always applied
    if not _apply_lock.acquire(timeout=300):
        return _problem('Timed out waiting for a previous apply')
    try:
        if not STORAGE_DB.is_file() and STORAGE_DB.parent.is_dir():
            _create_empty_db()
        if not STORAGE_DB.is_file():
            return _problem('Roblox has no cache yet: open Roblox once, then apply')

        if not pending_only:
            # Apply to Roblox retries everything, e.g. right after the login was fixed
            _failed_downloads.clear()
        targets = _collect_targets(config_manager)
        games = imported_games()
        previous, _incremental = _incremental, None  # set again once this apply finishes
        incremental = (
            pending_only
            and previous is not None
            and previous[0] == targets
            and previous[1] == games
        )
        skip: dict[bytes, tuple[int, int, bool]] = previous[2] if incremental and previous else {}
        db = sqlite3.connect(STORAGE_DB, timeout=10)
        try:
            if skip:
                # Only skip work while every entry still holds what the last apply
                # wrote; if Roblox replaced one, do the full restore + re-apply.
                # ponytail: compared by size only; a same-size replacement goes
                # unnoticed until the next full apply (hash the body if that matters)
                sizes = dict(db.execute('SELECT id, size FROM files').fetchall())
                incremental = all(sizes.get(k) == size for k, (_, size, _) in skip.items())
                if not incremental:
                    skip = {}
            written = dict(skip)
            # Written entries no longer match the original's fingerprint
            skip_by_aid: dict[int, list[bytes]] = {}
            for key, (owner, _, _) in skip.items():
                skip_by_aid.setdefault(owner, []).append(key)
            restored = 0 if incremental else restore_backups(db)
            db.commit()
            if not targets:
                message = f'No asset ID rules to apply (restored {restored} entries)'
                return ApplyResult(message, 'Nothing to apply', ok=True)

            cookie = get_roblosecurity()
            if not cookie:
                return _problem('Log in to the Roblox app first')
            place_id = last_joined_place_id()

            key_lists = load_key_lists(imported_only=True)
            # Assets a key list already covers need no original downloaded
            fingerprints = _ensure_fingerprints(
                [aid for aid in targets if aid not in key_lists], cookie, place_id
            )
            index = _content_index(db)

            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            applied = entries = inserted = failed = 0
            not_cached: list[int] = []
            unavailable: list[int] = []
            failed_ids: list[int] = []
            learned: dict[int, list[tuple[bytes, str]]] = {}
            total = len(targets)
            for done, (aid, (kind, source)) in enumerate(targets.items()):
                if progress is not None:
                    progress(done, total)
                matched = [k for fp in fingerprints.get(str(aid), []) for k in index.get(fp, [])]
                # Cached entries first; key-list kinds only matter for inserts
                slots: dict[bytes, str] = dict.fromkeys(matched, '')
                for key, listed_kind in key_lists.get(aid, []):
                    slots.setdefault(key, listed_kind)
                for key in skip_by_aid.get(aid, []):
                    slots.setdefault(key, '')
                if not slots:
                    (not_cached if str(aid) in fingerprints else unavailable).append(aid)
                    continue
                # Already written by the last apply: count it, do not rewrite it
                done_slots = [skip[k] for k in slots if k in skip]
                entries += len(done_slots)
                inserted += sum(was_inserted for _, _, was_inserted in done_slots)
                if len(done_slots) == len(slots):
                    applied += 1
                    continue
                try:
                    data = _replacement_bytes(kind, source, cookie, place_id)
                except OSError as exc:
                    # A replacement ID Roblox already cached works without downloading
                    data = _cached_original(db, int(source), index) if kind == 'id' else None
                    if data is None:
                        log_buffer.log('CacheMode', f'Asset {aid}: replacement unavailable: {exc}')
                        failed += 1
                        failed_ids.append(aid)
                        continue
                wrote = bool(done_slots)
                for key, listed_kind in slots.items():
                    if key in skip:
                        continue
                    blob = read_blob(db, key)
                    if blob is not None and parse_rbxh(blob) is None:
                        continue
                    asset_kind = kind_of(body_of(blob)) if blob is not None else listed_kind
                    body = data if data is not None else _bundled_empty(asset_kind)
                    if body is not None:
                        body = _fit_to_slot(body, asset_kind)
                    if body is None:
                        log_buffer.log(
                            'CacheMode', f'Asset {aid}: no empty placeholder for this type'
                        )
                        continue
                    if blob is None:
                        # A row whose file is gone (cache folder cleared while the
                        # database was locked) is replaced by a fresh entry
                        db.execute('DELETE FROM files WHERE id = ?', (key,))
                        written[key] = (aid, insert_blob(db, key, body), True)
                        (BACKUP_DIR / (key.hex() + INSERTED_SUFFIX)).touch()
                        inserted += 1
                    else:
                        if key in matched:
                            learned.setdefault(aid, []).append((key, asset_kind))
                        backup = BACKUP_DIR / key.hex()
                        if not backup.exists():
                            backup.write_bytes(blob)
                        new_blob = with_body(blob, body)
                        write_blob(db, key, new_blob)
                        written[key] = (aid, len(new_blob), False)
                    entries += 1
                    wrote = True
                applied += wrote
            db.commit()
            _incremental = (targets, games, written)
            if progress is not None:
                progress(total, total)
            if learned:
                _save_learned_keys(learned)
        finally:
            db.close()

        names = _rule_names(config_manager)
        if not_cached:
            # Full list in the log; the summary (tray notification) stays short.
            log_buffer.log(
                'CacheMode',
                'Not cached yet (load these in game; they are picked up while Roblox runs, '
                'rejoin to see them): ' + _describe(not_cached, names),
            )
        if unavailable:
            log_buffer.log(
                'CacheMode',
                'Original could not be downloaded (asset may belong to a game you have '
                'not joined last): ' + _describe(unavailable, names),
            )
        summary = (
            f'Cache mode applied {applied} of {len(targets)} assets ({entries} cache entries, '
            f'{inserted} inserted from key lists); {len(not_cached)} not cached yet, '
            f'{len(unavailable)} unavailable, {failed} failed'
        )
        if not_cached:
            summary += f'. Not cached yet: {_describe(not_cached, names, limit=5)}'
        short = f'Applied {applied}/{len(targets)}'
        if failed:
            short += f' · {failed} failed'
        if not_cached or unavailable:
            short += f' · {len(not_cached) + len(unavailable)} not available yet'
        details = tuple(
            (heading, tuple(_describe(ids, names).split(', ')))
            for heading, ids in (
                ('Failed (replacement file or asset could not be loaded)', failed_ids),
                ('Not loaded in game yet (load it, then rejoin)', not_cached),
                ('Original could not be downloaded', unavailable),
            )
            if ids
        )
        return ApplyResult(
            summary,
            short,
            ok=not failed,
            applied=applied,
            pending=len(not_cached) + len(unavailable),
            details=details,
        )
    finally:
        _apply_lock.release()

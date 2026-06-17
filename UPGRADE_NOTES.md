# Upgrading from any earlier version to v37+

Starting in v37, your watchlist, Schwab token, and `.env` live in a
stable location outside the version folder so they survive every
zip upgrade going forward.

## Stable paths

```
~/.jerry-dashboard/
  watchlist.json     # all your symbols, tags, notes
  schwab_token.json  # OAuth token (don't share)
  .env               # SCHWAB_APP_KEY + SCHWAB_APP_SECRET
```

Override the directory with `JERRY_DATA_DIR=/some/other/path` if you
want a different location.

## First run after upgrade

The server checks for legacy files in the project folder and copies
them forward automatically:

- `Sell_covered_calls_v36/watchlist.json` → `~/.jerry-dashboard/watchlist.json`
- `Sell_covered_calls_v36/schwab_token.json` → `~/.jerry-dashboard/schwab_token.json`

After that copy, future versions read directly from `~/.jerry-dashboard/`
and never touch the project folder for storage.

## What this means going forward

- Unzip a new version, start the server, your watchlist is there.
- No more re-adding symbols. No more re-running `schwab_auth.py`.
- Old version folders can be deleted safely.

## Rollback safety

The migration only **copies** files, it does NOT delete the originals.
Your old `watchlist.json` in the v36 folder is untouched. If anything
goes wrong with the new location, the old data is still there.

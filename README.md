# Homebase for Mac

A local Python/Tkinter desktop app with SQLite storage. The package includes your app.py and inventory.py plus the schema and sample data that they need.

## Run

1. Extract the entire Homebase_Mac.zip archive.
2. Open Terminal (Command+Space, type Terminal, press Return).
3. If you extracted the folder in Downloads, run:

```sh
cd ~/Downloads/Homebase_Mac
python3 app.py
```

If you extracted elsewhere, type `cd ` in Terminal, drag the Homebase_Mac folder from Finder into the Terminal window, and press Return. Then run `python3 app.py`.

You can also double-click Start_Homebase.command. If macOS does not allow the launcher to run, use the direct Terminal command above; do not disable system security settings.

## Check Python first

```sh
python3 --version
python3 -m tkinter
```

Use Python 3.10 or newer. The second command should open a small Tk test window; close it when finished. If Python or Tkinter is missing, install a macOS Python package from https://www.python.org/downloads/macos/ and reopen Terminal. Python.org installers include Tkinter. No Xcode installation is needed for this desktop version.

## Photos

PNG/GIF previews work without extra packages. JPG/WebP previews use optional Pillow. If needed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install Pillow
python app.py
```

After using that virtual environment, activate it again before running the app with `source .venv/bin/activate`. Use `python app.py` in the activated environment so Pillow is available. The launcher otherwise uses the Python found as `python3` in your shell.

## Local data

The first launch creates `data/homebase.sqlite3` in this project folder, with 18 sample items and 16 nested locations. It contains inventory records, quantities, tags, notes, photos, locations, and activity. Keep the whole folder somewhere writable.

Use **Back up database…** inside the app to copy everything, including photos. The app stays offline and does not sync with the earlier website or your phone. The iPhone interface remains a separate future step.

## Changes for Mac

- macOS font selection.
- Command+N to add an item and Command+F to search.
- Command+Return to save an item form.
- Platform-specific mouse-wheel event bindings.
- Mac launcher and instructions.

The original two uploaded files remain available separately; this package contains a platform-specific copy of app.py. inventory.py is unchanged.

## Tests

```sh
python3 -m unittest discover -s tests -v
python3 app.py --check
```

21 data and desktop-callback tests pass in the build environment. The extracted ZIP also passes the database startup/integrity check. Native macOS window rendering must be checked on your Mac; the build environment has no macOS desktop.

## Files

- app.py — interface and desktop actions
- inventory.py — validation, filtering, database operations, and backups
- schema.sql — SQLite tables and constraints
- sample_data.json — sample inventory and locations
- Start_Homebase.command — Mac launcher
- requirements.txt — optional Pillow photo support
- tests/ — automated tests

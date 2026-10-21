#!/bin/zsh
# Start Homebase from Finder or Terminal; no server or internet is used.
cd -- "${0:A:h}" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    print 'Python 3 was not found. Install Python for macOS from https://www.python.org/downloads/macos/'
    read '?Press Return to close.'
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    print 'Homebase needs Python 3.10 or newer. Install a macOS Python package from python.org.'
    read '?Press Return to close.'
    exit 1
fi
if ! python3 -c 'import tkinter' 2>/dev/null; then
    print 'This Python installation does not include Tkinter.'
    print 'Install Python for macOS from https://www.python.org/downloads/macos/ then reopen Terminal.'
    read '?Press Return to close.'
    exit 1
fi
python3 app.py
homebase_exit=$?
if (( homebase_exit != 0 )); then
    print ''
    print 'Homebase could not start. Copy the error above into our chat.'
    read '?Press Return to close.'
fi
exit "$homebase_exit"

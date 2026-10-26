# datasets/

Empty until downloaded. On a laptop with internet:

    python tools/fetch_datasets.py      # ready-built set from the GitHub Release, ~30 MB

or build the same set from the authors' Google Drive (~15 min): `pip install gdown`, then
`python tools/build_datasets.py`.

Result: 30 clips per group (40 false turn-taking clips), 16 kHz mono FLAC, about 30 MB, plus `manifest.json`.
A Pi needs it only for the virtual-cable mode. Licences: see DATA_LICENSES.md.

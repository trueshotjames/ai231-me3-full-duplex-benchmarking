# datasets/

Empty until built. On a laptop with internet:

    pip install gdown
    python tools/build_datasets.py

Result: 30 clips per group (40 false turn-taking clips), 16 kHz mono FLAC, about 120 MB, plus `manifest.json`.
Copy this whole folder to a Pi if you benchmark there. Licences: see DATA_LICENSES.md.

# Releasing

The recipe used for v24 (2026-10-09), with the two-ZIP split that starts at v25 (PR #26). One heavy job at a
time on the build machine: the suite, PyInstaller and the smoke test each run alone.

1. **Export the source.** `git archive` of the merged `main` into `build/vN-src`; build from that export, never
   from a working tree with uncommitted files.
2. **Run the suite on the export** and keep its full output: `python -B -m unittest discover -s tests -v > build/vN-tests.log 2>&1`.
   Release only on exit status 0 (`OK`, skips allowed).
3. **Record the environment.** `pip freeze > build/vN-requirements.txt` from the venv that builds the release, so
   the exact dependency set of each release is known (`requirements.txt` holds ranges).
4. **Build.** From the export: `python -m PyInstaller DiscSkinnerApp.spec --noconfirm --distpath ../../dist/vN --workpath ../vN-work`
   (about 35 s). The spec builds one folder: `SkinnerApp.exe` plus `_internal`.
5. **Package.** Extract the previous release's ZIP into `dist/vN-package` (a fresh extract: the package folders of
   earlier test runs get polluted), replace `SkinnerApp.exe` and `_internal` with the new build, and refresh
   `README.md`, `ANIMATION_EXPORT.md`, `T2_IMPORT.md`, `NOTICE.md`, `LICENSE` and `docs/*` from the export. The
   Tribes 1 animation caches stay under `local-data/animations`.
6. **Smoke-test a copy** on a spare port with `SKINNER_DATA_DIR` pointing at a scratch folder: run an import (from
   the app page, or with curl and an `Origin` header, since imports refuse foreign requests), then `list_models`
   and one `export_glb`; `python tools/check_packaged.py http://127.0.0.1:PORT` checks the catalogs. Stop every
   `SkinnerApp.exe` afterwards (`tasklist | findstr SkinnerApp`; the one-folder build ran as a single process for v25,
   the old single-file build as two) and check the port no longer answers.
7. **Zip and hash.** From v25: `python tools/split_release.py dist/vN-package/DTS-Skinner dist/vN vN` (run it from the export, since the
   script lives on `main` and an older branch's worktree may not have it) writes the app ZIP and
   the data ZIP with their `.sha256` files; if the data ZIP's hash equals the last data release's, upload only the
   app ZIP and link the data release in the notes. Write `.sha256` files with LF line endings (Python text mode
   on Windows gives CRLF, which breaks `sha256sum -c`).
8. **Publish.** `gh release create vN --target FULLSHA` with the ZIPs and checksums; `--target` needs a branch
   name or a full SHA. The notes say what is new and whether the data ZIP changed. v25 is the first split release,
   so it also publishes `DTS-Skinner-data-Windows.zip` as its own release tagged `data-1` (about 504 MB) and links
   it from the notes; later data releases are `data-2`, `data-3` and so on.
9. **Afterwards.** Delete `dist/v(N-1)` and `dist/v(N-1)-package` once vN is confirmed; keep the newest pair
   for the next package step and for `check_packaged.py`.

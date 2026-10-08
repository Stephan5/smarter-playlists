# TODO

* **Record estimated plays in the run table**: `run.plays_estimated` already counts the plays estimated by each run. Also
  record the share of all plays that are estimated (or the longest gap between imports), so the run history shows when
  play history quality is slipping, and warn when it climbs.
* **Packaging release workflow**: a GitHub Actions workflow in `.github/workflows` that builds the PyInstaller binary
  (`smarter-playlists.spec`, see the `Makefile`) when a version tag is pushed, and attaches it to a GitHub release.

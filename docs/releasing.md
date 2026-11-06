# Releasing Manual Beagle

For maintainers. A release is made by pushing a version tag (`v0.1.0`). The
[release workflow](../.github/workflows/release.yml) then builds the sdist, the wheel and the container image,
checks them, and, once publishing is switched on, publishes them to PyPI and the GitHub Container Registry and
makes the GitHub release.

- [Versions](#versions)
- [Switching publishing on (once)](#switching-publishing-on-once)
- [Making a release](#making-a-release)
- [Checking a release locally](#checking-a-release-locally)

## Versions

Manual Beagle follows [Semantic Versioning](https://semver.org/). While it's 0.x, a minor version (0.2.0) may
change anything, and a patch version (0.1.1) only fixes things. The version is written in one place,
`src/manualbeagle/__init__.py`; `pyproject.toml` reads it from there. Between releases it carries `.dev0`
(`0.2.0.dev0`). A pre-release is `0.2.0rc1` (PEP 440), tagged `v0.2.0rc1`: it becomes a GitHub pre-release, and
its image isn't tagged `latest`.

## Switching publishing on (once)

1. **The repository.** Its home is the `manualbeagle` GitHub organization,
   `github.com/manualbeagle/manualbeagle`; every link in the code and docs names it, as does the User-Agent in
   `src/manualbeagle/http.py`. Set `.github/CODEOWNERS` to the organization's maintainers.
2. **Repository settings** (Settings > Code security): private vulnerability reporting (SECURITY.md and the Code
   of Conduct point to it), secret scanning and push protection, and Dependabot alerts. Under Settings > Rules,
   protect `main` (pull requests, with the CI checks passing) and add a tag rule for `v*` so only maintainers
   can push release tags. Under Settings > Actions > General, keep the default `GITHUB_TOKEN` permissions
   read-only: each workflow asks for what it needs.
3. **PyPI.** Sign in to PyPI and add a *pending publisher* (Your account > Publishing): project `manualbeagle`,
   owner `manualbeagle`, repository `manualbeagle`, workflow `release.yml`, environment `pypi`. No API token is
   stored anywhere: GitHub Actions proves who it is with OpenID Connect.
4. **The `pypi` environment** (Settings > Environments > New environment, named `pypi`): add yourself as a
   required reviewer, so every upload waits for a click, and limit it to tags matching `v*`.
5. **Switch it on:** Settings > Secrets and variables > Actions > Variables, add `RELEASE_ENABLED` = `true`.
6. **After the first image is pushed,** make the package public: the organization's Packages tab >
   `manualbeagle` > Package settings > Change visibility. Then link it to the repository there if GitHub hasn't
   (the image's `org.opencontainers.image.source` label names it).
7. **The live adapter run** (`.github/workflows/live-adapters.yml`) switches from weekly to nightly by itself
   once the repository is public, and keeps one issue labelled `live-adapters` current. Nothing to set.

## Making a release

1. **Check.** `ruff check src tests`, `ruff format --check src tests` and `pytest` pass. Run the live adapters
   (`manualbeagle adapters test --live`, or Actions > Live adapters > Run workflow) and look at anything broken.
   Regenerate the scoreboard: `manualbeagle adapters scoreboard --write docs/brands.md`.
2. **Set the version** in `src/manualbeagle/__init__.py` (drop `.dev0`). In `CHANGELOG.md`, rename
   `[Unreleased]` to `[0.1.0] - <today's date>`, add an empty `[Unreleased]` above it, and update the compare
   links at the bottom. The workflow refuses a tag whose version has no `## [0.1.0]` section.
3. **Commit** (`Release 0.1.0`) through a pull request, and wait for CI on `main`.
4. **A dry run**, if anything in packaging changed: Actions > Release > Run workflow on `main`. It builds and
   checks everything and publishes nothing.
5. **Tag it** on `main`, and push the tag:

   ```
   git tag -a v0.1.0 -m "Manual Beagle 0.1.0"
   git push origin v0.1.0
   ```

   The workflow checks that the tag matches the version and that CHANGELOG.md has its section, builds and checks
   the packages (each installs in a fresh virtual environment and runs), builds the image and checks that it
   serves `/healthz`, has OCR and runs as an ordinary user, then pushes the image for linux/amd64 and
   linux/arm64 and waits for the `pypi` environment's approval. After the upload it makes the GitHub release,
   with the changelog section as its notes and the sdist and wheel attached.
6. **Afterwards,** set the version to the next `.dev0` (`0.2.0.dev0`) and merge that.

If an upload fails half-way, don't reuse the version number for different files: PyPI never accepts the same
file name twice. Fix the problem and release the next patch version. A tag pushed by mistake before
`RELEASE_ENABLED` is on publishes nothing; delete it (`git push origin :refs/tags/v0.1.0`) and tag again.

## Checking a release locally

The same checks the workflow runs, on your own machine, before you tag:

```
python -m pip install build twine
python -m build                      # dist/manualbeagle-<version>.tar.gz and -py3-none-any.whl
twine check --strict dist/*

python -m venv /tmp/mb-check         # a clean environment, for each of the two files
/tmp/mb-check/bin/pip install "dist/manualbeagle-<version>-py3-none-any.whl[web,mcp]"
/tmp/mb-check/bin/manualbeagle --version
/tmp/mb-check/bin/manualbeagle adapters test --fixtures tests/fixtures   # every case, replayed offline

docker build --build-arg VERSION=<version> -t manualbeagle:check .
docker run -d --name mb-check -p 127.0.0.1:8765:8765 -e MANUALBEAGLE_WEB_TOKEN=check manualbeagle:check
curl -fs http://127.0.0.1:8765/healthz
docker exec mb-check python -c "from manualbeagle.library import ocr_available; assert ocr_available()"
docker rm -f mb-check
```

The sdist holds the tracked files that git doesn't ignore (so never a `CLAUDE.local.md`, a `.env` or a
library); look at `tar -tzf dist/*.tar.gz` if you've added files at the top of the repository.

.PHONY: test binary-test music-test music-write-test binary help clean

help:
	@echo "Available targets:"
	@echo "  make clean             - Remove venv, build artifacts, and cache"
	@echo "  make binary            - Build standalone executable with PyInstaller"
	@echo "  make test              - Run tests"
	@echo "  make binary-test       - Build the executable, and test it by running it"
	@echo "  make music-test        - Run tests, and checks against your real Music library (read-only)"
	@echo "  make music-write-test  - Test exporting to the Music app for real, in playlists it then deletes"

clean:
	rm -rf venv build dist *.egg-info .pytest_cache __pycache__


# The virtual environment, with the package and its dependencies, and what's needed to test it. Made again when
# pyproject.toml changes. The executable is built from what's installed here, so without this it would be missing
# whatever isn't.
venv/.installed: pyproject.toml
	python3 -m venv venv
	./venv/bin/pip install -e '.[test]'
	@touch venv/.installed

binary: venv/.installed
	./venv/bin/pip install pyinstaller
	./venv/bin/pyinstaller --noconfirm smarter-playlists.spec

# Extra arguments for pytest, e.g. make test PYTEST_ARGS=-v
test: venv/.installed
	./venv/bin/python -m pytest $(PYTEST_ARGS)

# Builds the standalone executable, then runs it as a user would. Slow, as every command it runs takes seconds to start.
binary-test: binary
	./venv/bin/python -m pytest tests/test_binary.py --binary dist/smarter-playlists

# Also reads your real Music library and talks to the Music app, read-only. Not --music-write, which changes Music.
music-test: venv/.installed
	./venv/bin/python -m pytest --music

# Makes playlists and folders in the Music app, then deletes them. A couple of minutes.
music-write-test: venv/.installed
	./venv/bin/python -m pytest --music-write

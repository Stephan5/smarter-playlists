.PHONY: test binary help clean

test:
	./venv/bin/python -m pytest

binary:
	./venv/bin/pip install pyinstaller
	./venv/bin/pyinstaller --noconfirm smarter-playlists.spec

clean:
	rm -rf venv build dist *.egg-info .pytest_cache __pycache__

help:
	@echo "Available targets:"
	@echo "  make test    - Run tests"
	@echo "  make binary  - Build standalone executable with PyInstaller"
	@echo "  make clean   - Remove venv, build artifacts, and cache"
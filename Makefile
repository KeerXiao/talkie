# talkie — common tasks. See README.md for the full story.

.DEFAULT_GOAL := help
.PHONY: help install test run check record clean

SECONDS ?= 3

help: ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) \
		| awk -F':.*## ' '{printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

install: ## Install everything talkie needs
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv is required. Install it with:"; \
		echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		exit 1; }
	uv sync
	@uv run python -c "import sounddevice" >/dev/null 2>&1 \
		&& echo "audio backend ok" \
		|| $(MAKE) -s portaudio
	@echo
	@echo "Installed. Two things left, both one-time:"
	@echo "  1. export OPENROUTER_API_KEY=sk-or-v1-...   (https://openrouter.ai/keys)"
	@echo "  2. grant Microphone, Input Monitoring and Accessibility to your terminal"
	@echo "     (System Settings > Privacy & Security -- see README.md)"
	@echo
	@echo "Then: make check"

portaudio:
	@echo "sounddevice cannot load PortAudio; installing it."
	@command -v brew >/dev/null 2>&1 || { \
		echo "Homebrew not found. Install PortAudio yourself, then rerun make install."; \
		exit 1; }
	brew install portaudio
	uv sync --reinstall-package sounddevice

check: ## Verify the API key and macOS permissions
	uv run talkie --check

run: ## Start the hotkey loop (Ctrl+Q to dictate)
	uv run talkie

record: ## Record SECONDS and print the transcript, no paste (SECONDS=3)
	uv run talkie --record $(SECONDS)

test: ## Run the test suite
	uv run pytest

clean: ## Remove caches and build output (keeps .venv)
	rm -rf dist build .pytest_cache
	find src -name __pycache__ -type d -exec rm -rf {} +
	find . -maxdepth 2 -name "*.egg-info" -type d -exec rm -rf {} +

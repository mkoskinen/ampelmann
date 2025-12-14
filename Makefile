.PHONY: help install install-dev install-system uninstall \
        test test-cov test-watch lint format typecheck \
        build clean distclean \
        run check validate \
        deploy deploy-checks deploy-dashboard show-cron \
        db-migrate db-backup \
        dev-ollama dev-ntfy

PYTHON := python3
PIP := pip3
PROJECT := ampelmann
VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)

# Directories
PREFIX := /usr/local
SYSCONFDIR := /etc
LOCALSTATEDIR := /var/lib
LOGDIR := /var/log
DATADIR := /var/www/ampelmann

# Colors
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m

#---------------------------------------------------------------------------
# Help
#---------------------------------------------------------------------------

help: ## Show this help
	@echo "$(BLUE)Ampelmann$(NC) - LLM-Powered System Alert Filter"
	@echo ""
	@echo "$(GREEN)Usage:$(NC)"
	@echo "  make [target]"
	@echo ""
	@echo "$(GREEN)Targets:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-18s$(NC) %s\n", $$1, $$2}'

#---------------------------------------------------------------------------
# Development
#---------------------------------------------------------------------------

install-dev: ## Install for development (editable + dev deps)
	$(PIP) install -e ".[dev]"

install: ## Install package
	$(PIP) install .

uninstall: ## Uninstall package
	$(PIP) uninstall -y $(PROJECT)

venv: ## Create virtual environment
	$(PYTHON) -m venv .venv
	@echo "Run: source .venv/bin/activate"

#---------------------------------------------------------------------------
# Testing
#---------------------------------------------------------------------------

test: ## Run tests
	$(PYTHON) -m pytest tests/ -v

test-cov: ## Run tests with coverage
	$(PYTHON) -m pytest tests/ -v --cov=src/ampelmann --cov-report=term-missing --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

test-watch: ## Run tests in watch mode
	$(PYTHON) -m pytest-watch tests/ -v

test-unit: ## Run only unit tests (no LLM/network)
	$(PYTHON) -m pytest tests/ -v -m "not integration"

test-integration: ## Run integration tests (requires Ollama)
	$(PYTHON) -m pytest tests/ -v -m integration

#---------------------------------------------------------------------------
# Code Quality
#---------------------------------------------------------------------------

lint: ## Run linter (ruff)
	$(PYTHON) -m ruff check src/ tests/

lint-fix: ## Run linter and fix issues
	$(PYTHON) -m ruff check src/ tests/ --fix

format: ## Format code (ruff)
	$(PYTHON) -m ruff format src/ tests/

format-check: ## Check formatting without changing
	$(PYTHON) -m ruff format src/ tests/ --check

typecheck: ## Run type checker (mypy)
	$(PYTHON) -m mypy src/ampelmann

check-all: lint typecheck test ## Run all checks

#---------------------------------------------------------------------------
# Build & Package
#---------------------------------------------------------------------------

build: clean ## Build package
	$(PYTHON) -m build

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/
	rm -rf htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

distclean: clean ## Clean everything including venv
	rm -rf .venv/

#---------------------------------------------------------------------------
# Local Development Helpers
#---------------------------------------------------------------------------

run: ## Run ampelmann (due checks only)
	$(PYTHON) -m ampelmann run

run-all: ## Run all checks (ignore schedule)
	$(PYTHON) -m ampelmann run --all --force

check: ## Alias for 'run'
	$(PYTHON) -m ampelmann run

list: ## List all checks
	$(PYTHON) -m ampelmann list

validate: ## Validate check configurations
	$(PYTHON) -m ampelmann validate

dashboard: ## Regenerate dashboard JSON
	$(PYTHON) -m ampelmann dashboard

#---------------------------------------------------------------------------
# System Installation (requires root)
#---------------------------------------------------------------------------

install-system: install ## Full system installation
	@echo "$(BLUE)Creating directories...$(NC)"
	install -d $(SYSCONFDIR)/ampelmann/checks.d
	install -d $(LOCALSTATEDIR)/ampelmann
	install -d $(LOGDIR)/ampelmann
	install -d $(DATADIR)/data/checks
	install -d $(DATADIR)/assets
	@echo "$(BLUE)Installing config...$(NC)"
	@if [ ! -f $(SYSCONFDIR)/ampelmann/config.toml ]; then \
		install -m 644 examples/config.toml.example $(SYSCONFDIR)/ampelmann/config.toml; \
		echo "Installed default config - edit $(SYSCONFDIR)/ampelmann/config.toml"; \
	else \
		echo "Config exists, skipping"; \
	fi
	@echo "$(BLUE)Installing dashboard assets...$(NC)"
	install -m 644 assets/index.html $(DATADIR)/
	install -m 644 assets/style.css $(DATADIR)/assets/
	install -m 644 assets/ampelmann.svg $(DATADIR)/assets/
	@echo "$(GREEN)Done! Run 'make deploy-checks' to install example checks$(NC)"
	@echo "$(GREEN)Then run 'make show-cron' to see how to schedule with cron$(NC)"

deploy-checks: ## Install example checks (won't overwrite existing)
	@echo "$(BLUE)Installing example checks...$(NC)"
	@for f in examples/checks/*.toml; do \
		name=$$(basename $$f); \
		if [ ! -f $(SYSCONFDIR)/ampelmann/checks.d/$$name ]; then \
			install -m 644 $$f $(SYSCONFDIR)/ampelmann/checks.d/; \
			echo "  Installed $$name"; \
		else \
			echo "  Skipped $$name (exists)"; \
		fi \
	done

show-cron: ## Show example cron entry
	@echo "$(BLUE)Add to root crontab (crontab -e):$(NC)"
	@echo ""
	@echo "# Run ampelmann every 15 minutes"
	@echo "*/15 * * * * /usr/local/bin/ampelmann run >> /var/log/ampelmann/cron.log 2>&1"
	@echo ""
	@echo "$(YELLOW)Or for more frequent checks:$(NC)"
	@echo "*/5 * * * * /usr/local/bin/ampelmann run >> /var/log/ampelmann/cron.log 2>&1"

uninstall-system: ## Remove system installation
	@echo "$(RED)Removing ampelmann...$(NC)"
	@echo "$(YELLOW)Remember to remove the cron entry: crontab -e$(NC)"
	@echo ""
	@echo "$(YELLOW)Keeping config and data in:$(NC)"
	@echo "  $(SYSCONFDIR)/ampelmann/"
	@echo "  $(LOCALSTATEDIR)/ampelmann/"
	@echo "  $(LOGDIR)/ampelmann/"
	@echo "Remove manually if desired"

#---------------------------------------------------------------------------
# Database
#---------------------------------------------------------------------------

db-backup: ## Backup database
	@mkdir -p backups
	cp $(LOCALSTATEDIR)/ampelmann/ampelmann.db backups/ampelmann-$$(date +%Y%m%d-%H%M%S).db
	@echo "$(GREEN)Backed up to backups/$(NC)"

db-shell: ## Open database shell
	sqlite3 $(LOCALSTATEDIR)/ampelmann/ampelmann.db

db-stats: ## Show database statistics
	@echo "$(BLUE)Database stats:$(NC)"
	@sqlite3 $(LOCALSTATEDIR)/ampelmann/ampelmann.db \
		"SELECT 'Total runs:', COUNT(*) FROM check_runs; \
		 SELECT 'Alerts:', COUNT(*) FROM check_runs WHERE status='alert'; \
		 SELECT 'Last run:', MAX(run_at) FROM check_runs;"

#---------------------------------------------------------------------------
# Development Mocks
#---------------------------------------------------------------------------

dev-ollama: ## Check Ollama connectivity
	@echo "$(BLUE)Testing Ollama...$(NC)"
	@curl -s http://localhost:11434/api/tags | $(PYTHON) -m json.tool || \
		echo "$(RED)Ollama not responding$(NC)"

dev-ntfy: ## Send test notification (uses config)
	@echo "$(BLUE)Sending test notification...$(NC)"
	$(PYTHON) -m ampelmann alert "Ampelmann test notification" --tags test || \
		echo "$(RED)ntfy failed$(NC)"

dev-check: ## Run a single check in debug mode
	@read -p "Check name: " name; \
	$(PYTHON) -m ampelmann test $$name --verbose

#---------------------------------------------------------------------------
# Release
#---------------------------------------------------------------------------

version: ## Show version
	@echo $(VERSION)

tag: ## Create git tag for current version
	git tag -a v$(VERSION) -m "Release $(VERSION)"
	git push origin v$(VERSION)

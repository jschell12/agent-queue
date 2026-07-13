.PHONY: test lint fmt fmt-check help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

test: ## Run the test suite
	pytest

lint: ## Lint scripts/ and tests/ with ruff
	ruff check scripts/ tests/

fmt: ## Format scripts/ and tests/ with ruff
	ruff format scripts/ tests/

fmt-check: ## Check formatting without writing changes
	ruff format --check scripts/ tests/

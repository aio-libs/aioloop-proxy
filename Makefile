install:
	pip install -U pip
	pip install -r requirements.txt
	pip install -e .
	pre-commit install

lint:
ifdef CI
	pre-commit run --all-files --show-diff-on-failure
else
	pre-commit run --all-files
endif
	mypy --strict --show-error-codes -p aioloop_proxy
	mypy --show-error-codes tests

test:
	python -We -m unittest discover tests

cov:
	coverage run -m unittest discover tests
	@echo "Coverage report"
	@coverage report
	@coverage html
	@echo "Use xdg-open file://`pwd`/htmlcov/index.html"

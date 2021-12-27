install:
	pip install -U pip
	pip install -r requirements.txt
	pip install -e .
	pre-commit install

lint:
	pre-commit run --all-files

test:
	python -We -m unittest discover tests

cov:
	coverage run -m unittest discover tests
	@echo "Coverage report"
	@coverage report
	@coverage html
	@echo "Use xdg-open file://`pwd`/htmlcov/index.html"

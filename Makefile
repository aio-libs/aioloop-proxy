install:
	pip install -U pip
	pip install -r requiremtnts.txy
	pip install -e .
	pre-commit install

lint:
	pre-commit run --all-files

test:
	pytest tests

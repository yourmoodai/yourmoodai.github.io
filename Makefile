.PHONY: build check test preview debug profile infrastructure-check
PYTHON = uv run --python 3.12 --with-requirements requirements-dev.txt python
build:
	$(PYTHON) scripts/legal.py build
check: build
	$(PYTHON) scripts/legal.py check
test:
	$(PYTHON) -m unittest discover -s tests -v
infrastructure-check:
	uv run --python 3.12 --with-requirements requirements-dev.txt cfn-lint infra/receipts/template.yaml
preview: build
	$(PYTHON) -m http.server 1111 --bind 127.0.0.1 --directory public
debug:
	$(PYTHON) -m pdb scripts/legal.py build
profile:
	$(PYTHON) scripts/profile_legal.py

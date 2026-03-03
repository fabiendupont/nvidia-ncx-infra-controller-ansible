# SPDX-FileCopyrightText: Copyright (c) 2026 Fabien Dupont
# SPDX-License-Identifier: Apache-2.0

SPEC := ../bare-metal-manager-rest/openapi/spec.yaml
MODULES_DIR := plugins/modules

.PHONY: generate lint test clean

generate:
	python scripts/generate.py --spec $(SPEC) --output $(MODULES_DIR)

lint:
	python -m py_compile plugins/module_utils/common.py
	python -m py_compile plugins/module_utils/client.py
	python -m py_compile plugins/module_utils/resource.py
	@for f in $(MODULES_DIR)/*.py; do python -m py_compile "$$f"; done

test:
	python -m pytest tests/unit/ -v

clean:
	rm -f $(MODULES_DIR)/*.py
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

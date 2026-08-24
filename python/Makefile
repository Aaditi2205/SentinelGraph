.PHONY: demo evaluate test provenance failure-demo test-order
demo:
	python server.py --port 8000
evaluate:
	python build_sentinelgraph.py --data-dir data/raw
test:
	python -m pytest -q
provenance:
	python scripts/verify_data.py --data-dir data/raw
failure-demo:
	python scripts/failure_demo.py
test-order:
	python scripts/create_test_order.py

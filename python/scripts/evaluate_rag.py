"""Evaluate policy retrieval without any external model call."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from riskpilot.retrieval import PolicyRetriever


cases = json.loads((ROOT / "knowledge" / "rag_eval.json").read_text(encoding="utf-8"))
result = PolicyRetriever(ROOT / "knowledge" / "policies.json").evaluate(cases, top_k=3)
(ROOT / "artifacts" / "rag_eval.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))

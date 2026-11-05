from __future__ import annotations

import argparse
import json

from .engine import EvidenceSource, IncidentIntelligenceEngine
from .providers import ProviderRouter


SAMPLE_LOGS = """\
{"timestamp":"2026-11-05T09:00:00Z","severity":"INFO","event_type":"deployment","service":"checkout","message":"release v2.8.1 deployed","commit_sha":"abc123"}
{"timestamp":"2026-11-05T09:02:00Z","severity":"ERROR","service":"checkout","message":"DatabaseError: connection pool exhausted","exception_type":"DatabaseError","error_code":"503"}
{"timestamp":"2026-11-05T09:03:00Z","severity":"ERROR","service":"checkout","message":"DatabaseError: connection pool exhausted","exception_type":"DatabaseError","error_code":"503"}
{"timestamp":"2026-11-05T09:04:00Z","severity":"ERROR","service":"checkout","message":"DatabaseError: connection pool exhausted","exception_type":"DatabaseError","error_code":"503"}
{"timestamp":"2026-11-05T09:05:00Z","severity":"WARNING","service":"checkout","message":"retry budget nearly exhausted"}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Aftermath local incident investigation demo.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic analysis only; no API calls.")
    args = parser.parse_args()
    engine = IncidentIntelligenceEngine(router=ProviderRouter())
    result = engine.investigate("demo-checkout-001", [EvidenceSource("checkout.jsonl", SAMPLE_LOGS)],
                                use_llm=not args.no_llm)
    print(json.dumps(result.report.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

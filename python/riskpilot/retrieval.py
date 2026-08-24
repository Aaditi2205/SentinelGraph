"""Hybrid retrieval for versioned risk-policy clauses."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class PolicyRetriever:
    def __init__(self, path: Path):
        self.policies = json.loads(path.read_text(encoding="utf-8"))
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        corpus = [f"{item['title']} {item['text']} {' '.join(item['tags'])}" for item in self.policies]
        self.matrix = self.vectorizer.fit_transform(corpus)
        self.tokens = [self._tokens(document) for document in corpus]
        self.average_length = sum(map(len, self.tokens)) / max(len(self.tokens), 1)
        self.document_frequency = Counter()
        for tokens in self.tokens:
            self.document_frequency.update(set(tokens))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _bm25(self, query: str) -> list[float]:
        query_tokens = self._tokens(query)
        scores = []
        for tokens in self.tokens:
            counts = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = counts[token]
                if not frequency:
                    continue
                document_count = len(self.tokens)
                containing = self.document_frequency[token]
                inverse_frequency = math.log(1 + (document_count - containing + 0.5) / (containing + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(self.average_length, 1))
                score += inverse_frequency * frequency * 2.5 / denominator
            scores.append(score)
        maximum = max(scores, default=0.0)
        return [score / maximum if maximum else 0.0 for score in scores]

    def retrieve_many(self, query: str, top_k: int = 3) -> list[dict]:
        """Fuse phrase-sensitive TF-IDF with BM25 lexical retrieval."""
        clean_query = query.strip()[:1000]
        vector = self.vectorizer.transform([clean_query])
        tfidf_scores = cosine_similarity(vector, self.matrix)[0]
        bm25_scores = self._bm25(clean_query)
        combined = [0.58 * float(tfidf) + 0.42 * bm25 for tfidf, bm25 in zip(tfidf_scores, bm25_scores)]
        lowered = clean_query.lower()
        outage_language = any(term in lowered for term in ("timeout", "unavailable", "outage", "down", "missing service", "dependency failed"))
        for index, policy in enumerate(self.policies):
            if policy["id"] in {"POL-02", "POL-09"} and not outage_language:
                combined[index] *= 0.22
        indices = sorted(range(len(combined)), key=lambda index: (-combined[index], self.policies[index]["id"]))[:max(1, top_k)]
        results = []
        for rank, index in enumerate(indices, 1):
            policy = dict(self.policies[index])
            policy.update({
                "rank": rank,
                "score": round(combined[index], 4),
                "tfidfScore": round(float(tfidf_scores[index]), 4),
                "bm25Score": round(float(bm25_scores[index]), 4),
                "retrievalMethod": "word/bigram TF-IDF + BM25 score fusion",
                "version": policy.get("version", "merchant-risk-policy/2026.08"),
                "sourceType": policy.get("sourceType", "merchant-approved policy"),
            })
            results.append(policy)
        return results

    def retrieve(self, query: str) -> dict:
        policy = self.retrieve_many(query, 1)[0]
        policy["similarity"] = policy["score"]
        policy["explanation"] = (
            f"{policy['id']} applies: {policy['text']}"
        )
        return policy

    def evaluate(self, cases: list[dict], top_k: int = 3) -> dict:
        reciprocal_ranks = []
        hits = 0
        rows = []
        for case in cases:
            results = self.retrieve_many(case["query"], top_k)
            ids = [item["id"] for item in results]
            expected = case["expectedPolicyId"]
            rank = ids.index(expected) + 1 if expected in ids else None
            hits += int(rank is not None)
            reciprocal_ranks.append(1 / rank if rank else 0)
            rows.append({"id": case["id"], "expected": expected, "retrieved": ids, "rank": rank})
        return {
            "cases": len(cases), "topK": top_k,
            "recallAtK": hits / max(len(cases), 1),
            "meanReciprocalRank": sum(reciprocal_ranks) / max(len(cases), 1),
            "retrievalMethod": "word/bigram TF-IDF + BM25 score fusion",
            "datasetType": "hand-labelled policy-routing queries including a prompt-injection-style case",
            "limitation": "Small developer-authored routing set; not an independent corpus of real analyst questions.",
            "rows": rows,
        }

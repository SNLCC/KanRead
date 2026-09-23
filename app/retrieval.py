"""Offline bilingual sparse-vector retrieval with page-aware ranking."""
import math
import re
from collections import Counter


def tokens(text):
    text = text.lower()
    english = re.findall(r"[^\W_]+", re.sub(r'[\u4e00-\u9fff]+',' ',text),re.UNICODE)
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return english + [s[i:i+2] for s in chinese for i in range(max(1, len(s)-1))]


def local_vectors(texts):
    """Corpus-fitted sparse TF-IDF embeddings; no downloaded model weights."""
    counts = [Counter(tokens(text)) for text in texts]
    df = Counter(t for count in counts for t in count)
    vectors = []
    for count in counts:
        vector = {t:(1 + math.log(n)) * (1 + math.log((1 + len(texts))/(1 + df[t]))) for t,n in count.items()}
        norm = math.sqrt(sum(v*v for v in vector.values())) or 1
        vectors.append({t:v/norm for t,v in vector.items()})
    return vectors


def local_rerank(query, chunks,limit=8):
    """Exact phrase and query coverage reranking, with original rank as tie-break."""
    terms = set(tokens(query))
    def score(item):
        index, chunk = item
        coverage = len(terms & set(tokens(chunk['text']))) / max(1,len(terms))
        phrase = .2 if query.strip().lower() in chunk['text'].lower() else 0
        return coverage + phrase + .1/(index+1)
    return [c for _,c in sorted(enumerate(chunks), key=score, reverse=True)][:limit]


def rank(query, chunks, document_id, page, mode="close", limit=8):
    query_tokens = Counter(tokens(query))
    if not query_tokens or not chunks:
        return []
    counts = [Counter(tokens(c["text"])) for c in chunks]
    frequencies = Counter(t for c in counts for t in c)
    results = []
    for chunk, count in zip(chunks, counts):
        score = sum((1 + math.log(v)) * math.log(1 + len(chunks) / (1 + frequencies[t]))
                    * (count[t] / (count[t] + 1.2)) for t, v in query_tokens.items() if count[t])
        if not score:
            continue
        same = chunk["document_id"] == document_id
        proximity = 1 / (1 + abs(chunk["page"] - page)) if same else 0
        score *= 1 + (0.4 if mode == "close" else 0.12) * proximity + (0.08 if same else 0)
        results.append({**chunk, "score": round(score, 4)})
    return sorted(results, key=lambda x: x["score"], reverse=True)[:limit]

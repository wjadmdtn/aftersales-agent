"""T005: 向量化。

StubEmbedder 必须确定性——单测和 CI 不能依赖真模型。
真实 embedding 走 BGE（本地），保证评测可复现且不依赖外部 API。
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class StubEmbedder:
    """sha256 派生的确定性伪向量。

    语义相似度为随机值——它只用于验证编排逻辑（过滤、排序、阈值、门禁），
    不能用于标定阈值；标定必须用真实 embedder。
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
            for b in digest:
                if len(out) >= self.dim:
                    break
                out.append(b / 255.0 - 0.5)
            counter += 1
        norm = math.sqrt(sum(v * v for v in out)) or 1.0
        return [v / norm for v in out]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class BgeEmbedder:
    """bge 系列本地推理。仅在本地/生产使用，CI 用 StubEmbedder。

    dim 由模型决定，不要沿用外部传入值——传错维度会让 pgvector 建出错误的表。
    默认用 bge-small-zh-v1.5：m3 在 hf-mirror 上动辄限流、体积 2.2GB，
    而 short-zh 在中文短问句上召回与 m3 相当，换模型必须重跑标定。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover - 只在真模型环境触发
            raise RuntimeError(
                "sentence-transformers not installed; use rag_embedder=stub or pip install it"
            ) from e
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


def build_embedder(kind: str, dim: int) -> Embedder:
    if kind == "stub":
        return StubEmbedder(dim)
    if kind == "bge":
        return BgeEmbedder()
    raise ValueError(f"unknown embedder: {kind}")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)

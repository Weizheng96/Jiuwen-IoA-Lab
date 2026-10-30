"""服务发现：A2X registry 作库进程内调用（不挂任何 A2X 路由）。

固定顺序：RegistryService(database_dir) → create_dataset → 种子 register_batch → 按领域确定性建分类树（market/taxonomy.py，零 LLM）
→ startup() → 之后再注册（运行期注册按声明的领域 assign 进叶类）。检索默认 A2X 分类树导航（LLM），树不可用或未配 key 时走向量退路
（远端嵌入 + numpy 余弦；不装 torch、不经 chromadb）。A2X 副本的 Agent Card 只放索引字段。
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger("ioalab.discovery")
DATASET = "ioalab"


class Discovery:
    def __init__(self, database_dir: Path, *, a2x_home: Path, llm_configured: bool, embed_fn=None, taxonomy_seed: Path | None = None) -> None:
        os.environ["A2X_REGISTRY_HOME"] = str(a2x_home)          # 只影响 llm_apikey.json 的位置；路径一律显式
        from a2x_registry.register.service import RegistryService
        self.database_dir = database_dir.resolve()
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.svc = RegistryService(database_dir=self.database_dir)
        self.taxonomy_seed = taxonomy_seed
        self.llm_configured = llm_configured
        self.embed_fn = embed_fn                      # texts → 向量列表；None 则字符二元组退路
        self._searcher = None
        self._bigram_vocab: dict[str, int] = {}
        self._vectors: dict[str, Any] = {}
        self._bigram_vectors: dict[str, Any] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="a2x-search")
        self.tree_timeout = 60.0
        self._lock = threading.Lock()
        self._started = False
        self.last_mode = ""

    # ── 数据集与注册 ──────────────────────────────────────────────────
    def ensure_dataset(self) -> None:
        if not self.svc.dataset_exists(DATASET):
            self.svc.create_dataset(DATASET, formats={"a2a": "v0.0"})

    def entries(self) -> list[dict]:
        return self.svc.list_services(DATASET)

    def register_batch(self, cards: list[tuple[str, Mapping[str, Any]]]) -> None:
        """种子：一次写盘。``cards`` 是 (service_id, agent_card) 列表，card 顶层带 ``a2c`` 与 ``status``。"""
        from a2x_registry.register.models import AgentCard, RegistryEntry
        self.ensure_dataset()
        entries = [RegistryEntry(service_id=sid, type="a2a", source="api_config", agent_card=AgentCard(**dict(card)))
                   for sid, card in cards]
        self.svc.register_batch(entries, DATASET)

    def register_one(self, service_id: str, card: Mapping[str, Any], *, category: str | None = None) -> None:
        """运行期注册：写入注册表，再按声明的领域确定性加进分类树叶类（没有树时只靠向量退路检索到）。"""
        from a2x_registry.register.models import AgentCard, RegisterA2ARequest
        self.ensure_dataset()
        self.svc.register_a2a(RegisterA2ARequest(service_id=service_id, dataset=DATASET, agent_card=AgentCard(**dict(card))))
        self._vectors.pop(service_id, None)
        self._bigram_vectors.pop(service_id, None)
        self.assign(service_id, category)

    def update_card(self, service_id: str, card: Mapping[str, Any]) -> None:
        """只改 a2c / status 等索引字段（不动 name / description，不触发树失效）。"""
        if self.svc.get_entry(DATASET, service_id) is None:
            return
        updates = {k: v for k, v in dict(card).items() if k in ("a2c", "status", "url", "version")}
        self.svc.update_service(DATASET, service_id, updates)

    def get_card(self, service_id: str) -> dict | None:
        e = self.svc.get_entry(DATASET, service_id)
        return e.agent_card.model_dump() if e is not None and e.agent_card is not None else None

    def startup(self) -> None:
        self.ensure_dataset()
        self.svc.startup()
        self._started = True
        self._searcher = None

    # ── 分类树 ────────────────────────────────────────────────────────
    def taxonomy_state(self) -> str:
        st = self.svc.check_taxonomy_state(DATASET) if self._started else None
        return st.value if st is not None else "unknown"

    def build_domains(self, categories: list[Mapping[str, Any]], members: Mapping[str, list[str]]) -> str:
        """按领域规格确定性建树（零 LLM）：写树文件 → 回写 service_hash → 重新载入；返回树状态。"""
        from . import taxonomy
        self.ensure_dataset()
        self.svc.startup()                      # 注册表载入后 list_services 才非空，哈希才算得出
        taxonomy.build(categories, members, self.svc.taxonomy_dir(DATASET))
        self._write_hash()
        self.svc.startup()
        self._started = True
        self._searcher = None
        return self.taxonomy_state()

    def assign(self, service_id: str, category: str | None) -> str | None:
        """运行期把服务确定性归入叶类（不存在的叶类退到 cat_misc）；没有树时返回 None。"""
        from . import taxonomy
        tdir = self.svc.taxonomy_dir(DATASET)
        if not (tdir / "taxonomy.json").exists():
            return None
        cid = taxonomy.assign(tdir, service_id, category or taxonomy.MISC["id"])
        self.svc.startup()
        self._write_hash()
        self.svc.startup()
        self._searcher = None
        return cid

    def categories(self) -> list[dict]:
        from . import taxonomy
        tdir = self.svc.taxonomy_dir(DATASET)
        return taxonomy.categories(tdir) if (tdir / "taxonomy.json").exists() else []

    def _write_hash(self) -> None:
        from a2x_registry.register.service import _compute_build_hash
        p = self.svc.taxonomy_dir(DATASET) / "build_config.json"
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        services = self.svc.list_services(DATASET)
        if not services:      # 注册表未 startup() 时缓存为空，空哈希会把树标成 unavailable
            return
        cfg["service_hash"] = _compute_build_hash(services)
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 检索 ──────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 8, *, prefer_tree: bool = True) -> list[dict]:
        """返回 [{id, name, description, card}]；``self.last_mode`` 记录用了分类树还是向量。"""
        ids: list[str] = []
        if prefer_tree and self.llm_configured and self.taxonomy_state() == "available":
            try:
                # 树检索里每次模型调用都带 A2X 自己的重试，网络整段不通时会拖到数分钟：限时，超时退到向量
                ids = self._pool.submit(self._tree_search, query).result(timeout=self.tree_timeout)
                self.last_mode = "a2x-tree"
            except Exception as e:  # noqa: BLE001
                log.warning("A2X tree search failed, falling back to vector: %s", e)
        if ids:
            # 树导航偶尔进错叶类（建树时的归类与查询措辞不一致）：并上向量前 3 名再统一重排，正解不至于整组缺席
            extra = [i for i in self._vector_search(query, 3) if i not in ids]
            if extra:
                ids = self._rerank(query, ids + extra)
        else:
            ids = self._vector_search(query, top_k)
            self.last_mode = "vector"
        out = []
        for sid in ids[:top_k]:
            card = self.get_card(sid)
            if card is None:
                continue
            out.append({"id": sid, "name": card.get("name", ""), "description": card.get("description", ""), "card": card})
        return out

    def _tree_search(self, query: str) -> list[str]:
        from a2x_registry.a2x.search.a2x_search import A2XSearch
        with self._lock:
            if self._searcher is None:
                # get_all：模型只做类目导航，取回叶类整组候选；组内再按向量相似度重排（get_important 让模型在组内
                # 挑「重要」的，措辞稍变就把正解漏掉）
                self._searcher = A2XSearch(taxonomy_path=str(self.svc.taxonomy_path(DATASET)),
                                           class_path=str(self.svc.class_path(DATASET)),
                                           service_path=str(self.svc.dataset_dir(DATASET) / "service.json"),
                                           max_workers=2, parallel=False, mode="get_all")
            results, _stats = self._searcher.search(query)
        ids = [r.id for r in results]
        return self._rerank(query, ids) if len(ids) > 1 else ids

    def _rerank(self, query: str, ids: list[str]) -> list[str]:
        """树给的是候选集（整组可达 30 个），再按向量相似度排一次，top_k 截断才不会把正解切掉。"""
        services = {s["id"]: s for s in self.svc.list_services(DATASET)}
        scored = self._score(query, [services[i] for i in ids if i in services])
        return [i for _, i in scored]

    def _score(self, query: str, services: list[dict]) -> list[tuple[float, str]]:
        """按相似度降序：先用远端嵌入（有 key 时），网络不通就退到字符二元组——两套向量各有缓存，不混用。"""
        if self.embed_fn is not None:
            try:
                return self._score_with(query, services, self._vectors, self._embed_remote)
            except Exception as e:  # noqa: BLE001
                log.warning("embedding unavailable, falling back to char-bigram: %s", e)
        return self._score_with(query, services, self._bigram_vectors, self._embed_bigram)

    def _score_with(self, query: str, services: list[dict], cache: dict, embed) -> list[tuple[float, str]]:
        import numpy as np
        missing = [s for s in services if s["id"] not in cache]
        if missing:
            for s, v in zip(missing, embed([f"{s['name']}：{s['description']}" for s in missing])):
                cache[s["id"]] = v
        if not services:
            return []
        q = embed([query])[0]
        width = q.shape[0]
        return sorted(((float(np.dot(q, self._pad(cache[s["id"]], width))), s["id"]) for s in services), reverse=True)

    def _embed_remote(self, texts: list[str]):
        """DashScope text-embedding-v4（不在进程里装 torch——sentence-transformers 一加载，之后的异步模型调用就会拖到超时）。"""
        import numpy as np
        v = np.asarray(self.embed_fn(texts), dtype="float32")
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-9)

    def _embed_bigram(self, texts: list[str]):
        """没有 key 或网络不通时的退路：字符二元组计数向量（numpy 向量化）。"""
        import numpy as np
        vocab = self._bigram_vocab
        rows = []
        for t in texts:
            grams = [t[i:i + 2] for i in range(len(t) - 1)]
            for g in grams:
                vocab.setdefault(g, len(vocab))
            rows.append(grams)
        v = np.zeros((len(texts), max(len(vocab), 1)), dtype="float32")
        for i, grams in enumerate(rows):
            for g in grams:
                v[i, vocab[g]] += 1.0
        self._bigram_vectors = {k: self._pad(x, v.shape[1]) for k, x in self._bigram_vectors.items()}
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-9)

    @staticmethod
    def _pad(x, width):
        import numpy as np
        if x.shape[0] >= width:
            return x
        out = np.zeros(width, dtype="float32")
        out[:x.shape[0]] = x
        return out

    def _vector_search(self, query: str, top_k: int) -> list[str]:
        scored = self._score(query, self.svc.list_services(DATASET))
        return [sid for _, sid in scored[:top_k]]


__all__ = ["Discovery", "DATASET"]

"""
Milvus 向量服务 - 学习资源语义检索与推荐
使用 TF-IDF 做 Embedding（无需额外模型下载），Milvus 做向量存储与检索
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
import numpy as np

try:
    from pymilvus import MilvusClient
except Exception as exc:
    MilvusClient = None
    _PYMILVUS_IMPORT_ERROR = exc
else:
    _PYMILVUS_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# 全局客户端
_milvus_client: Optional[MilvusClient] = None

# 向量维度（TF-IDF 词汇表大小，取前500个高频词）
VECTOR_DIM = 500
COLLECTION_NAME = "learning_resources"

# 全局词汇表（首次 build 后填充）
_vocabulary: list[str] = []


# ===== TF-IDF 向量化 =====

def _tokenize(text: str) -> list[str]:
    """简单中文分词：按常见分隔符 + 2-gram 字符切分"""
    if not text:
        return []
    # 清理特殊字符
    text = re.sub(r'[^\w\u4e00-\u9fff]', ' ', text)
    # 按空格切分
    words = text.split()
    # 对中文做 2-gram
    tokens = []
    for word in words:
        if len(word) <= 2:
            tokens.append(word)
        else:
            tokens.append(word)
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
    return tokens


def _build_vocabulary(texts: list[str]) -> list[str]:
    """构建 TF-IDF 词汇表（取词频最高的 N 个词）"""
    word_freq = {}
    for text in texts:
        for token in _tokenize(text):
            word_freq[token] = word_freq.get(token, 0) + 1
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in sorted_words[:VECTOR_DIM]]


def _compute_tfidf(text: str, vocabulary: list[str]) -> list[float]:
    """计算文本的 TF-IDF 向量"""
    if not vocabulary:
        return [0.0] * VECTOR_DIM

    tokens = _tokenize(text)
    total = len(tokens) or 1
    vec = [0.0] * len(vocabulary)

    for token in tokens:
        if token in vocabulary:
            idx = vocabulary.index(token)
            vec[idx] += 1.0 / total

    # L2 归一化
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = (np.array(vec) / norm).tolist()
    return vec


def _resource_to_text(resource: dict) -> str:
    """将学习资源转为可索引文本"""
    parts = []
    if resource.get("title"):
        parts.append(str(resource["title"]))
    if resource.get("topic"):
        parts.append(str(resource["topic"]))
    if resource.get("resource_type"):
        parts.append(str(resource["resource_type"]))
    # 提取 content 中的文本
    content = resource.get("content", {})
    if isinstance(content, dict):
        for v in content.values():
            if isinstance(v, str):
                parts.append(v[:500])
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        parts.append(str(item.get("question", ""))[:200])
                        parts.append(str(item.get("explanation", ""))[:200])
                    elif isinstance(item, str):
                        parts.append(item[:200])
    elif isinstance(content, str):
        parts.append(content[:500])
    return " ".join(parts)


# ===== Milvus 连接与集合管理 =====

async def init_milvus(uri: str = "http://localhost:19530") -> MilvusClient:
    """初始化 Milvus 连接 + 创建集合"""
    global _milvus_client

    if MilvusClient is None:
        raise RuntimeError(f"pymilvus is unavailable: {_PYMILVUS_IMPORT_ERROR}")

    logger.info(f"[Milvus] 连接: {uri}")
    _milvus_client = MilvusClient(uri=uri, timeout=10)

    # 检查集合是否存在
    collections = _milvus_client.list_collections()
    if COLLECTION_NAME not in collections:
        logger.info(f"[Milvus] 创建集合: {COLLECTION_NAME}")
        _milvus_client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=VECTOR_DIM,
            metric_type="COSINE",
            auto_id=True,
        )
        # 创建索引
        _milvus_client.create_index(
            collection_name=COLLECTION_NAME,
            field_name="vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128}
        )
    else:
        logger.info(f"[Milvus] 集合已存在: {COLLECTION_NAME}")

    logger.info("[Milvus] 连接成功")
    return _milvus_client


async def close_milvus():
    """关闭 Milvus 连接"""
    global _milvus_client
    if _milvus_client:
        _milvus_client.close()
        _milvus_client = None
        logger.info("[Milvus] 连接已关闭")


def get_milvus() -> Optional[MilvusClient]:
    return _milvus_client


async def reconnect_milvus(uri: str = "http://localhost:19530") -> Optional[MilvusClient]:
    """Milvus 重连（容器重启后自动恢复）"""
    global _milvus_client
    if _milvus_client is not None:
        return _milvus_client
    try:
        return await init_milvus(uri)
    except Exception as e:
        logger.warning(f"[Milvus] 重连失败: {e}")
        return None


# ===== 向量存储 =====

async def insert_resource(resource: dict) -> Optional[str]:
    """将学习资源向量化并存入 Milvus"""
    client = get_milvus()
    if not client:
        logger.warning("[Milvus] 客户端未初始化，跳过向量存储")
        return None

    try:
        text = _resource_to_text(resource)

        # 使用已有词汇表计算向量
        vector = _compute_tfidf(text, _vocabulary)

        data = [{
            "vector": vector,
            "resource_type": resource.get("resource_type", ""),
            "title": resource.get("title", "")[:300],
            "topic": resource.get("topic", "")[:200],
            "user_id": resource.get("user_id", 0),
            "text_preview": text[:1000]
        }]

        result = client.insert(collection_name=COLLECTION_NAME, data=data)
        ids = result.get("ids", [])
        logger.info(f"[Milvus] 资源已入库: id={ids[0] if ids else '?'} type={resource.get('resource_type')}")
        return str(ids[0]) if ids else None

    except Exception as e:
        logger.error(f"[Milvus] 插入失败: {e}")
        return None


async def rebuild_vocabulary():
    """从已有资源重建词汇表（启动时调用一次）"""
    global _vocabulary
    client = get_milvus()
    if not client:
        return

    try:
        # 查询所有已有资源的文本
        results = client.query(
            collection_name=COLLECTION_NAME,
            filter="",
            output_fields=["text_preview"],
            limit=1000
        )
        if results:
            texts = [r.get("text_preview", "") for r in results]
            _vocabulary = _build_vocabulary(texts)
            logger.info(f"[Milvus] 词汇表重建完成: {len(_vocabulary)} 词")
        else:
            # 空集合，使用默认词汇
            _vocabulary = []
    except Exception as e:
        logger.warning(f"[Milvus] 词汇表构建跳过: {e}")


# ===== 语义检索 =====

async def search_resources(
    query: str,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    limit: int = 10
) -> list[dict]:
    """语义搜索相似学习资源"""
    client = get_milvus()
    if not client or not _vocabulary:
        logger.warning("[Milvus] 无法搜索（客户端未初始化或词汇表为空）")
        return []

    try:
        query_vec = _compute_tfidf(query, _vocabulary)

        filter_expr = ""
        conditions = []
        if user_id:
            conditions.append(f"user_id == {user_id}")
        if resource_type:
            conditions.append(f'resource_type == "{resource_type}"')
        if conditions:
            filter_expr = " && ".join(conditions)

        results = client.search(
            collection_name=COLLECTION_NAME,
            data=[query_vec],
            limit=limit,
            filter=filter_expr,
            output_fields=["resource_type", "title", "topic", "user_id", "text_preview"]
        )

        if results and results[0]:
            return [
                {
                    "id": str(hit.get("id", "")),
                    "score": round(hit.get("distance", 0), 4),
                    "resource_type": hit.get("entity", {}).get("resource_type", ""),
                    "title": hit.get("entity", {}).get("title", ""),
                    "topic": hit.get("entity", {}).get("topic", ""),
                    "preview": hit.get("entity", {}).get("text_preview", "")[:200]
                }
                for hit in results[0]
            ]
        return []

    except Exception as e:
        logger.error(f"[Milvus] 搜索失败: {e}")
        return []


async def delete_user_resources(user_id: int):
    """删除用户的所有向量资源"""
    client = get_milvus()
    if not client:
        return
    try:
        client.delete(collection_name=COLLECTION_NAME, filter=f"user_id == {user_id}")
        logger.info(f"[Milvus] 已删除 user_id={user_id} 的所有向量")
    except Exception as e:
        logger.warning(f"[Milvus] 删除失败: {e}")

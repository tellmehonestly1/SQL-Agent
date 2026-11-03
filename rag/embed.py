"""Embedding 模型：本地 bge-small-zh-v1.5（中文），离线跑、不调 API。

为什么选本地模型：DeepSeek 没有 embedding 接口；bge-small-zh 是中文小模型，
效果够用、体积小（约 100MB），sentence-transformers 直接加载。

默认离线加载（local_files_only=True），两个原因：
1. 国内连 huggingface.co 会卡死；
2. 当前 sentence-transformers/transformers 新版本，在线加载此模型会报
   "Unrecognized processing class"，离线路径反而稳定。

需要首次下载模型时：设 HF_LOCAL_FILES_ONLY=0（配合 HF_ENDPOINT=https://hf-mirror.com 走镜像）。
"""

import os
from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings


@lru_cache(maxsize=1)
def build_embeddings() -> HuggingFaceEmbeddings:
    local_files_only = os.environ.get("HF_LOCAL_FILES_ONLY", "1") != "0"
    return HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        model_kwargs={"local_files_only": local_files_only},
        # 归一化：pgvector 用余弦距离（<=>），向量归一化后余弦等价于内积，检索更准
        encode_kwargs={"normalize_embeddings": True},
    )

"""Embedding 模型：本地 bge-small-zh-v1.5（中文），离线跑、不调 API。

为什么选本地模型：DeepSeek 没有 embedding 接口；bge-small-zh 是中文小模型，
效果够用、体积小（约 100MB），sentence-transformers 直接加载，国内网络友好。
"""
from langchain_huggingface import HuggingFaceEmbeddings


def build_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        # 离线：只用本地缓存，不去连 HuggingFace Hub（国内连 huggingface.co 会卡死）
        model_kwargs={"local_files_only": True},
        # 归一化：pgvector 用余弦距离（<=>），向量归一化后余弦等价于内积，检索更准
        encode_kwargs={"normalize_embeddings": True},
    )

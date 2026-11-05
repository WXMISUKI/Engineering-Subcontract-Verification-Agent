"""工程分包审查的集合命名权威。"""

BS_COLLECTION_PREFIX = "bs_"


def bs_collection(collection_name: str) -> str:
    """返回分包审查知识库的完整集合名。"""
    return f"{BS_COLLECTION_PREFIX}{collection_name}"

"""
DocDB - 一个支持 JSON 文档存储与查询的文档数据库
支持: 文档存储、二级索引、查询语言、聚合管道、事务
"""

from .core.database import Database
from .core.collection import Collection
from .core.document import Document

__version__ = "0.1.0"
__all__ = ["Database", "Collection", "Document"]

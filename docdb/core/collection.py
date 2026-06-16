"""
Collection 类 - 文档集合

集合是文档的容器，相当于关系型数据库中的表。
每个集合维护:
- 文档存储（内存索引 + 磁盘持久化）
- 二级索引列表
- 集合级别的配置
"""

import os
import json
import threading
from typing import Dict, List, Optional, Any, Iterator, Callable

from .document import Document
from ..storage.document_store import DocumentStore
from ..index.index_manager import IndexManager
from ..query.parser import QueryParser
from ..query.filter_tree import FilterNode
from ..execution.optimizer import QueryOptimizer
from ..execution.executor import QueryExecutor


class Collection:
    """
    文档集合类，提供文档的增删改查和索引管理
    """

    def __init__(self, name: str, data_dir: str):
        """
        初始化集合
        
        Args:
            name: 集合名称
            data_dir: 数据存储目录
        """
        self.name = name
        self.data_dir = data_dir
        self._collection_dir = os.path.join(data_dir, name)
        os.makedirs(self._collection_dir, exist_ok=True)

        self._doc_store = DocumentStore(self._collection_dir)
        self._index_manager = IndexManager(self._collection_dir, self._doc_store)
        self._query_parser = QueryParser()
        self._optimizer = QueryOptimizer(self._index_manager)
        self._executor = QueryExecutor(self._doc_store, self._index_manager)
        self._lock = threading.RWLock() if hasattr(threading, "RWLock") else threading.RLock()

        self._metadata_file = os.path.join(self._collection_dir, "_metadata.json")
        self._load_metadata()

    def _load_metadata(self) -> None:
        """加载集合元数据"""
        if os.path.exists(self._metadata_file):
            with open(self._metadata_file, "r", encoding="utf-8") as f:
                self._metadata = json.load(f)
        else:
            self._metadata = {
                "name": self.name,
                "document_count": 0,
                "indexes": {},
                "created_at": None,
            }
            self._save_metadata()

    def _save_metadata(self) -> None:
        """保存集合元数据"""
        with open(self._metadata_file, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, indent=2, ensure_ascii=False)

    @property
    def doc_store(self) -> DocumentStore:
        """获取文档存储"""
        return self._doc_store

    @property
    def index_manager(self) -> IndexManager:
        """获取索引管理器"""
        return self._index_manager

    def count(self) -> int:
        """获取文档总数"""
        return self._doc_store.count()

    def insert_one(self, data: Dict[str, Any]) -> Document:
        """
        插入单个文档
        
        Args:
            data: 文档数据
            
        Returns:
            插入后的文档对象
        """
        with self._lock:
            doc = Document(data)
            self._doc_store.insert(doc)
            self._index_manager.index_document(doc)
            self._metadata["document_count"] = self._doc_store.count()
            self._save_metadata()
            return doc

    def insert_many(self, data_list: List[Dict[str, Any]]) -> List[Document]:
        """
        批量插入文档
        
        Args:
            data_list: 文档数据列表
            
        Returns:
            插入后的文档列表
        """
        with self._lock:
            docs = []
            for data in data_list:
                doc = Document(data)
                self._doc_store.insert(doc)
                self._index_manager.index_document(doc)
                docs.append(doc)
            self._metadata["document_count"] = self._doc_store.count()
            self._save_metadata()
            return docs

    def find_one(
        self,
        query: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Document]:
        """
        查询单个文档
        
        Args:
            query: 查询条件
            projection: 投影字段
            
        Returns:
            匹配的文档或None
        """
        results = self.find(query, projection=projection, limit=1)
        return results[0] if results else None

    def find(
        self,
        query: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, int]] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> List[Document]:
        """
        查询文档
        
        Args:
            query: 查询条件字典
            projection: 投影字段
            sort: 排序规则 {field: 1/-1}
            limit: 结果数量限制
            skip: 跳过的文档数
            
        Returns:
            匹配的文档列表
        """
        with self._lock:
            filter_tree = self._query_parser.parse_query(query or {})
            parsed_projection = self._query_parser.parse_projection(projection)
            query_plan = self._optimizer.optimize(filter_tree, parsed_projection, sort)
            results = self._executor.execute(query_plan)

            if skip:
                results = results[skip:]
            if limit:
                results = results[:limit]

            return results

    def update_one(
        self, query: Dict[str, Any], update_data: Dict[str, Any]
    ) -> int:
        """
        更新单个文档
        
        Args:
            query: 查询条件
            update_data: 更新数据（支持 $set, $unset 等操作符）
            
        Returns:
            更新的文档数量
        """
        with self._lock:
            doc = self.find_one(query)
            if not doc:
                return 0

            old_doc = doc.clone()
            self._apply_update(doc, update_data)
            doc.increment_version()

            self._doc_store.update(doc)
            self._index_manager.update_document(old_doc, doc)

            return 1

    def update_many(
        self, query: Dict[str, Any], update_data: Dict[str, Any]
    ) -> int:
        """
        更新多个文档
        
        Args:
            query: 查询条件
            update_data: 更新数据
            
        Returns:
            更新的文档数量
        """
        with self._lock:
            docs = self.find(query)
            if not docs:
                return 0

            count = 0
            for doc in docs:
                old_doc = doc.clone()
                self._apply_update(doc, update_data)
                doc.increment_version()

                self._doc_store.update(doc)
                self._index_manager.update_document(old_doc, doc)
                count += 1

            return count

    def _apply_update(self, doc: Document, update_data: Dict[str, Any]) -> None:
        """
        应用更新操作到文档
        
        支持的更新操作符:
        - $set: 设置字段值
        - $unset: 删除字段
        - $inc: 数值递增
        - $push: 数组追加
        - $pull: 数组移除
        """
        if "$set" in update_data:
            for path, value in update_data["$set"].items():
                doc.set(path, value)

        if "$unset" in update_data:
            fields = update_data["$unset"]
            if isinstance(fields, dict):
                for path in fields.keys():
                    doc.delete_field(path)
            elif isinstance(fields, list):
                for path in fields:
                    doc.delete_field(path)

        if "$inc" in update_data:
            for path, amount in update_data["$inc"].items():
                current = doc.get(path, 0)
                if isinstance(current, (int, float)):
                    doc.set(path, current + amount)

        if "$push" in update_data:
            for path, value in update_data["$push"].items():
                current = doc.get(path, [])
                if isinstance(current, list):
                    current.append(value)
                    doc.set(path, current)

        if "$pull" in update_data:
            for path, value in update_data["$pull"].items():
                current = doc.get(path, [])
                if isinstance(current, list):
                    while value in current:
                        current.remove(value)
                    doc.set(path, current)

    def delete_one(self, query: Dict[str, Any]) -> int:
        """
        删除单个文档
        
        Args:
            query: 查询条件
            
        Returns:
            删除的文档数量
        """
        with self._lock:
            doc = self.find_one(query)
            if not doc:
                return 0

            self._doc_store.delete(doc.id)
            self._index_manager.unindex_document(doc)
            self._metadata["document_count"] = self._doc_store.count()
            self._save_metadata()
            return 1

    def delete_many(self, query: Dict[str, Any]) -> int:
        """
        删除多个文档
        
        Args:
            query: 查询条件
            
        Returns:
            删除的文档数量
        """
        with self._lock:
            docs = self.find(query)
            if not docs:
                return 0

            for doc in docs:
                self._doc_store.delete(doc.id)
                self._index_manager.unindex_document(doc)

            self._metadata["document_count"] = self._doc_store.count()
            self._save_metadata()
            return len(docs)

    def create_index(
        self,
        field: str,
        index_type: str = "btree",
        unique: bool = False,
        name: Optional[str] = None,
    ) -> str:
        """
        创建二级索引
        
        Args:
            field: 索引字段路径（支持嵌套字段）
            index_type: 索引类型 (btree, hash)
            unique: 是否唯一索引
            name: 索引名称
            
        Returns:
            索引名称
        """
        with self._lock:
            index_name = name or f"{field.replace('.', '_')}_{index_type}"
            self._index_manager.create_index(
                index_name, field, index_type, unique
            )
            self._metadata["indexes"][index_name] = {
                "field": field,
                "type": index_type,
                "unique": unique,
            }
            self._save_metadata()
            return index_name

    def drop_index(self, index_name: str) -> bool:
        """删除索引"""
        with self._lock:
            if index_name in self._metadata["indexes"]:
                self._index_manager.drop_index(index_name)
                del self._metadata["indexes"][index_name]
                self._save_metadata()
                return True
            return False

    def list_indexes(self) -> List[Dict[str, Any]]:
        """列出所有索引"""
        return [
            {"name": name, **info}
            for name, info in self._metadata["indexes"].items()
        ]

    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        聚合查询
        
        Args:
            pipeline: 聚合管道阶段列表
            例如: [
                {"$match": {"status": "active"}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 10}
            ]
            
        Returns:
            聚合结果列表
        """
        with self._lock:
            from ..aggregation.pipeline import AggregationPipeline
            pipeline_obj = AggregationPipeline(pipeline, self)
            return pipeline_obj.execute()

    def find_by_id(self, doc_id: str) -> Optional[Document]:
        """根据ID查找文档"""
        return self._doc_store.get(doc_id)

    def iterate(self) -> Iterator[Document]:
        """迭代所有文档"""
        return self._doc_store.iterate()

    def drop(self) -> None:
        """删除集合及其所有数据"""
        with self._lock:
            self._index_manager.drop_all_indexes()
            self._doc_store.close()
            import shutil
            shutil.rmtree(self._collection_dir, ignore_errors=True)

    def close(self) -> None:
        """关闭集合"""
        with self._lock:
            self._save_metadata()
            self._index_manager.close()
            self._doc_store.close()

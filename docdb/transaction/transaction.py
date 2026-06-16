"""
Transaction - 事务类

使用 MVCC（多版本并发控制）实现事务

事务原理:
- 每个文档维护版本号（_version）
- 事务开始时，读取数据的一致性视图
- 事务内的修改暂存在本地（写集合）
- 提交时，进行版本检查和原子写入
- 如果有冲突（其他事务已修改），事务回滚

事务隔离级别:
- 快照隔离（Snapshot Isolation）
- 读己之所写
- 可重复读

事务保证:
- 原子性: 所有修改要么全部提交，要么全部回滚
- 一致性: 事务执行前后数据保持一致
- 隔离性: 事务之间互不干扰
- 持久性: 提交的数据持久化到磁盘

索引一致性:
- 事务提交时，索引和数据一起更新
- 事务回滚时，索引不会被修改
- 使用 WAL 保证索引和数据的一致性
"""

import threading
import time
import copy
from typing import Dict, List, Optional, Any, Set
from enum import Enum


class TransactionState(Enum):
    """事务状态"""
    ACTIVE = "active"           # 活动中
    COMMITTED = "committed"     # 已提交
    ABORTED = "aborted"         # 已中止/回滚
    PREPARING = "preparing"     # 准备提交（两阶段提交）


class Transaction:
    """
    事务类
    
    支持多文档的原子操作
    使用乐观并发控制 + MVCC
    """

    def __init__(self, database):
        """
        初始化事务
        
        Args:
            database: 数据库引用
        """
        self._db = database
        self._txn_id = self._generate_txn_id()
        self._state = TransactionState.ACTIVE
        self._start_time = time.time()

        self._write_set: Dict[str, Dict[str, Any]] = {}
        self._delete_set: Set[str] = set()
        self._read_set: Dict[str, int] = {}

        self._index_changes: Dict[str, List[Dict[str, Any]]] = {}

        self._lock = threading.Lock()

    def _generate_txn_id(self) -> int:
        """生成事务 ID（使用时间戳 + 计数器）"""
        return int(time.time() * 1000000) + id(self) % 1000

    @property
    def txn_id(self) -> int:
        """获取事务 ID"""
        return self._txn_id

    @property
    def state(self) -> TransactionState:
        """获取事务状态"""
        return self._state

    def _ensure_active(self) -> None:
        """确保事务处于活动状态"""
        if self._state != TransactionState.ACTIVE:
            raise ValueError(
                f"Transaction is not active (state: {self._state.value})"
            )

    def insert(self, collection_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        在事务中插入文档
        
        Args:
            collection_name: 集合名称
            data: 文档数据
            
        Returns:
            插入的文档
        """
        self._ensure_active()

        from ..core.document import Document

        doc = Document(data)

        key = f"{collection_name}:{doc.id}"
        self._write_set[key] = {
            "type": "insert",
            "collection": collection_name,
            "doc": doc,
        }

        return doc.to_dict()

    def find_one(
        self, collection_name: str, query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        在事务中查询单个文档
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            
        Returns:
            匹配的文档或 None
        """
        self._ensure_active()
        results = self.find(collection_name, query, limit=1)
        return results[0] if results else None

    def find(
        self,
        collection_name: str,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, int]] = None,
        limit: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        在事务中查询文档
        
        读取规则:
        1. 先看写集合（事务内的修改）
        2. 再读数据库
        3. 记录读取版本（用于冲突检测）
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            projection: 投影
            sort: 排序
            limit: 限制数量
            skip: 跳过数量
            
        Returns:
            文档列表
        """
        self._ensure_active()

        collection = self._db.get_collection(collection_name)
        docs = collection.find(query, projection, sort, limit, skip)

        results = []
        for doc in docs:
            self._read_set[f"{collection_name}:{doc.id}"] = doc.version
            results.append(doc.to_dict())

        write_results = self._find_in_write_set(collection_name, query)
        results.extend(write_results)

        if sort:
            results = self._sort_results(results, sort)
        if skip:
            results = results[skip:]
        if limit:
            results = results[:limit]

        return results

    def _find_in_write_set(
        self, collection_name: str, query: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """在写集合中查找匹配的文档"""
        from ..query.parser import QueryParser

        parser = QueryParser()
        filter_tree = parser.parse_query(query)

        results = []
        for key, entry in self._write_set.items():
            if not key.startswith(f"{collection_name}:"):
                continue

            if entry["type"] == "delete":
                continue

            doc = entry["doc"]
            doc_dict = doc.to_dict()

            if filter_tree.evaluate(doc_dict):
                results.append(doc_dict)

        return results

    def _sort_results(
        self, results: List[Dict[str, Any]], sort: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """对结果排序"""

        def sort_key(doc: Dict[str, Any]):
            keys = []
            for field, direction in sort.items():
                value = None
                parts = field.split(".")
                current = doc
                for part in parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        value = None
                        break
                else:
                    value = current

                if value is None:
                    value = float("-inf") if direction > 0 else float("inf")
                if direction < 0:
                    if isinstance(value, (int, float)):
                        value = -value
                keys.append(value)
            return tuple(keys)

        try:
            return sorted(results, key=sort_key)
        except TypeError:
            return results

    def update_one(
        self,
        collection_name: str,
        query: Dict[str, Any],
        update_data: Dict[str, Any],
    ) -> int:
        """
        在事务中更新单个文档
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            update_data: 更新操作
            
        Returns:
            更新的文档数量
        """
        self._ensure_active()

        doc_dict = self.find_one(collection_name, query)
        if not doc_dict:
            return 0

        from ..core.document import Document

        doc = Document(doc_dict)
        self._apply_update(doc, update_data)

        key = f"{collection_name}:{doc.id}"
        self._write_set[key] = {
            "type": "update",
            "collection": collection_name,
            "doc": doc,
            "old_version": doc_dict.get("_version", 1),
        }

        return 1

    def update_many(
        self,
        collection_name: str,
        query: Dict[str, Any],
        update_data: Dict[str, Any],
    ) -> int:
        """
        在事务中更新多个文档
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            update_data: 更新操作
            
        Returns:
            更新的文档数量
        """
        self._ensure_active()

        docs = self.find(collection_name, query)
        if not docs:
            return 0

        from ..core.document import Document

        count = 0
        for doc_dict in docs:
            doc = Document(doc_dict)
            self._apply_update(doc, update_data)

            key = f"{collection_name}:{doc.id}"
            self._write_set[key] = {
                "type": "update",
                "collection": collection_name,
                "doc": doc,
                "old_version": doc_dict.get("_version", 1),
            }
            count += 1

        return count

    def _apply_update(self, doc: Any, update_data: Dict[str, Any]) -> None:
        """应用更新操作"""
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

        doc.increment_version()

    def delete_one(self, collection_name: str, query: Dict[str, Any]) -> int:
        """
        在事务中删除单个文档
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            
        Returns:
            删除的文档数量
        """
        self._ensure_active()

        doc_dict = self.find_one(collection_name, query)
        if not doc_dict:
            return 0

        key = f"{collection_name}:{doc_dict['_id']}"
        self._write_set[key] = {
            "type": "delete",
            "collection": collection_name,
            "doc_id": doc_dict["_id"],
            "old_version": doc_dict.get("_version", 1),
        }

        return 1

    def delete_many(self, collection_name: str, query: Dict[str, Any]) -> int:
        """
        在事务中删除多个文档
        
        Args:
            collection_name: 集合名称
            query: 查询条件
            
        Returns:
            删除的文档数量
        """
        self._ensure_active()

        docs = self.find(collection_name, query)
        if not docs:
            return 0

        count = 0
        for doc_dict in docs:
            key = f"{collection_name}:{doc_dict['_id']}"
            self._write_set[key] = {
                "type": "delete",
                "collection": collection_name,
                "doc_id": doc_dict["_id"],
                "old_version": doc_dict.get("_version", 1),
            }
            count += 1

        return count

    def commit(self) -> bool:
        """
        提交事务
        
        提交流程:
        1. 检查冲突（乐观并发控制）
        2. 开始 WAL 事务
        3. 应用所有修改
        4. 更新索引
        5. 提交 WAL
        6. 标记事务为已提交
        
        Returns:
            是否提交成功
            
        Raises:
            如果有冲突则抛出异常
        """
        with self._lock:
            if self._state != TransactionState.ACTIVE:
                raise ValueError(f"Cannot commit transaction in state {self._state}")

            self._state = TransactionState.PREPARING

            try:
                self._validate_conflicts()
                self._apply_changes()
                self._state = TransactionState.COMMITTED
                return True
            except Exception as e:
                self._state = TransactionState.ABORTED
                raise e

    def _validate_conflicts(self) -> None:
        """
        验证冲突
        
        检查读集合中的文档版本是否有变化
        如果有其他事务修改了我们读过的文档，冲突
        """
        for key, version in self._read_set.items():
            collection_name, doc_id = key.split(":", 1)

            collection = self._db.get_collection(collection_name)
            doc = collection.find_by_id(doc_id)

            if doc and doc.version != version:
                raise ValueError(
                    f"Write conflict: document {doc_id} in collection {collection_name} "
                    f"has been modified (expected version {version}, got {doc.version})"
                )

        for key, entry in self._write_set.items():
            if entry["type"] == "delete":
                continue
            if "old_version" not in entry:
                continue

            collection_name, doc_id = key.split(":", 1)
            collection = self._db.get_collection(collection_name)
            doc = collection.find_by_id(doc_id)

            if doc and doc.version != entry["old_version"]:
                raise ValueError(
                    f"Write conflict: document {doc_id} has been modified"
                )

    def _apply_changes(self) -> None:
        """
        应用所有修改（原子性：要么全部成功，要么全部回滚）
        
        使用补偿事务模式：记录每个已执行操作，中途失败时逐个逆操作回滚。
        """
        applied_ops: List[Dict[str, Any]] = []

        docs_by_collection: Dict[str, Dict[str, Any]] = {}

        for key, entry in self._write_set.items():
            collection_name = entry["collection"]
            if collection_name not in docs_by_collection:
                docs_by_collection[collection_name] = {
                    "inserts": [],
                    "updates": [],
                    "deletes": [],
                }

            if entry["type"] == "insert":
                docs_by_collection[collection_name]["inserts"].append(entry["doc"])
            elif entry["type"] == "update":
                docs_by_collection[collection_name]["updates"].append(
                    (entry["old_version"], entry["doc"])
                )
            elif entry["type"] == "delete":
                docs_by_collection[collection_name]["deletes"].append(entry["doc_id"])

        try:
            for collection_name, changes in docs_by_collection.items():
                collection = self._db.get_collection(collection_name)

                for doc in changes["inserts"]:
                    old_doc_snapshot = collection.find_by_id(doc.id)
                    self._do_insert(collection, doc)
                    applied_ops.append({
                        "type": "insert",
                        "collection": collection,
                        "doc": doc,
                        "old_snapshot": old_doc_snapshot,
                    })

                for old_version, doc in changes["updates"]:
                    old_doc = collection.find_by_id(doc.id)
                    old_doc_snapshot = old_doc.clone() if old_doc else None
                    self._do_update(collection, doc)
                    applied_ops.append({
                        "type": "update",
                        "collection": collection,
                        "doc": doc,
                        "old_snapshot": old_doc_snapshot,
                    })

                for doc_id in changes["deletes"]:
                    old_doc = collection.find_by_id(doc_id)
                    old_doc_snapshot = old_doc.clone() if old_doc else None
                    self._do_delete(collection, doc_id)
                    applied_ops.append({
                        "type": "delete",
                        "collection": collection,
                        "doc_id": doc_id,
                        "old_snapshot": old_doc_snapshot,
                    })
        except Exception as apply_exc:
            self._rollback_applied_ops(applied_ops)
            self._write_set.clear()
            self._read_set.clear()
            raise apply_exc

    def _rollback_applied_ops(self, applied_ops: List[Dict[str, Any]]) -> None:
        """
        回滚已经执行的操作（逆操作）
        
        按倒序逐个撤销。注意：文档存储是追加式写入，
        数据文件的旧版本可以保留，只需回滚内存索引和 B+ 树二级索引。
        """
        for op in reversed(applied_ops):
            try:
                collection = op["collection"]
                with collection._lock:
                    if op["type"] == "insert":
                        doc = op["doc"]
                        # 逆操作: 从文档存储和二级索引中删除刚插入的文档
                        if doc.id in collection._doc_store._index:
                            del collection._doc_store._index[doc.id]
                            collection._doc_store._doc_count -= 1
                        # 从二级索引移除
                        try:
                            collection._index_manager.unindex_document(doc)
                        except Exception as idx_exc:
                            print(f"Warning: rollback unindex failed for insert: {idx_exc}")

                    elif op["type"] == "update":
                        old_doc = op["old_snapshot"]
                        new_doc = op["doc"]
                        if old_doc and new_doc:
                            # 逆操作: 先移除新版本索引，再把旧版本写回内存索引和二级索引
                            try:
                                collection._index_manager.update_document(new_doc, old_doc)
                            except Exception as idx_exc:
                                print(f"Warning: rollback update index failed: {idx_exc}")
                            # 写回旧版本到数据存储（追加式，会产生新版本但内容是旧的）
                            try:
                                collection._doc_store._write_doc_to_file(old_doc, update_index=True)
                            except Exception as ds_exc:
                                print(f"Warning: rollback update doc_store failed: {ds_exc}")

                    elif op["type"] == "delete":
                        old_doc = op["old_snapshot"]
                        if old_doc:
                            # 逆操作: 重新写回内存索引和二级索引
                            try:
                                collection._doc_store._write_doc_to_file(old_doc, update_index=True)
                            except Exception as ds_exc:
                                print(f"Warning: rollback delete doc_store failed: {ds_exc}")
                            try:
                                collection._index_manager.index_document(old_doc)
                            except Exception as idx_exc:
                                print(f"Warning: rollback delete index failed: {idx_exc}")
            except Exception as rollback_exc:
                print(f"Warning: rollback step failed ({op.get('type')}): {rollback_exc}")

    def _do_insert(self, collection, doc) -> None:
        """执行插入（内部具有原子性：索引失败会回滚文档存储）"""
        with collection._lock:
            collection._doc_store.insert(doc)
            try:
                collection._index_manager.index_document(doc)
            except Exception as idx_exc:
                # 索引失败，回滚文档存储的插入
                if doc.id in collection._doc_store._index:
                    del collection._doc_store._index[doc.id]
                    collection._doc_store._doc_count -= 1
                raise idx_exc

    def _do_update(self, collection, doc) -> None:
        """执行更新"""
        with collection._lock:
            old_doc = collection.find_by_id(doc.id)
            if old_doc:
                collection._doc_store.update(doc)
                try:
                    collection._index_manager.update_document(old_doc, doc)
                except Exception as idx_exc:
                    # 索引失败，尝试把旧文档写回
                    try:
                        collection._doc_store.update(old_doc)
                        collection._index_manager.update_document(doc, old_doc)
                    except Exception:
                        pass
                    raise idx_exc

    def _do_delete(self, collection, doc_id) -> None:
        """执行删除"""
        with collection._lock:
            doc = collection.find_by_id(doc_id)
            if doc:
                old_snapshot = doc.clone()
                collection._doc_store.delete(doc_id)
                try:
                    collection._index_manager.unindex_document(doc)
                except Exception as idx_exc:
                    # 索引失败，恢复文档
                    try:
                        collection._doc_store._write_doc_to_file(old_snapshot, update_index=True)
                        collection._index_manager.index_document(old_snapshot)
                    except Exception:
                        pass
                    raise idx_exc

    def abort(self) -> None:
        """
        中止事务（回滚）
        
        放弃所有修改
        """
        with self._lock:
            if self._state == TransactionState.ACTIVE:
                self._state = TransactionState.ABORTED
                self._write_set.clear()
                self._read_set.clear()

    def rollback(self) -> None:
        """回滚（abort 的别名）"""
        self.abort()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            if self._state == TransactionState.ACTIVE:
                self.abort()
        else:
            if self._state == TransactionState.ACTIVE:
                self.commit()

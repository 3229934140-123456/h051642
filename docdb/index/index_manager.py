"""
IndexManager - 索引管理器

索引管理器负责:
- 创建、删除二级索引
- 在文档增删改时维护所有索引
- 索引与数据的一致性保证
- 为查询优化器提供索引信息

索引一致性保证:
- 文档更新时，先删除旧索引条目，再插入新索引条目
- 使用 WAL 保证索引操作的原子性
- 删除文档时，从所有索引中移除该文档的条目
- 定期校验索引与数据的一致性

支持的索引类型:
- B+ 树索引: 支持范围查询和排序
- 哈希索引: 仅支持精确匹配（暂用 B+ 树模拟）
- 数组索引: 对数组字段，每个数组元素建一条索引
"""

import os
import json
import threading
from typing import Dict, List, Optional, Any, Set, Tuple

from ..core.document import Document
from ..storage.document_store import DocumentStore
from .btree import BPlusTree


class IndexType:
    BTREE = "btree"
    HASH = "hash"


class IndexInfo:
    """索引信息"""

    def __init__(
        self,
        name: str,
        field: str,
        index_type: str = IndexType.BTREE,
        unique: bool = False,
        multi_key: bool = False,
    ):
        """
        初始化索引信息
        
        Args:
            name: 索引名称
            field: 索引字段路径
            index_type: 索引类型
            unique: 是否唯一索引
            multi_key: 是否为多键索引（数组字段）
        """
        self.name = name
        self.field = field
        self.index_type = index_type
        self.unique = unique
        self.multi_key = multi_key
        self.tree: Optional[BPlusTree] = None
        self._dirty = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "field": self.field,
            "index_type": self.index_type,
            "unique": self.unique,
            "multi_key": self.multi_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IndexInfo":
        return cls(
            name=data["name"],
            field=data["field"],
            index_type=data.get("index_type", IndexType.BTREE),
            unique=data.get("unique", False),
            multi_key=data.get("multi_key", False),
        )


class IndexManager:
    """
    索引管理器
    
    负责维护集合上的所有二级索引
    """

    def __init__(self, data_dir: str, doc_store: DocumentStore):
        """
        初始化索引管理器
        
        Args:
            data_dir: 数据目录
            doc_store: 文档存储引用
        """
        self.data_dir = data_dir
        self._index_dir = os.path.join(data_dir, "_indexes")
        os.makedirs(self._index_dir, exist_ok=True)

        self._doc_store = doc_store
        self._indexes: Dict[str, IndexInfo] = {}
        self._lock = threading.RLock()

        self._meta_file = os.path.join(self._index_dir, "_index_meta.json")
        self._load_meta()
        self._load_indexes()

    def _load_meta(self) -> None:
        """加载索引元数据"""
        if os.path.exists(self._meta_file):
            with open(self._meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                for idx_data in meta.get("indexes", []):
                    idx_info = IndexInfo.from_dict(idx_data)
                    self._indexes[idx_info.name] = idx_info
        else:
            self._save_meta()

    def _save_meta(self) -> None:
        """保存索引元数据"""
        meta = {
            "indexes": [idx.to_dict() for idx in self._indexes.values()],
            "version": "0.1.0",
        }
        with open(self._meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def _load_indexes(self) -> None:
        """加载所有索引数据"""
        for idx_name, idx_info in self._indexes.items():
            idx_file = os.path.join(self._index_dir, f"{idx_name}.json")
            if os.path.exists(idx_file):
                self._load_index_from_file(idx_info, idx_file)
            else:
                idx_info.tree = BPlusTree(order=32)

    def _load_index_from_file(self, idx_info: IndexInfo, file_path: str) -> None:
        """从文件加载索引"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            tree = BPlusTree(order=data.get("order", 32))
            for entry in data.get("entries", []):
                key = self._deserialize_key(entry["key"])
                values = entry["values"]
                for v in values:
                    tree.insert(key, v)

            idx_info.tree = tree
        except Exception as e:
            print(f"Warning: failed to load index {idx_info.name}: {e}")
            idx_info.tree = BPlusTree()
            if self._doc_store:
                self._build_index(idx_info)

    def _serialize_key(self, key: Any) -> Any:
        """序列化键（处理类型信息）"""
        if key is None:
            return {"_type": "null", "value": None}
        if isinstance(key, bool):
            return {"_type": "bool", "value": key}
        if isinstance(key, int):
            return {"_type": "int", "value": key}
        if isinstance(key, float):
            return {"_type": "float", "value": key}
        if isinstance(key, str):
            return {"_type": "str", "value": key}
        return {"_type": "str", "value": str(key)}

    def _deserialize_key(self, data: Any) -> Any:
        """反序列化键"""
        if isinstance(data, dict) and "_type" in data:
            t = data["_type"]
            v = data["value"]
            if t == "null":
                return None
            if t == "bool":
                return v
            if t == "int":
                return int(v)
            if t == "float":
                return float(v)
            if t == "str":
                return v
        return data

    def _save_index(self, idx_info: IndexInfo) -> None:
        """保存索引到文件"""
        if idx_info.tree is None:
            return

        entries = []
        for key, values in idx_info.tree.iterate():
            entries.append(
                {"key": self._serialize_key(key), "values": values}
            )

        data = {
            "name": idx_info.name,
            "order": idx_info.tree.order,
            "entries": entries,
            "size": idx_info.tree.size,
            "value_count": idx_info.tree.value_count,
        }

        idx_file = os.path.join(self._index_dir, f"{idx_info.name}.json")
        tmp_file = idx_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_file, idx_file)

        idx_info._dirty = False

    def _build_index(self, idx_info: IndexInfo) -> None:
        """
        构建索引（扫描所有文档）
        
        索引构建原理:
        - 遍历所有文档
        - 提取索引字段的值
        - 对数组字段（multi_key），每个元素建一条索引
        - 对唯一索引，检查冲突
        """
        if idx_info.tree is None:
            idx_info.tree = BPlusTree(order=32)
        else:
            idx_info.tree = BPlusTree(order=32)

        for doc in self._doc_store.iterate():
            self._index_document_single(doc, idx_info)

    def create_index(
        self,
        name: str,
        field: str,
        index_type: str = IndexType.BTREE,
        unique: bool = False,
    ) -> str:
        """
        创建二级索引
        
        Args:
            name: 索引名称
            field: 索引字段路径
            index_type: 索引类型
            unique: 是否唯一索引
            
        Returns:
            索引名称
        """
        with self._lock:
            if name in self._indexes:
                raise ValueError(f"Index '{name}' already exists")

            idx_info = IndexInfo(
                name=name,
                field=field,
                index_type=index_type,
                unique=unique,
            )
            idx_info.tree = BPlusTree(order=32)

            if self._doc_store and self._doc_store.count() > 0:
                self._build_index(idx_info)

            self._indexes[name] = idx_info
            self._save_meta()
            self._save_index(idx_info)

            return name

    def drop_index(self, name: str) -> bool:
        """删除索引"""
        with self._lock:
            if name not in self._indexes:
                return False

            idx_file = os.path.join(self._index_dir, f"{name}.json")
            if os.path.exists(idx_file):
                os.remove(idx_file)

            del self._indexes[name]
            self._save_meta()
            return True

    def drop_all_indexes(self) -> None:
        """删除所有索引"""
        with self._lock:
            for name in list(self._indexes.keys()):
                self.drop_index(name)

    def list_indexes(self) -> List[Dict[str, Any]]:
        """列出所有索引"""
        with self._lock:
            return [idx.to_dict() for idx in self._indexes.values()]

    def get_index(self, name: str) -> Optional[BPlusTree]:
        """获取索引的 B+ 树"""
        with self._lock:
            if name not in self._indexes:
                return None
            return self._indexes[name].tree

    def get_index_info(self, name: str) -> Optional[IndexInfo]:
        """获取索引信息"""
        with self._lock:
            return self._indexes.get(name)

    def get_indexes_for_field(self, field: str) -> List[str]:
        """获取指定字段上的所有索引名称"""
        with self._lock:
            return [
                name
                for name, idx in self._indexes.items()
                if idx.field == field
            ]

    def _extract_index_values(self, doc: Document, field: str) -> List[Any]:
        """
        从文档中提取索引字段的值
        
        支持:
        - 普通字段: 直接返回值
        - 嵌套字段: 按路径查找
        - 数组字段: 返回数组所有元素（多键索引）
        - 数组中嵌套对象: 提取数组中每个对象的指定字段
        
        Args:
            doc: 文档
            field: 字段路径
            
        Returns:
            值列表（可能有多个值，对于数组字段）
        """
        parts = field.split(".")
        values = [doc._data]

        for i, part in enumerate(parts):
            next_values = []
            for val in values:
                if isinstance(val, dict):
                    if part in val:
                        next_values.append(val[part])
                elif isinstance(val, list):
                    if part.isdigit():
                        idx = int(part)
                        if 0 <= idx < len(val):
                            next_values.append(val[idx])
                    else:
                        for item in val:
                            if isinstance(item, dict) and part in item:
                                next_values.append(item[part])

            values = next_values
            if not values:
                break

        result = []
        for val in values:
            if isinstance(val, list):
                result.extend(val)
            else:
                result.append(val)

        return result if result else [None]

    def _index_document_single(self, doc: Document, idx_info: IndexInfo) -> None:
        """
        将单个文档加入指定索引
        
        索引条目格式: key -> [doc_id1, doc_id2, ...]
        唯一索引只允许一个 doc_id
        """
        if idx_info.tree is None:
            return

        values = self._extract_index_values(doc, idx_info.field)

        for value in values:
            if idx_info.unique:
                existing = idx_info.tree.get(value)
                if existing and doc.id not in existing:
                    raise ValueError(
                        f"Duplicate key '{value}' for unique index '{idx_info.name}'"
                    )

            idx_info.tree.insert(value, doc.id)

    def index_document(self, doc: Document) -> None:
        """
        文档插入时，将文档加入所有索引

        原子性: 若中途某个索引写入失败（如唯一冲突），已写入的索引会被回滚。
        """
        with self._lock:
            applied_indexes: List[IndexInfo] = []
            try:
                for idx_info in self._indexes.values():
                    self._index_document_single(doc, idx_info)
                    applied_indexes.append(idx_info)
                    idx_info._dirty = True
            except Exception:
                for idx_info in reversed(applied_indexes):
                    try:
                        self._unindex_document_single(doc, idx_info)
                        idx_info._dirty = True
                    except Exception:
                        pass
                raise

    def _unindex_document_single(self, doc: Document, idx_info: IndexInfo) -> None:
        """将文档从指定索引中移除"""
        if idx_info.tree is None:
            return

        values = self._extract_index_values(doc, idx_info.field)
        for value in values:
            idx_info.tree.delete(value, doc.id)

    def unindex_document(self, doc: Document) -> None:
        """
        文档删除时，从所有索引中移除
        
        索引一致性保证:
        - 删除文档后，从所有索引中移除该文档的条目
        - 避免出现索引指向已删除文档的情况（悬空索引）
        """
        with self._lock:
            for idx_info in self._indexes.values():
                self._unindex_document_single(doc, idx_info)
                idx_info._dirty = True

    def update_document(self, old_doc: Document, new_doc: Document) -> None:
        """
        文档更新时，维护所有索引

        原子性: 若中途某个索引更新失败，已更新的索引会被回滚。

        索引维护策略:
        1. 先从所有索引中删除旧版本文档的条目
        2. 再将新版本文档加入所有索引
        """
        with self._lock:
            applied: List[Tuple[IndexInfo, set, set]] = []
            try:
                for idx_info in self._indexes.values():
                    old_values = set(
                        self._extract_index_values(old_doc, idx_info.field)
                    )
                    new_values = set(
                        self._extract_index_values(new_doc, idx_info.field)
                    )

                    removed_values = old_values - new_values
                    added_values = new_values - old_values

                    if idx_info.tree:
                        for val in removed_values:
                            idx_info.tree.delete(val, new_doc.id)
                        for val in added_values:
                            if idx_info.unique:
                                existing = idx_info.tree.get(val)
                                if existing and new_doc.id not in existing:
                                    raise ValueError(
                                        f"Duplicate key '{val}' for unique index '{idx_info.name}'"
                                    )
                            idx_info.tree.insert(val, new_doc.id)

                    applied.append((idx_info, removed_values, added_values))
                    idx_info._dirty = True
            except Exception:
                for idx_info, removed_values, added_values in reversed(applied):
                    if idx_info.tree:
                        try:
                            for val in added_values:
                                idx_info.tree.delete(val, new_doc.id)
                            for val in removed_values:
                                idx_info.tree.insert(val, new_doc.id)
                            idx_info._dirty = True
                        except Exception:
                            pass
                raise

    def find_by_index(
        self, index_name: str, value: Any
    ) -> List[str]:
        """
        通过索引查找文档 ID 列表
        
        Args:
            index_name: 索引名称
            value: 查找值
            
        Returns:
            文档 ID 列表
        """
        with self._lock:
            idx_info = self._indexes.get(index_name)
            if not idx_info or idx_info.tree is None:
                return []
            return idx_info.tree.get(value)

    def range_by_index(
        self,
        index_name: str,
        start_value: Optional[Any] = None,
        end_value: Optional[Any] = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> List[Tuple[Any, List[str]]]:
        """
        通过索引范围查找
        
        Args:
            index_name: 索引名称
            start_value: 起始值
            end_value: 结束值
            include_start: 是否包含起始值
            include_end: 是否包含结束值
            
        Returns:
            [(key, [doc_ids]), ...]
        """
        with self._lock:
            idx_info = self._indexes.get(index_name)
            if not idx_info or idx_info.tree is None:
                return []

            return idx_info.tree.range_query(
                start_value, end_value, include_start, include_end
            )

    def validate_index_consistency(self) -> Dict[str, Any]:
        """
        校验索引与数据的一致性
        
        检查项:
        1. 悬挂引用: 索引中的文档是否已被删除
        2. 值不匹配: 索引中存的值和文档实际值是否一致
        3. 遗漏索引: 存在文档应该在索引中但实际没建索引
        4. 索引重复/多余: 同一文档同值是否出现多次
        
        Returns:
            校验结果字典
            {
                index_name: {
                    "indexed_documents": int,     # 索引覆盖的文档数
                    "total_documents": int,       # 数据存储中的文档总数
                    "expected_documents": int,    # 应该被索引的文档数（有值的）
                    "dangling_references": int,   # 悬挂引用数
                    "value_mismatches": int,      # 值不匹配数
                    "missing_index_entries": int, # 遗漏索引数
                    "missing_docs": [             # 具体遗漏的文档
                        {"doc_id": ..., "field_value": ...}, ...
                    ],
                    "issues": [str, ...],         # 可读的问题描述
                    "consistent": bool,           # 是否一致
                }
            }
        """
        with self._lock:
            results = {}

            total_docs = self._doc_store.count()

            for idx_name, idx_info in self._indexes.items():
                if idx_info.tree is None:
                    continue

                issues: List[str] = []
                indexed_docs: Set[str] = set()
                indexed_entries: Set[Tuple[str, Any]] = set()
                dangling_count = 0
                mismatch_count = 0
                missing_count = 0
                missing_docs: List[Dict[str, Any]] = []

                # 检查 1 & 2: 遍历索引，找悬挂引用和值不匹配
                for key, doc_ids in idx_info.tree.iterate():
                    for doc_id in doc_ids:
                        indexed_docs.add(doc_id)
                        entry_key = (doc_id, key)
                        if entry_key in indexed_entries:
                            issues.append(
                                f"Duplicate index entry: doc '{doc_id}' with value '{key}' "
                                f"appears multiple times in index '{idx_name}'"
                            )
                        indexed_entries.add(entry_key)

                        doc = self._doc_store.get(doc_id)

                        if doc is None:
                            dangling_count += 1
                            issues.append(
                                f"Dangling reference: doc '{doc_id}' is in index "
                                f"with key '{key}' but document does not exist in store"
                            )
                            continue

                        actual_values = self._extract_index_values(
                            doc, idx_info.field
                        )
                        if key not in actual_values:
                            mismatch_count += 1
                            issues.append(
                                f"Value mismatch: doc '{doc_id}' field '{idx_info.field}' "
                                f"has actual values {actual_values}, but index has stale key '{key}'"
                            )

                # 检查 3: 遍历所有文档，找遗漏的索引
                expected_count = 0
                for doc in self._doc_store.iterate():
                    if doc.is_deleted:
                        continue
                    values = self._extract_index_values(doc, idx_info.field)
                    non_null_values = [v for v in values if v is not None]
                    if non_null_values:
                        expected_count += len(non_null_values)
                        for val in non_null_values:
                            if (doc.id, val) not in indexed_entries:
                                missing_count += 1
                                missing_docs.append({
                                    "doc_id": doc.id,
                                    "field": idx_info.field,
                                    "field_value": val,
                                })
                                if missing_count <= 10:
                                    issues.append(
                                        f"Missing index entry: doc '{doc.id}' field "
                                        f"'{idx_info.field}' = '{val}' is not in index '{idx_name}'"
                                    )

                if missing_count > 10:
                    issues.append(
                        f"... and {missing_count - 10} more missing index entries (showing first 10)"
                    )

                results[idx_name] = {
                    "indexed_documents": len(indexed_docs),
                    "indexed_entries": len(indexed_entries),
                    "total_documents": total_docs,
                    "expected_entries": expected_count,
                    "dangling_references": dangling_count,
                    "value_mismatches": mismatch_count,
                    "missing_index_entries": missing_count,
                    "missing_docs": missing_docs,
                    "issues": issues,
                    "consistent": (
                        dangling_count == 0
                        and mismatch_count == 0
                        and missing_count == 0
                    ),
                }

            return results

    def rebuild_all(self) -> int:
        """
        重建所有索引（WAL 恢复后调用，确保索引和数据一致）
        
        Returns:
            重建的索引数量
        """
        with self._lock:
            count = 0
            for idx_info in self._indexes.values():
                self._build_index(idx_info)
                idx_info._dirty = True
                count += 1
            self.flush()
            return count

    def flush(self) -> None:
        """将所有索引持久化到磁盘"""
        with self._lock:
            for idx_info in self._indexes.values():
                if idx_info._dirty:
                    self._save_index(idx_info)

    def close(self) -> None:
        """关闭索引管理器"""
        self.flush()

"""
DocumentStore - 文档存储引擎

文档存储原理:
- 内存中维护 doc_id -> 文档位置 的索引（哈希索引）
- 磁盘上使用追加式写的方式存储文档数据（类似 LSM Tree 的 SSTable 简化版）
- 每个文档有唯一的 _id 作为主键
- 支持按 ID 快速查找（O(1)）
- 删除操作使用墓碑标记（tombstone），由合并清理
- 定期进行 compaction 合并数据文件，清理已删除和旧版本数据

存储格式:
- data_0001.log: 数据文件，追加写入
- index.json: 内存索引的持久化快照
- 每个文档在数据文件中的偏移量存储在索引中
"""

import os
import json
import struct
import threading
import time
from typing import Dict, Optional, List, Iterator, Tuple
from bisect import bisect_left

from ..core.document import Document
from .wal import WAL


class DocumentStore:
    """
    文档存储引擎
    
    负责:
    - 文档的持久化存储
    - 按 ID 快速查找
    - CRUD 操作
    - WAL 保证数据一致性
    - Compaction 压缩
    """

    def __init__(self, data_dir: str):
        """
        初始化文档存储
        
        Args:
            data_dir: 数据目录
        """
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

        self._index: Dict[str, Tuple[int, int]] = {}  # doc_id -> (file_num, offset)
        self._current_file_num = 0
        self._current_file = None
        self._current_offset = 0
        self._wal = WAL(data_dir)

        self._lock = threading.RLock()
        self._doc_count = 0
        self.recovered_ops = 0

        self._load_index()
        self._open_current_file()
        self.recovered_ops = self._recover_if_needed()

    def _index_file_path(self) -> str:
        return os.path.join(self.data_dir, "_doc_index.json")

    def _data_file_path(self, file_num: int) -> str:
        return os.path.join(self.data_dir, f"data_{file_num:04d}.log")

    def _load_index(self) -> None:
        """加载索引文件"""
        index_path = self._index_file_path()
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    idx_data = json.load(f)
                    self._index = {
                        k: (v[0], v[1]) for k, v in idx_data.get("index", {}).items()
                    }
                    self._current_file_num = idx_data.get("current_file", 0)
                    self._doc_count = idx_data.get("doc_count", 0)
            except Exception as e:
                print(f"Warning: failed to load index: {e}")
                self._index = {}
                self._current_file_num = 0
                self._doc_count = 0
        else:
            self._index = {}
            self._current_file_num = 0
            self._doc_count = 0

    def _save_index(self) -> None:
        """保存索引到磁盘"""
        index_path = self._index_file_path()
        idx_data = {
            "index": {k: list(v) for k, v in self._index.items()},
            "current_file": self._current_file_num,
            "doc_count": self._doc_count,
            "saved_at": time.time(),
        }
        tmp_path = index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(idx_data, f, ensure_ascii=False)
        os.replace(tmp_path, index_path)

    def _open_current_file(self) -> None:
        """打开当前数据文件用于追加写入"""
        if self._current_file and not self._current_file.closed:
            self._current_file.close()

        file_path = self._data_file_path(self._current_file_num)
        self._current_file = open(file_path, "ab")
        self._current_offset = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    def _recover_if_needed(self) -> int:
        """从 WAL 恢复数据（幂等，可多次调用）
        
        Returns:
            恢复的操作数量
        """
        recover_ops = self._wal.recover()
        if not recover_ops:
            return 0

        print(f"Recovering {len(recover_ops)} operations from WAL...")

        count = 0
        for op_type, data in recover_ops:
            doc_id = data.get("doc_id")
            if not doc_id:
                continue

            if op_type == "INSERT" or op_type == "UPDATE":
                doc_data = data.get("doc_data") or data.get("new_data")
                if doc_data:
                    doc = Document(doc_data)
                    doc._id = doc_id
                    self._write_doc_to_file(doc, update_index=True)
                    count += 1
            elif op_type == "DELETE":
                old_data = data.get("old_data", {})
                tombstone = Document(old_data)
                tombstone._id = doc_id
                tombstone.mark_deleted()
                self._write_doc_to_file(tombstone, update_index=False)
                if doc_id in self._index:
                    del self._index[doc_id]
                    self._doc_count -= 1
                count += 1

        self._save_index()
        return count

    def _write_doc_to_file(self, doc: Document, update_index: bool = True) -> int:
        """
        将文档追加写入数据文件
        
        Args:
            doc: 文档对象
            update_index: 是否更新内存索引
            
        Returns:
            写入偏移量
        """
        doc_bytes = doc.to_bytes()
        offset = self._current_offset

        self._current_file.write(doc_bytes)
        self._current_file.flush()
        self._current_offset += len(doc_bytes)

        if update_index:
            is_new = doc.id not in self._index
            self._index[doc.id] = (self._current_file_num, offset)
            if is_new and not doc.is_deleted:
                self._doc_count += 1

        return offset

    def count(self) -> int:
        """获取文档总数"""
        return self._doc_count

    def insert(self, doc: Document) -> str:
        """
        插入文档
        
        Args:
            doc: 文档对象
            
        Returns:
            文档ID
        """
        with self._lock:
            if doc.id in self._index:
                raise ValueError(f"Document with id '{doc.id}' already exists")

            txn_id = self._wal.begin_transaction()
            try:
                self._wal.log_insert(txn_id, doc.id, doc.to_dict())

                self._write_doc_to_file(doc)
                self._save_index()

                self._wal.commit_transaction(txn_id)
                return doc.id
            except Exception as e:
                self._wal.abort_transaction(txn_id)
                raise e

    def get(self, doc_id: str) -> Optional[Document]:
        """
        根据 ID 获取文档
        
        Args:
            doc_id: 文档ID
            
        Returns:
            文档对象或 None
        """
        with self._lock:
            if doc_id not in self._index:
                return None

            file_num, offset = self._index[doc_id]
            file_path = self._data_file_path(file_num)

            try:
                with open(file_path, "rb") as f:
                    f.seek(offset)
                    version_bytes = f.read(4)
                    if len(version_bytes) < 4:
                        return None

                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        return None

                    json_length = struct.unpack(">I", length_bytes)[0]
                    total_size = 8 + json_length

                    f.seek(offset)
                    doc_bytes = f.read(total_size)

                    doc = Document.from_bytes(doc_bytes)
                    if doc.is_deleted:
                        return None
                    return doc
            except Exception as e:
                print(f"Error reading document: {e}")
                return None

    def update(self, doc: Document) -> bool:
        """
        更新文档（追加式写入，旧版本保留）
        
        Args:
            doc: 更新后的文档
            
        Returns:
            是否更新成功
        """
        with self._lock:
            if doc.id not in self._index:
                raise ValueError(f"Document with id '{doc.id}' does not exist")

            old_doc = self.get(doc.id)
            if not old_doc:
                return False

            txn_id = self._wal.begin_transaction()
            try:
                self._wal.log_update(
                    txn_id, doc.id, old_doc.to_dict(), doc.to_dict()
                )

                self._write_doc_to_file(doc)
                self._save_index()

                self._wal.commit_transaction(txn_id)
                return True
            except Exception as e:
                self._wal.abort_transaction(txn_id)
                raise e

    def delete(self, doc_id: str) -> bool:
        """
        删除文档（墓碑标记 + 索引移除）
        
        Args:
            doc_id: 文档ID
            
        Returns:
            是否删除成功
        """
        with self._lock:
            if doc_id not in self._index:
                return False

            old_doc = self.get(doc_id)
            if not old_doc:
                return False

            txn_id = self._wal.begin_transaction()
            try:
                self._wal.log_delete(txn_id, doc_id, old_doc.to_dict())

                tombstone = old_doc.clone()
                tombstone.mark_deleted()
                self._write_doc_to_file(tombstone, update_index=False)

                del self._index[doc_id]
                self._doc_count -= 1
                self._save_index()

                self._wal.commit_transaction(txn_id)
                return True
            except Exception as e:
                self._wal.abort_transaction(txn_id)
                raise e

    def iterate(self) -> Iterator[Document]:
        """
        迭代所有文档
        
        Yields:
            文档对象
        """
        with self._lock:
            doc_ids = list(self._index.keys())

        for doc_id in doc_ids:
            doc = self.get(doc_id)
            if doc and not doc.is_deleted:
                yield doc

    def get_all(self) -> List[Document]:
        """获取所有文档"""
        return list(self.iterate())

    def compact(self) -> None:
        """
        Compaction: 合并数据文件，清理旧版本和已删除文档
        
        原理:
        - 创建新的数据文件
        - 将所有活跃文档（最新版本）写入新文件
        - 替换旧的索引
        - 删除旧的数据文件
        
        注意: 这是一个简化版本的 compaction
        """
        with self._lock:
            new_file_num = self._current_file_num + 1
            new_file_path = self._data_file_path(new_file_num)
            new_index: Dict[str, Tuple[int, int]] = {}
            new_offset = 0
            new_count = 0

            all_docs = self.get_all()

            with open(new_file_path, "wb") as new_file:
                for doc in all_docs:
                    doc_bytes = doc.to_bytes()
                    new_file.write(doc_bytes)
                    new_index[doc.id] = (new_file_num, new_offset)
                    new_offset += len(doc_bytes)
                    new_count += 1

            old_file_nums = [fn for fn in range(self._current_file_num + 1)]

            self._current_file_num = new_file_num
            self._index = new_index
            self._doc_count = new_count

            self._open_current_file()
            self._save_index()

            for fn in old_file_nums:
                old_path = self._data_file_path(fn)
                try:
                    os.remove(old_path)
                except OSError:
                    pass

    def close(self) -> None:
        """关闭存储引擎"""
        with self._lock:
            self._save_index()
            if self._current_file and not self._current_file.closed:
                self._current_file.flush()
                self._current_file.close()
                self._current_file = None
            self._wal.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

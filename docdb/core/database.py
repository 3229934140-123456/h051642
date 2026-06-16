"""
Database 类 - 数据库实例

数据库是集合的容器，管理多个集合。
负责:
- 集合的创建、删除、获取
- 事务管理
- 数据库级别的配置
"""

import os
import json
import threading
from typing import Dict, Optional, List

from .collection import Collection
from ..transaction.transaction import Transaction


class Database:
    """
    数据库类，管理多个集合
    """

    def __init__(self, name: str, data_dir: str = "./data"):
        """
        初始化数据库
        
        Args:
            name: 数据库名称
            data_dir: 数据存储根目录
        """
        self.name = name
        self.data_dir = os.path.abspath(os.path.join(data_dir, name))
        os.makedirs(self.data_dir, exist_ok=True)

        self._collections: Dict[str, Collection] = {}
        self._lock = threading.RLock()

        self._metadata_file = os.path.join(self.data_dir, "_db_metadata.json")
        self._load_metadata()
        self._load_collections()

    def _load_metadata(self) -> None:
        """加载数据库元数据"""
        if os.path.exists(self._metadata_file):
            with open(self._metadata_file, "r", encoding="utf-8") as f:
                self._metadata = json.load(f)
        else:
            self._metadata = {
                "name": self.name,
                "collections": [],
                "version": "0.1.0",
            }
            self._save_metadata()

    def _save_metadata(self) -> None:
        """保存数据库元数据"""
        with open(self._metadata_file, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, indent=2, ensure_ascii=False)

    def _load_collections(self) -> None:
        """加载已存在的集合"""
        if not os.path.isdir(self.data_dir):
            return

        for entry in os.listdir(self.data_dir):
            entry_path = os.path.join(self.data_dir, entry)
            if os.path.isdir(entry_path) and not entry.startswith("_"):
                try:
                    self._collections[entry] = Collection(entry, self.data_dir)
                except Exception as e:
                    print(f"Warning: failed to load collection {entry}: {e}")

    def create_collection(self, name: str) -> Collection:
        """
        创建集合
        
        Args:
            name: 集合名称
            
        Returns:
            创建的集合对象
        """
        with self._lock:
            if name in self._collections:
                raise ValueError(f"Collection '{name}' already exists")

            collection = Collection(name, self.data_dir)
            self._collections[name] = collection

            if name not in self._metadata["collections"]:
                self._metadata["collections"].append(name)
                self._save_metadata()

            return collection

    def get_collection(self, name: str) -> Collection:
        """
        获取集合
        
        Args:
            name: 集合名称
            
        Returns:
            集合对象
        """
        with self._lock:
            if name not in self._collections:
                raise ValueError(f"Collection '{name}' does not exist")
            return self._collections[name]

    def collection(self, name: str) -> Collection:
        """
        获取或创建集合（便捷方法）
        
        Args:
            name: 集合名称
            
        Returns:
            集合对象
        """
        with self._lock:
            if name not in self._collections:
                return self.create_collection(name)
            return self._collections[name]

    def drop_collection(self, name: str) -> bool:
        """
        删除集合
        
        Args:
            name: 集合名称
            
        Returns:
            是否成功删除
        """
        with self._lock:
            if name not in self._collections:
                return False

            self._collections[name].drop()
            del self._collections[name]

            if name in self._metadata["collections"]:
                self._metadata["collections"].remove(name)
                self._save_metadata()

            return True

    def list_collections(self) -> List[str]:
        """列出所有集合名称"""
        with self._lock:
            return list(self._collections.keys())

    def start_transaction(self) -> Transaction:
        """
        开启事务
        
        Returns:
            事务对象
        """
        return Transaction(self)

    def __getitem__(self, name: str) -> Collection:
        """支持 db['collection_name'] 语法"""
        return self.collection(name)

    def __getattr__(self, name: str) -> Collection:
        """支持 db.collection_name 语法"""
        if name.startswith("_"):
            raise AttributeError(f"'Database' object has no attribute '{name}'")
        return self.collection(name)

    def close(self) -> None:
        """关闭数据库"""
        with self._lock:
            for collection in self._collections.values():
                collection.close()
            self._save_metadata()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

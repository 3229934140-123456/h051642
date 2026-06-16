"""
Document 类 - JSON 文档的数据模型

文档存储原理:
- 每个文档有一个唯一的 _id 字段作为主键
- 文档内容以 JSON 格式存储，支持嵌套结构和数组
- 内部维护版本号用于 MVCC 和事务
- 文档序列化为二进制格式存储在磁盘上

文档格式:
{
    "_id": "uuid-or-user-provided",
    "_version": 1,
    "_created_at": timestamp,
    "_updated_at": timestamp,
    "field1": "value1",
    "field2": { "nested": "value" },
    "field3": [1, 2, 3]
}
"""

import uuid
import time
import json
import copy
from typing import Any, Dict, Optional, List, Union


class Document:
    """
    文档类，封装 JSON 文档的所有操作
    
    属性说明:
    - _id: 文档唯一标识符，可由用户指定或自动生成
    - _version: 文档版本号，用于 MVCC 和乐观并发控制
    - _created_at: 创建时间戳
    - _updated_at: 更新时间戳
    - data: 用户数据部分（不含系统字段）
    """

    SYSTEM_FIELDS = {"_id", "_version", "_created_at", "_updated_at", "_deleted"}

    def __init__(
        self,
        data: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
        version: int = 1,
    ):
        """
        初始化文档
        
        Args:
            data: 文档数据字典
            doc_id: 文档ID，不提供则自动生成
            version: 初始版本号
        """
        self._id = doc_id or str(uuid.uuid4())
        self._version = version
        self._created_at = time.time()
        self._updated_at = self._created_at
        self._deleted = False
        self._data: Dict[str, Any] = {}

        if data:
            self._data = self._extract_user_data(data)
            if "_id" in data:
                self._id = str(data["_id"])
            if "_version" in data:
                self._version = data["_version"]
            if "_created_at" in data:
                self._created_at = data["_created_at"]
            if "_updated_at" in data:
                self._updated_at = data["_updated_at"]
            if "_deleted" in data:
                self._deleted = data["_deleted"]

    def _extract_user_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """从完整数据中提取用户数据（过滤系统字段）"""
        return {k: v for k, v in data.items() if k not in self.SYSTEM_FIELDS}

    @property
    def id(self) -> str:
        """获取文档ID"""
        return self._id

    @property
    def version(self) -> int:
        """获取文档版本号"""
        return self._version

    @property
    def created_at(self) -> float:
        """获取创建时间"""
        return self._created_at

    @property
    def updated_at(self) -> float:
        """获取更新时间"""
        return self._updated_at

    @property
    def is_deleted(self) -> bool:
        """文档是否已删除（用于MVCC墓碑标记）"""
        return self._deleted

    def get(self, path: str, default: Any = None) -> Any:
        """
        按路径获取字段值，支持嵌套字段和数组索引
        
        路径格式:
        - "field" - 顶层字段
        - "field.nested" - 嵌套字段
        - "field.array.0" - 数组元素
        - "field.array" - 整个数组
        
        Args:
            path: 字段路径，用点号分隔
            default: 默认值
            
        Returns:
            字段值
        """
        parts = path.split(".")
        current: Any = self._data

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return default
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    if 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return default
                except ValueError:
                    return default
            else:
                return default

        return current

    def set(self, path: str, value: Any) -> None:
        """
        按路径设置字段值，支持嵌套字段
        
        Args:
            path: 字段路径
            value: 要设置的值
        """
        parts = path.split(".")
        current = self._data

        for i, part in enumerate(parts[:-1]):
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value
        self._updated_at = time.time()

    def delete_field(self, path: str) -> bool:
        """
        删除指定路径的字段
        
        Args:
            path: 字段路径
            
        Returns:
            是否成功删除
        """
        parts = path.split(".")
        current = self._data

        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False

        if isinstance(current, dict) and parts[-1] in current:
            del current[parts[-1]]
            self._updated_at = time.time()
            return True

        return False

    def has_field(self, path: str) -> bool:
        """检查是否存在指定字段"""
        return self.get(path) is not None

    def to_dict(self, include_system_fields: bool = True) -> Dict[str, Any]:
        """
        将文档转换为字典
        
        Args:
            include_system_fields: 是否包含系统字段
            
        Returns:
            完整的文档字典
        """
        result = copy.deepcopy(self._data)
        if include_system_fields:
            result["_id"] = self._id
            result["_version"] = self._version
            result["_created_at"] = self._created_at
            result["_updated_at"] = self._updated_at
            if self._deleted:
                result["_deleted"] = True
        return result

    def to_json(self, include_system_fields: bool = True) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(include_system_fields), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "Document":
        """从 JSON 字符串反序列化为文档"""
        data = json.loads(json_str)
        return cls(data)

    def to_bytes(self) -> bytes:
        """
        序列化为二进制格式用于磁盘存储
        
        存储格式:
        - 4字节: 版本号
        - 4字节: JSON数据长度
        - N字节: JSON数据
        
        Returns:
            二进制数据
        """
        full_data = self.to_dict(include_system_fields=True)
        json_bytes = json.dumps(full_data, ensure_ascii=False).encode("utf-8")

        version_bytes = self._version.to_bytes(4, byteorder="big")
        length_bytes = len(json_bytes).to_bytes(4, byteorder="big")

        return version_bytes + length_bytes + json_bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "Document":
        """从二进制数据反序列化为文档"""
        if len(data) < 8:
            raise ValueError("Invalid document data: too short")

        version = int.from_bytes(data[:4], byteorder="big")
        length = int.from_bytes(data[4:8], byteorder="big")

        if len(data) < 8 + length:
            raise ValueError("Invalid document data: truncated")

        json_bytes = data[8 : 8 + length]
        json_str = json_bytes.decode("utf-8")
        doc_data = json.loads(json_str)

        return cls(doc_data)

    def increment_version(self) -> int:
        """递增版本号（更新时调用）"""
        self._version += 1
        self._updated_at = time.time()
        return self._version

    def mark_deleted(self) -> None:
        """标记为已删除（墓碑标记，用于MVCC）"""
        self._deleted = True
        self.increment_version()

    def clone(self) -> "Document":
        """创建文档的深拷贝"""
        new_doc = Document(copy.deepcopy(self._data), doc_id=self._id, version=self._version)
        new_doc._created_at = self._created_at
        new_doc._updated_at = self._updated_at
        new_doc._deleted = self._deleted
        return new_doc

    def __repr__(self) -> str:
        return f"Document(id={self._id}, version={self._version}, data={self._data})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Document):
            return False
        return self._id == other._id and self._version == other._version

    def __hash__(self) -> int:
        return hash((self._id, self._version))

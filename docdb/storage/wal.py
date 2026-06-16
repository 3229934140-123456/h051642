"""
WAL (Write-Ahead Log) - 预写日志

WAL 是数据库保证数据一致性的核心机制:
- 所有修改操作在写入数据文件之前，先写入 WAL 日志
- 系统崩溃后，可以通过重放 WAL 日志恢复数据
- WAL 支持事务的原子性：事务提交时写入 COMMIT 记录

日志记录格式:
<record_size:4><record_type:1><txn_id:8><timestamp:8><data>

记录类型:
- 0x01: INSERT
- 0x02: UPDATE
- 0x03: DELETE
- 0x10: TXN_BEGIN
- 0x11: TXN_COMMIT
- 0x12: TXN_ABORT
- 0x20: CHECKPOINT
"""

import os
import struct
import time
import threading
from typing import Optional, List, Dict, Any, Tuple
import json


class WALRecordType:
    INSERT = 0x01
    UPDATE = 0x02
    DELETE = 0x03
    TXN_BEGIN = 0x10
    TXN_COMMIT = 0x11
    TXN_ABORT = 0x12
    CHECKPOINT = 0x20


class WALRecord:
    """WAL 日志记录"""

    def __init__(
        self,
        record_type: int,
        txn_id: int,
        data: Dict[str, Any],
        timestamp: Optional[float] = None,
        lsn: Optional[int] = None,
    ):
        self.record_type = record_type
        self.txn_id = txn_id
        self.timestamp = timestamp or time.time()
        self.data = data
        self.lsn = lsn  # Log Sequence Number

    def to_bytes(self) -> bytes:
        """序列化为二进制"""
        data_json = json.dumps(self.data, ensure_ascii=False).encode("utf-8")

        header = struct.pack(
            ">BBQd",
            0,  # 占位，record_size
            self.record_type,
            self.txn_id,
            self.timestamp,
        )

        record = header + data_json
        record_size = len(record)
        record = struct.pack(">I", record_size) + record[4:]

        return record

    @classmethod
    def from_bytes(cls, data: bytes) -> "WALRecord":
        """从二进制反序列化"""
        offset = 0

        record_size = struct.unpack_from(">I", data, offset)[0]
        offset += 4

        record_type = struct.unpack_from(">B", data, offset)[0]
        offset += 1

        txn_id = struct.unpack_from(">Q", data, offset)[0]
        offset += 8

        timestamp = struct.unpack_from(">d", data, offset)[0]
        offset += 8

        data_json = data[offset:record_size].decode("utf-8")
        data_dict = json.loads(data_json)

        return cls(record_type, txn_id, data_dict, timestamp)

    def __repr__(self) -> str:
        type_names = {
            0x01: "INSERT",
            0x02: "UPDATE",
            0x03: "DELETE",
            0x10: "TXN_BEGIN",
            0x11: "TXN_COMMIT",
            0x12: "TXN_ABORT",
            0x20: "CHECKPOINT",
        }
        type_name = type_names.get(self.record_type, f"UNKNOWN({self.record_type})")
        return f"WALRecord(type={type_name}, txn={self.txn_id}, data={self.data})"


class WAL:
    """
    预写日志管理器

    功能:
    - 追加日志记录
    - 按事务读取日志
    - 崩溃恢复：重放日志
    - 日志截断/检查点
    """

    def __init__(self, log_dir: str, log_name: str = "wal.log"):
        """
        初始化 WAL
        
        Args:
            log_dir: 日志目录
            log_name: 日志文件名
        """
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, log_name)

        self._file = None
        self._lock = threading.RLock()
        self._current_lsn = 0
        self._txn_counter = 0

        self._open_log_file()
        self._recover_if_needed()

    def _open_log_file(self) -> None:
        """打开日志文件"""
        if self._file is None or self._file.closed:
            self._file = open(self.log_path, "ab+")

    def _recover_if_needed(self) -> None:
        """检查是否需要恢复"""
        if os.path.exists(self.log_path):
            file_size = os.path.getsize(self.log_path)
            if file_size > 0:
                last_record = self._read_last_record()
                if last_record:
                    self._current_lsn = last_record.lsn or 0

    def _read_last_record(self) -> Optional[WALRecord]:
        """读取最后一条记录（用于恢复LSN）"""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                file_size = f.tell()

                if file_size < 4:
                    return None

                pos = file_size - 4
                while pos >= 0:
                    f.seek(pos)
                    size_bytes = f.read(4)
                    if len(size_bytes) < 4:
                        break

                    record_size = struct.unpack(">I", size_bytes)[0]
                    if pos + record_size == file_size and record_size > 12:
                        f.seek(pos)
                        record_data = f.read(record_size)
                        return WALRecord.from_bytes(record_data)

                    pos -= 1

                return None
        except Exception:
            return None

    def append(
        self,
        record_type: int,
        txn_id: int,
        data: Dict[str, Any],
        sync: bool = True,
    ) -> int:
        """
        追加一条日志记录
        
        Args:
            record_type: 记录类型
            txn_id: 事务ID
            data: 记录数据
            sync: 是否立即刷盘
            
        Returns:
            LSN (日志序列号)
        """
        with self._lock:
            self._open_log_file()
            self._current_lsn += 1
            lsn = self._current_lsn

            record = WALRecord(record_type, txn_id, data, lsn=lsn)
            record_bytes = record.to_bytes()

            self._file.write(record_bytes)
            if sync:
                self._file.flush()
                os.fsync(self._file.fileno())

            return lsn

    def begin_transaction(self) -> int:
        """开始一个事务，返回事务ID"""
        with self._lock:
            self._txn_counter += 1
            txn_id = self._txn_counter
            self.append(WALRecordType.TXN_BEGIN, txn_id, {})
            return txn_id

    def commit_transaction(self, txn_id: int) -> None:
        """提交事务"""
        self.append(WALRecordType.TXN_COMMIT, txn_id, {})

    def abort_transaction(self, txn_id: int) -> None:
        """回滚事务"""
        self.append(WALRecordType.TXN_ABORT, txn_id, {})

    def log_insert(self, txn_id: int, doc_id: str, doc_data: Dict[str, Any]) -> int:
        """记录插入操作"""
        return self.append(
            WALRecordType.INSERT,
            txn_id,
            {"doc_id": doc_id, "doc_data": doc_data},
        )

    def log_update(
        self,
        txn_id: int,
        doc_id: str,
        old_data: Dict[str, Any],
        new_data: Dict[str, Any],
    ) -> int:
        """记录更新操作"""
        return self.append(
            WALRecordType.UPDATE,
            txn_id,
            {
                "doc_id": doc_id,
                "old_data": old_data,
                "new_data": new_data,
            },
        )

    def log_delete(self, txn_id: int, doc_id: str, old_data: Dict[str, Any]) -> int:
        """记录删除操作"""
        return self.append(
            WALRecordType.DELETE,
            txn_id,
            {"doc_id": doc_id, "old_data": old_data},
        )

    def iterate_records(self) -> List[WALRecord]:
        """
        迭代所有日志记录
        
        Returns:
            日志记录列表
        """
        records = []
        if not os.path.exists(self.log_path):
            return records

        try:
            with open(self.log_path, "rb") as f:
                lsn = 0
                while True:
                    size_bytes = f.read(4)
                    if len(size_bytes) < 4:
                        break

                    record_size = struct.unpack(">I", size_bytes)[0]
                    if record_size < 12:
                        break

                    record_data = f.read(record_size - 4)
                    if len(record_data) < record_size - 4:
                        break

                    full_data = size_bytes + record_data
                    try:
                        record = WALRecord.from_bytes(full_data)
                        lsn += 1
                        record.lsn = lsn
                        records.append(record)
                    except Exception:
                        break

        except Exception as e:
            print(f"Error reading WAL: {e}")

        return records

    def get_transaction_records(self, txn_id: int) -> List[WALRecord]:
        """获取指定事务的所有记录"""
        all_records = self.iterate_records()
        return [r for r in all_records if r.txn_id == txn_id]

    def checkpoint(self) -> None:
        """
        创建检查点，标记之前的日志可以安全删除
        
        注意：实际生产中会配合数据文件的刷盘
        """
        self.append(WALRecordType.CHECKPOINT, 0, {"lsn": self._current_lsn})

    def truncate_before_lsn(self, lsn: int) -> None:
        """
        截断指定 LSN 之前的日志
        
        Args:
            lsn: 保留从该 LSN 开始的日志
        """
        records = self.iterate_records()
        keep_records = [r for r in records if r.lsn and r.lsn >= lsn]

        with self._lock:
            self._file.close()
            self._file = None

            tmp_path = self.log_path + ".tmp"
            with open(tmp_path, "wb") as f:
                for record in keep_records:
                    f.write(record.to_bytes())

            os.replace(tmp_path, self.log_path)

            self._file = open(self.log_path, "ab+")
            self._current_lsn = lsn - 1

    def recover(self) -> List[Tuple[str, Dict[str, Any]]]:
        """
        崩溃恢复：重放 WAL 日志
        
        恢复规则:
        - 有 TXN_COMMIT 的事务: 全部重放
        - txn_id = 0 的单操作（隐式自动提交）: 全部重放
        - 有 TXN_ABORT 的事务: 跳过
        - 只有 TXN_BEGIN 但无 COMMIT/ABORT 的事务: 跳过（崩溃时未提交）
        
        Returns:
            需要应用的操作列表 [(operation, data), ...]
        """
        records = self.iterate_records()
        committed_txns: set = set()
        aborted_txns: set = set()
        txn_operations: Dict[int, List[Tuple[str, Dict[str, Any]]]] = {}
        auto_commit_ops: List[Tuple[str, Dict[str, Any]]] = []

        for record in records:
            if record.record_type == WALRecordType.TXN_BEGIN:
                txn_operations.setdefault(record.txn_id, [])
            elif record.record_type == WALRecordType.TXN_COMMIT:
                committed_txns.add(record.txn_id)
            elif record.record_type == WALRecordType.TXN_ABORT:
                aborted_txns.add(record.txn_id)
            elif record.record_type in (
                WALRecordType.INSERT,
                WALRecordType.UPDATE,
                WALRecordType.DELETE,
            ):
                op_type = {
                    WALRecordType.INSERT: "INSERT",
                    WALRecordType.UPDATE: "UPDATE",
                    WALRecordType.DELETE: "DELETE",
                }[record.record_type]

                if record.txn_id == 0:
                    auto_commit_ops.append((op_type, record.data))
                else:
                    txn_operations.setdefault(record.txn_id, [])
                    txn_operations[record.txn_id].append((op_type, record.data))

        result: List[Tuple[str, Dict[str, Any]]] = []
        result.extend(auto_commit_ops)
        for txn_id, ops in txn_operations.items():
            if txn_id in committed_txns and txn_id not in aborted_txns:
                result.extend(ops)

        return result

    def close(self) -> None:
        """关闭 WAL"""
        with self._lock:
            if self._file and not self._file.closed:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()
                self._file = None

    def __del__(self):
        self.close()

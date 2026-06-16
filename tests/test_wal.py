"""测试 WAL"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb.storage.wal import WAL, WALRecordType

print("测试 WAL...")

tmp_dir = tempfile.mkdtemp(prefix="docdb_test_")
print(f"测试目录: {tmp_dir}")

try:
    print("  创建 WAL...")
    wal = WAL(tmp_dir)
    print("  OK")

    print("\n  写入日志...")
    txn_id = wal.begin_transaction()
    print(f"  事务 ID: {txn_id}")

    lsn1 = wal.log_insert(txn_id, "doc1", {"name": "Alice"})
    print(f"  插入日志 LSN: {lsn1}")

    lsn2 = wal.log_update(txn_id, "doc1", {"name": "Alice"}, {"name": "Alice2"})
    print(f"  更新日志 LSN: {lsn2}")

    wal.commit_transaction(txn_id)
    print("  提交事务")

    print("\n  读取所有日志...")
    records = wal.iterate_records()
    print(f"  日志记录数: {len(records)}")
    for r in records:
        print(f"    LSN={r.lsn}, type={r.record_type}, txn={r.txn_id}")

    print("\n  关闭 WAL...")
    wal.close()

    print("\n✅ WAL 测试通过")

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

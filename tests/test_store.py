"""最简化测试 - 只测试 DocumentStore"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb.core.document import Document
from docdb.storage.document_store import DocumentStore

print("测试 DocumentStore...")

tmp_dir = tempfile.mkdtemp(prefix="docdb_test_")
print(f"测试目录: {tmp_dir}")

try:
    print("  创建 DocumentStore...")
    store = DocumentStore(tmp_dir)
    print(f"  创建成功，文档数: {store.count()}")

    print("\n  插入文档...")
    doc1 = Document({"name": "Alice", "age": 30})
    store.insert(doc1)
    print(f"  插入 1 篇，文档数: {store.count()}")

    print("\n  读取文档...")
    doc_read = store.get(doc1.id)
    if doc_read:
        print(f"  读取成功: name={doc_read.get('name')}, age={doc_read.get('age')}")
    else:
        print("  读取失败!")

    print("\n  迭代文档...")
    for doc in store.iterate():
        print(f"    - {doc.get('name')}")

    print("\n  关闭存储...")
    store.close()

    print("\n✅ DocumentStore 测试通过")

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

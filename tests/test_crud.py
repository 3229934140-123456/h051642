"""快速测试 - 数据库 CRUD"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb import Database

print("测试数据库 CRUD...")

tmp_dir = tempfile.mkdtemp(prefix="docdb_test_")
print(f"测试目录: {tmp_dir}")

try:
    db = Database("testdb", data_dir=tmp_dir)
    print("数据库创建成功")

    users = db.create_collection("users")
    print(f"集合创建成功: {db.list_collections()}")

    print("\n插入文档...")
    doc1 = users.insert_one({
        "name": "Alice",
        "age": 30,
        "city": "Beijing",
    })
    print(f"  插入 1 篇: {doc1.id}")

    docs = users.insert_many([
        {"name": "Bob", "age": 25, "city": "Shanghai"},
        {"name": "Charlie", "age": 35, "city": "Beijing"},
    ])
    print(f"  批量插入 {len(docs)} 篇")
    print(f"  总文档数: {users.count()}")

    print("\n查询测试...")
    all_users = users.find()
    print(f"  find 全部: {len(all_users)} 篇")

    beijing_users = users.find({"city": "Beijing"})
    print(f"  city=Beijing: {len(beijing_users)} 篇")

    age_range = users.find({"age": {"$gt": 25, "$lte": 35}})
    print(f"  25 < age <= 35: {len(age_range)} 篇")

    print("\n排序测试...")
    sorted_asc = users.find(sort={"age": 1})
    print(f"  按 age 升序: {[d.get('name') for d in sorted_asc]}")

    print("\n更新测试...")
    updated = users.update_one({"name": "Alice"}, {"$set": {"age": 31}})
    print(f"  更新 Alice: {updated} 篇")
    alice = users.find_one({"name": "Alice"})
    print(f"  新年龄: {alice.get('age')}")

    print("\n删除测试...")
    deleted = users.delete_one({"name": "Bob"})
    print(f"  删除 Bob: {deleted} 篇")
    print(f"  剩余文档数: {users.count()}")

    print("\n投影测试...")
    projected = users.find_one({"name": "Charlie"}, projection={"name": 1, "age": 1, "_id": 0})
    print(f"  投影结果: {projected.to_dict() if projected else None}")

    db.close()
    print("\n✅ 数据库 CRUD 测试通过")

finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

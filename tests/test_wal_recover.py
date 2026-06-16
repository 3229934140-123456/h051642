"""WAL 恢复测试 - 模拟提交事务后重启存储"""
import os
import sys
import shutil
import tempfile
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb import Database


def test_wal_recover_with_db():
    """测试 Database 级别的 WAL 恢复（含插入、更新、删除、索引）"""
    print("=" * 60)
    print("WAL 恢复测试: Database 级别（插入/更新/删除/索引）")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_wal_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.create_index("age", index_type="btree")
        users.create_index("city", index_type="btree")

        # 1. 插入
        users.insert_one({"name": "Alice", "age": 30, "city": "Beijing"})
        users.insert_one({"name": "Bob", "age": 25, "city": "Shanghai"})
        users.insert_one({"name": "Charlie", "age": 35, "city": "Beijing"})
        users.insert_one({"name": "Diana", "age": 28, "city": "Shenzhen"})
        print(f"  [1] 插入 4 篇文档，总计数: {users.count()}")
        assert users.count() == 4

        # 2. 更新
        users.update_one({"name": "Alice"}, {"$set": {"age": 31, "email": "alice@test.com"}})
        users.update_many({"city": "Beijing"}, {"$inc": {"age": 1}})
        print(f"  [2] 更新 Alice 和北京用户")

        # 3. 删除
        users.delete_one({"name": "Bob"})
        print(f"  [3] 删除 Bob，总计数: {users.count()}")
        assert users.count() == 3

        # 4. 验证当前状态
        alice_before = users.find_one({"name": "Alice"})
        print(f"  Alice: age={alice_before.get('age')}, email={alice_before.get('email')}")
        assert alice_before.get("age") == 32
        assert alice_before.get("email") == "alice@test.com"

        charlie_before = users.find_one({"name": "Charlie"})
        print(f"  Charlie: age={charlie_before.get('age')}")
        assert charlie_before.get("age") == 36

        consistency_before = users.index_manager.validate_index_consistency()
        for name, info in consistency_before.items():
            print(f"  索引 {name} 一致性: {info['consistent']}")
            assert info["consistent"]

        db.close()
        print("  关闭数据库...\n")

        # 5. 重新打开
        print("  重新打开数据库...")
        db2 = Database("testdb", data_dir=tmp_dir)
        users2 = db2.get_collection("users")

        print(f"  [恢复后] 文档总数: {users2.count()}")
        assert users2.count() == 3, f"期望 3，实际 {users2.count()}"

        alice_after = users2.find_one({"name": "Alice"})
        assert alice_after is not None
        assert alice_after.get("age") == 32, f"Alice age 期望 32，实际 {alice_after.get('age')}"
        assert alice_after.get("email") == "alice@test.com"
        print(f"  Alice 恢复: age={alice_after.get('age')}, email={alice_after.get('email')} ✅")

        charlie_after = users2.find_one({"name": "Charlie"})
        assert charlie_after is not None
        assert charlie_after.get("age") == 36
        print(f"  Charlie 恢复: age={charlie_after.get('age')} ✅")

        diana_after = users2.find_one({"name": "Diana"})
        assert diana_after is not None
        assert diana_after.get("age") == 28
        print(f"  Diana 恢复: age={diana_after.get('age')} ✅")

        bob_after = users2.find_one({"name": "Bob"})
        assert bob_after is None, "Bob 应该已删除"
        print(f"  Bob 已删除（不存在）✅")

        # 6. 检查索引查询
        beijing = users2.find({"city": "Beijing"})
        print(f"  索引查询 city=Beijing: {len(beijing)} 篇 (期望 2)")
        assert len(beijing) == 2

        age_range = users2.find({"age": {"$gt": 30, "$lt": 40}})
        print(f"  索引查询 30<age<40: {len(age_range)} 篇 (期望 2)")
        assert len(age_range) == 2

        # 7. 索引一致性
        consistency_after = users2.index_manager.validate_index_consistency()
        for name, info in consistency_after.items():
            print(f"  恢复后索引 {name} 一致性: {info['consistent']}")
            assert info["consistent"]

        db2.close()
        print("\n✅ Database 级别 WAL 恢复测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_wal_recover_with_db()
        print("=" * 60)
        print("所有 WAL 恢复测试通过 ✅")
        print("=" * 60)
    except AssertionError as e:
        print(f"❌ 断言失败: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ 异常: {e}")
        sys.exit(1)

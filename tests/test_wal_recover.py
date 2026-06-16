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


def test_wal_recover_txn_boundary():
    """测试 WAL 恢复按事务边界判断：已提交的保留，失败和手动回滚的不恢复"""
    print("=" * 60)
    print("WAL 恢复测试: 事务边界 - 已提交保留，失败/回滚不恢复")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_wal_txn_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.create_index("age", index_type="btree")

        # --- 基准数据 ---
        base = users.insert_one({"name": "Base", "age": 10})
        print(f"  [基准] 插入 Base, count={users.count()}")

        # --- 场景1: 成功提交的事务（插入+更新+删除） ---
        print("\n  [场景1] 成功提交事务: 插入 CommittedInsert, 更新 Base->11, 删除 Base")
        txn_commit = db.start_transaction()
        txn_commit.insert("users", {"name": "CommittedInsert", "age": 20})
        txn_commit.update_one("users", {"name": "Base"}, {"$set": {"age": 11}})
        txn_commit.delete_one("users", {"name": "Base"})
        txn_commit.commit()
        print(f"  提交后 count={users.count()}")
        assert users.count() == 1  # 只剩 CommittedInsert

        # --- 场景2: 手动回滚的事务 ---
        print("\n  [场景2] 手动 abort 事务: 插入 AbortedInsert, 然后 abort")
        txn_abort = db.start_transaction()
        txn_abort.insert("users", {"name": "AbortedInsert", "age": 99})
        txn_abort.abort()
        print(f"  abort 后 count={users.count()}")
        assert users.count() == 1

        # --- 场景3: 提交失败的事务（唯一索引冲突） ---
        print("\n  [场景3] 提交失败事务: 唯一索引冲突触发回滚")
        users.create_index("name", index_type="btree", unique=True)
        txn_fail = db.start_transaction()
        txn_fail.insert("users", {"name": "FailInsert", "age": 88})
        txn_fail.insert("users", {"name": "CommittedInsert", "age": 77})  # 冲突
        try:
            txn_fail.commit()
            assert False, "应该提交失败"
        except Exception:
            print(f"  提交失败（符合预期）")
        print(f"  失败后 count={users.count()}")
        assert users.count() == 1

        db.close()
        print("\n  --- 关闭数据库，重新打开验证 WAL 恢复 ---")

        # --- 重启验证 ---
        db2 = Database("testdb", data_dir=tmp_dir)
        users2 = db2.get_collection("users")

        print(f"  [恢复后] count={users2.count()} (预期: 1)")
        assert users2.count() == 1, f"重启后期望 1，实际 {users2.count()}"

        ci = users2.find_one({"name": "CommittedInsert"})
        print(f"  已提交插入 CommittedInsert: {'存在' if ci else '不存在'} (预期: 存在)")
        assert ci is not None
        assert ci.get("age") == 20

        base_after = users2.find_one({"name": "Base"})
        print(f"  已提交删除 Base: {'存在' if base_after else '不存在'} (预期: 不存在)")
        assert base_after is None

        ai = users2.find_one({"name": "AbortedInsert"})
        print(f"  手动回滚 AbortedInsert: {'存在' if ai else '不存在'} (预期: 不存在)")
        assert ai is None

        fi = users2.find_one({"name": "FailInsert"})
        print(f"  失败事务 FailInsert: {'存在' if fi else '不存在'} (预期: 不存在)")
        assert fi is None

        # 索引查询验证
        idx_age = users2.find({"age": 20})
        print(f"  索引查询 age=20: {len(idx_age)} 条 (预期: 1)")
        assert len(idx_age) == 1
        assert idx_age[0].get("name") == "CommittedInsert"

        idx_bad = users2.find({"age": {"$in": [99, 88]}})
        print(f"  索引查询 age=99/88: {len(idx_bad)} 条 (预期: 0)")
        assert len(idx_bad) == 0

        consistency = users2.index_manager.validate_index_consistency()
        for name, info in consistency.items():
            print(f"  恢复后索引 {name} 一致性: {info['consistent']}")
            assert info["consistent"]

        db2.close()
        print("\n✅ WAL 事务边界恢复测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_wal_recover_with_db()
        test_wal_recover_txn_boundary()
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

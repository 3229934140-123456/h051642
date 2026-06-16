"""事务原子性测试 - 唯一索引冲突场景，中间失败要全部回滚"""
import os
import sys
import shutil
import tempfile
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb import Database


def test_transaction_atomic_unique_index():
    """测试事务原子性：多文档插入中唯一索引冲突，全部回滚"""
    print("=" * 60)
    print("事务原子性测试: 唯一索引冲突全部回滚")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_txn_atomic_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.create_index("email", index_type="btree", unique=True)

        # 先插入一条有 email 的记录（作为冲突源）
        users.insert_one({"name": "Existing", "email": "duplicate@test.com", "age": 99})
        print(f"  预置 1 条冲突记录")
        assert users.count() == 1

        # 开启事务，插入 3 条，第 3 条 email 冲突
        print(f"\n  开启事务，插入 3 条（第 3 条 email 冲突）...")
        txn = db.start_transaction()
        try:
            txn.insert("users", {"name": "Alice", "email": "alice@test.com", "age": 30})
            print("    插入 Alice: OK")
            txn.insert("users", {"name": "Bob", "email": "bob@test.com", "age": 25})
            print("    插入 Bob: OK")
            # 这一条会触发唯一索引冲突
            txn.insert("users", {"name": "Charlie", "email": "duplicate@test.com", "age": 35})
            print("    插入 Charlie: (预期失败)")
            txn.commit()
            print("  ❌ 事务不该提交成功！")
            assert False, "事务应该失败"
        except Exception as e:
            print(f"  事务提交失败（符合预期）: {type(e).__name__}: {e}")

        # 验证事务外文档数量应该还是 1（Alice 和 Bob 也应该被回滚）
        after_count = users.count()
        print(f"\n  事务失败后文档数量: {after_count} (预期: 1)")
        all_docs = users.find()
        for d in all_docs:
            print(f"    - 残留文档: {d.get('name')}, email={d.get('email')}, id={d.id}")
        assert after_count == 1, f"期望 1，实际 {after_count}"

        alice = users.find_one({"name": "Alice"})
        print(f"  Alice 是否存在: {alice is not None} (预期: False)")
        assert alice is None, "Alice 应该被回滚"

        bob = users.find_one({"name": "Bob"})
        print(f"  Bob 是否存在: {bob is not None} (预期: False)")
        assert bob is None, "Bob 应该被回滚"

        # 验证索引一致性
        consistency = users.index_manager.validate_index_consistency()
        for idx_name, info in consistency.items():
            print(f"  索引 {idx_name} 一致性: {info['consistent']}")
            assert info["consistent"]

        # 再验证通过索引查询 Alice/Bob 也查不到
        alice_by_idx = users.find({"email": "alice@test.com"})
        print(f"  通过索引查 alice@test.com: {len(alice_by_idx)} 条 (预期: 0)")
        assert len(alice_by_idx) == 0

        bob_by_idx = users.find({"email": "bob@test.com"})
        print(f"  通过索引查 bob@test.com: {len(bob_by_idx)} 条 (预期: 0)")
        assert len(bob_by_idx) == 0

        old_by_idx = users.find({"email": "duplicate@test.com"})
        print(f"  通过索引查 duplicate@test.com: {len(old_by_idx)} 条 (预期: 1)")
        assert len(old_by_idx) == 1
        assert old_by_idx[0].get("name") == "Existing"

        # --- 关库重开再查 ---
        db.close()
        print("\n  [重启验证] 关闭数据库，重新打开...")
        db_reopen = Database("testdb", data_dir=tmp_dir)
        users_reopen = db_reopen.get_collection("users")

        reopen_count = users_reopen.count()
        print(f"  重启后文档数量: {reopen_count} (预期: 1)")
        assert reopen_count == 1, f"重启后期望 1，实际 {reopen_count}"

        alice_reopen = users_reopen.find_one({"name": "Alice"})
        print(f"  重启后 Alice 是否存在: {alice_reopen is not None} (预期: False)")
        assert alice_reopen is None, "重启后 Alice 应该被回滚"

        bob_reopen = users_reopen.find_one({"name": "Bob"})
        print(f"  重启后 Bob 是否存在: {bob_reopen is not None} (预期: False)")
        assert bob_reopen is None, "重启后 Bob 应该被回滚"

        old_reopen = users_reopen.find_one({"name": "Existing"})
        print(f"  重启后旧用户 Existing 是否存在: {old_reopen is not None} (预期: True)")
        assert old_reopen is not None, "重启后旧用户应该保留"

        # 重启后按 email 索引查
        alice_idx_reopen = users_reopen.find({"email": "alice@test.com"})
        print(f"  重启后通过索引查 alice@test.com: {len(alice_idx_reopen)} 条 (预期: 0)")
        assert len(alice_idx_reopen) == 0

        bob_idx_reopen = users_reopen.find({"email": "bob@test.com"})
        print(f"  重启后通过索引查 bob@test.com: {len(bob_idx_reopen)} 条 (预期: 0)")
        assert len(bob_idx_reopen) == 0

        consistency_reopen = users_reopen.index_manager.validate_index_consistency()
        for idx_name, info in consistency_reopen.items():
            print(f"  重启后索引 {idx_name} 一致性: {info['consistent']}")
            assert info["consistent"]

        db_reopen.close()
        print("\n✅ 唯一索引冲突回滚测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_transaction_atomic_mixed_ops():
    """测试事务原子性：插入+更新+删除混合，中途失败全部回滚"""
    print("=" * 60)
    print("事务原子性测试: 插入+更新+删除混合中途失败回滚")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_txn_mixed_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")

        users.insert_one({"name": "Alice", "age": 30})
        users.insert_one({"name": "Bob", "age": 25})
        print(f"  初始 2 条: Alice(30), Bob(25)")

        before_count = users.count()
        alice_before = users.find_one({"name": "Alice"})
        bob_before = users.find_one({"name": "Bob"})

        # 事务: 更新 Alice 年龄，删除 Bob，再插入两条新的
        # 故意在中途制造一个不存在的更新（不会报错，但最后我们模拟异常）
        print(f"\n  开启事务: 更新 Alice->31, 删除 Bob, 插入 Charlie/Diana")
        txn = db.start_transaction()
        try:
            txn.update_one("users", {"name": "Alice"}, {"$set": {"age": 31}})
            print("    更新 Alice age=31: OK")

            txn.delete_one("users", {"name": "Bob"})
            print("    删除 Bob: OK")

            txn.insert("users", {"name": "Charlie", "age": 35})
            print("    插入 Charlie: OK")

            txn.insert("users", {"name": "Diana", "age": 28})
            print("    插入 Diana: OK")

            # 模拟异常（例如程序崩溃或中间校验失败）
            raise RuntimeError("模拟事务中途异常")

        except RuntimeError as e:
            print(f"  事务异常（符合预期）: {e}")
            # 显式回滚（异常退出时 __exit__ 也会自动回滚）
            if txn.state.value == "active":
                txn.abort()

        # 验证状态回到事务前
        after_count = users.count()
        print(f"\n  回滚后文档数量: {after_count} (预期: {before_count})")
        assert after_count == before_count, f"期望 {before_count}，实际 {after_count}"

        alice_after = users.find_one({"name": "Alice"})
        print(f"  Alice 年龄: {alice_after.get('age') if alice_after else None} (预期: {alice_before.get('age')})")
        assert alice_after is not None
        assert alice_after.get("age") == alice_before.get("age")

        bob_after = users.find_one({"name": "Bob"})
        print(f"  Bob 是否存在: {bob_after is not None} (预期: True)")
        assert bob_after is not None

        charlie = users.find_one({"name": "Charlie"})
        diana = users.find_one({"name": "Diana"})
        print(f"  Charlie 存在: {charlie is not None}, Diana 存在: {diana is not None} (都应为 False)")
        assert charlie is None
        assert diana is None

        db.close()
        print("\n✅ 混合操作回滚测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_transaction_atomic_unique_index()
        test_transaction_atomic_mixed_ops()
        print("=" * 60)
        print("所有事务原子性测试通过 ✅")
        print("=" * 60)
    except AssertionError as e:
        print(f"❌ 断言失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ 异常: {e}")
        sys.exit(1)

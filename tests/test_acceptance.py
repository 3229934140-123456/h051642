"""验收测试 - 覆盖用户需求全部场景

1. 直接导入 + 查询解析（无 NameError）
2. 唯一索引事务失败：立刻查、按 email 查、关库重开再查，都只剩旧数据
3. WAL 恢复：已提交保留，失败/手动回滚不恢复
4. 投影：name/age 不带 _id 和内部字段，_id:0 也只留业务字段
"""
import os
import sys
import shutil
import tempfile
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

passed = 0
failed = 0


def _check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        print(f"  FAIL: {label}  {detail}")


# ============================================================
# 场景1: 直接导入 + 查询解析（无 NameError）
# ============================================================
def test_import_and_parser():
    print("\n" + "=" * 60)
    print("场景1: 直接导入 docdb + 查询解析（无 NameError）")
    print("=" * 60)

    try:
        import docdb
        _check("import docdb 成功", True)
    except Exception as e:
        _check("import docdb 成功", False, str(e))
        return False

    try:
        from docdb import Database
        from docdb.query import QueryParser
        from docdb.query.filter_tree import (
            FilterNode, EqNode, GtNode, LtNode, GteNode, LteNode,
            InNode, ExistsNode, AndNode, OrNode, NotNode
        )
        _check("from docdb.query 导入解析相关类成功", True)
    except NameError as e:
        _check("from docdb.query 导入无 NameError", False, str(e))
        return False
    except Exception as e:
        _check("from docdb.query 导入解析相关类成功", False, str(e))
        return False

    try:
        parser = QueryParser()
        tree = parser.parse_query({"name": "Alice", "age": {"$gt": 20}})
        _check("parse_query 简单查询 OK", tree is not None)

        tree2 = parser.parse_query({"$or": [{"name": "Alice"}, {"age": {"$lt": 25}}]})
        _check("parse_query $or 查询 OK", tree2 is not None)

        tree3 = parser.parse_query({"$and": [{"status": "active"}, {"age": {"$gte": 18}}]})
        _check("parse_query $and 查询 OK", tree3 is not None)

        tree4 = parser.parse_query({"address.city": "Beijing"})
        _check("parse_query 嵌套字段查询 OK", tree4 is not None)
    except NameError as e:
        _check("parse_query 执行无 NameError", False, str(e))
        return False
    except Exception as e:
        _check("parse_query 执行成功", False, str(e))
        return False

    try:
        p1 = parser.parse_projection({"name": 1, "age": 1})
        _check("parse_projection include OK", p1 is not None)

        p2 = parser.parse_projection({"_id": 0, "name": 1})
        _check("parse_projection _id:0 OK", p2 is not None)

        p3 = parser.parse_projection({"_id": 0})
        _check("parse_projection 只排除 _id OK", p3 is not None)
    except Exception as e:
        _check("parse_projection 执行成功", False, str(e))
        return False

    return True


# ============================================================
# 场景2: 唯一索引事务失败 - 即时查询 + 重启查询
# ============================================================
def test_unique_index_txn_rollback():
    print("\n" + "=" * 60)
    print("场景2: 唯一索引事务失败 - 即时查 + 按索引查 + 重启查")
    print("=" * 60)

    from docdb import Database

    tmp_dir = tempfile.mkdtemp(prefix="docdb_accept_txn_")
    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.create_index("email", index_type="btree", unique=True)

        old = users.insert_one({"name": "OldUser", "email": "dup@test.com", "age": 50})
        _check("事务前 count=1", users.count() == 1)

        # 事务: 插 Alice -> Bob -> 重复 email (冲突)
        txn = db.start_transaction()
        txn.insert("users", {"name": "Alice", "email": "alice@test.com", "age": 25})
        txn.insert("users", {"name": "Bob", "email": "bob@test.com", "age": 30})
        txn.insert("users", {"name": "Conflict", "email": "dup@test.com", "age": 35})
        try:
            txn.commit()
            _check("事务提交失败(符合预期)", False, "commit unexpectedly succeeded")
        except Exception:
            _check("事务提交失败(符合预期)", True)

        # --- 即时查 ---
        _check("[即时] count=1", users.count() == 1, f"got {users.count()}")

        alice = users.find_one({"email": "alice@test.com"})
        _check("[即时] 按 email 查 alice 不存在", alice is None)

        bob = users.find_one({"email": "bob@test.com"})
        _check("[即时] 按 email 查 bob 不存在", bob is None)

        old_doc = users.find_one({"email": "dup@test.com"})
        _check("[即时] 按 email 查旧数据仍存在",
              old_doc is not None and old_doc.get("name") == "OldUser")

        all_docs = users.find()
        _check("[即时] 全表扫描只有 1 条", len(all_docs) == 1)

        consistency = users.index_manager.validate_index_consistency()
        for name, info in consistency.items():
            _check(f"[即时] 索引 {name} 一致性", info["consistent"])

        db.close()

        # --- 重启后查 ---
        db2 = Database("testdb", data_dir=tmp_dir)
        users2 = db2.get_collection("users")

        _check("[重启] count=1", users2.count() == 1, f"got {users2.count()}")

        alice2 = users2.find_one({"email": "alice@test.com"})
        _check("[重启] 按 email 查 alice 不存在", alice2 is None)

        bob2 = users2.find_one({"email": "bob@test.com"})
        _check("[重启] 按 email 查 bob 不存在", bob2 is None)

        old2 = users2.find_one({"email": "dup@test.com"})
        _check("[重启] 按 email 查旧数据仍存在",
              old2 is not None and old2.get("name") == "OldUser")

        all2 = users2.find()
        _check("[重启] 全表扫描只有 1 条", len(all2) == 1)

        consistency2 = users2.index_manager.validate_index_consistency()
        for name, info in consistency2.items():
            _check(f"[重启] 索引 {name} 一致性", info["consistent"])

        db2.close()
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# 场景3: WAL 恢复按事务边界
# ============================================================
def test_wal_recover_by_txn():
    print("\n" + "=" * 60)
    print("场景3: WAL 恢复 - 已提交保留，失败/回滚不恢复")
    print("=" * 60)

    from docdb import Database

    tmp_dir = tempfile.mkdtemp(prefix="docdb_accept_wal_")
    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.create_index("age", index_type="btree")

        users.insert_one({"name": "Base", "age": 10})

        # 已提交事务: 插入+更新+删除
        txn_ok = db.start_transaction()
        txn_ok.insert("users", {"name": "CommittedA", "age": 20})
        txn_ok.insert("users", {"name": "CommittedB", "age": 21})
        txn_ok.update_one("users", {"name": "Base"}, {"$set": {"age": 11}})
        txn_ok.delete_one("users", {"name": "Base"})
        txn_ok.commit()

        # 手动回滚
        txn_abort = db.start_transaction()
        txn_abort.insert("users", {"name": "AbortedX", "age": 99})
        txn_abort.insert("users", {"name": "AbortedY", "age": 98})
        txn_abort.abort()

        # 提交失败（唯一索引冲突）
        users.create_index("name", index_type="btree", unique=True)
        txn_fail = db.start_transaction()
        txn_fail.insert("users", {"name": "FailInsert", "age": 88})
        txn_fail.insert("users", {"name": "CommittedA", "age": 77})
        try:
            txn_fail.commit()
        except Exception:
            pass

        _check("[关闭前] count=2", users.count() == 2, f"got {users.count()}")

        db.close()

        # 重启验证
        db2 = Database("testdb", data_dir=tmp_dir)
        users2 = db2.get_collection("users")

        _check("[重启] count=2", users2.count() == 2, f"got {users2.count()}")

        ca = users2.find_one({"name": "CommittedA"})
        _check("[重启] 已提交 CommittedA 存在", ca is not None and ca.get("age") == 20)

        cb = users2.find_one({"name": "CommittedB"})
        _check("[重启] 已提交 CommittedB 存在", cb is not None and cb.get("age") == 21)

        base = users2.find_one({"name": "Base"})
        _check("[重启] 已提交删除 Base 不存在", base is None)

        ax = users2.find_one({"name": "AbortedX"})
        _check("[重启] 手动回滚 AbortedX 不存在", ax is None)

        ay = users2.find_one({"name": "AbortedY"})
        _check("[重启] 手动回滚 AbortedY 不存在", ay is None)

        fi = users2.find_one({"name": "FailInsert"})
        _check("[重启] 提交失败 FailInsert 不存在", fi is None)

        # 索引查询验证
        idx_ok = users2.find({"age": {"$in": [20, 21]}})
        _check("[重启] 索引查询已提交 age=20/21 共 2 条", len(idx_ok) == 2)

        idx_bad = users2.find({"age": {"$in": [99, 98, 88]}})
        _check("[重启] 索引查询失败/回滚 age=99/98/88 共 0 条", len(idx_bad) == 0)

        consistency = users2.index_manager.validate_index_consistency()
        for name, info in consistency.items():
            _check(f"[重启] 索引 {name} 一致性", info["consistent"])

        db2.close()
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# 场景4: 投影验收
# ============================================================
def test_projection():
    print("\n" + "=" * 60)
    print("场景4: 投影 - 只查 name/age 不带 _id 和内部字段，_id:0 也只留业务字段")
    print("=" * 60)

    from docdb import Database

    tmp_dir = tempfile.mkdtemp(prefix="docdb_accept_proj_")
    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")
        users.insert_one({"name": "Alice", "age": 30, "email": "alice@test.com"})

        def _keys_of_first(r):
            return set(r[0].to_dict().keys()) if r else set()

        # 4.1 include name, age
        r1 = users.find({"name": "Alice"}, projection={"name": 1, "age": 1})
        d1 = _keys_of_first(r1)
        _check("[投影] name,age: 有 name", "name" in d1)
        _check("[投影] name,age: 有 age", "age" in d1)
        _check("[投影] name,age: 无 email", "email" not in d1)
        _check("[投影] name,age: 无 _id", "_id" not in d1, f"keys={d1}")
        _check("[投影] name,age: 无 _version", "_version" not in d1, f"keys={d1}")
        _check("[投影] name,age: 无 _created_at", "_created_at" not in d1, f"keys={d1}")
        _check("[投影] name,age: 无 _updated_at", "_updated_at" not in d1, f"keys={d1}")

        # 4.2 include _id, name (用户明确要 _id)
        r2 = users.find({"name": "Alice"}, projection={"_id": 1, "name": 1})
        d2 = _keys_of_first(r2)
        _check("[投影] _id:1,name:1: 有 _id", "_id" in d2, f"keys={d2}")
        _check("[投影] _id:1,name:1: 有 name", "name" in d2)
        _check("[投影] _id:1,name:1: 无 age", "age" not in d2)
        _check("[投影] _id:1,name:1: 无 _version", "_version" not in d2, f"keys={d2}")

        # 4.3 _id:0 + include name
        r3 = users.find({"name": "Alice"}, projection={"_id": 0, "name": 1})
        d3 = _keys_of_first(r3)
        _check("[投影] _id:0,name:1: 无 _id", "_id" not in d3, f"keys={d3}")
        _check("[投影] _id:0,name:1: 有 name", "name" in d3)
        _check("[投影] _id:0,name:1: 无 age", "age" not in d3)
        _check("[投影] _id:0,name:1: 无系统字段",
              not any(k.startswith("_") for k in d3), f"keys={d3}")

        # 4.4 _id:0 单独使用（排除 _id，保留所有业务字段）
        r4 = users.find({"name": "Alice"}, projection={"_id": 0})
        d4 = _keys_of_first(r4)
        _check("[投影] _id:0: 无 _id", "_id" not in d4, f"keys={d4}")
        _check("[投影] _id:0: 有 name", "name" in d4)
        _check("[投影] _id:0: 有 age", "age" in d4)
        _check("[投影] _id:0: 有 email", "email" in d4)
        _check("[投影] _id:0: 无系统字段",
              not any(k.startswith("_") for k in d4), f"keys={d4}")

        db.close()
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
if __name__ == "__main__":
    try:
        test_import_and_parser()
        test_unique_index_txn_rollback()
        test_wal_recover_by_txn()
        test_projection()

        print("\n" + "=" * 60)
        print(f"验收总结果: {passed} passed, {failed} failed")
        print("=" * 60)
        sys.exit(1 if failed else 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ 验收测试异常: {e}")
        sys.exit(1)

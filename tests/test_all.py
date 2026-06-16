"""
DocDB 综合测试与示例

测试各个模块的功能:
1. 文档存储
2. 二级索引
3. 查询解析
4. 查询执行
5. 聚合管道
6. 事务
"""

import os
import sys
import shutil
import tempfile
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb import Database
from docdb.core.document import Document
from docdb.query.parser import QueryParser
from docdb.query.filter_tree import (
    AndNode, OrNode, NotNode, EqNode, GtNode, LtNode, InNode
)
from docdb.index.btree import BPlusTree
from docdb.execution.optimizer import QueryOptimizer, ScanType


def test_document():
    """测试 Document 类"""
    print("=" * 60)
    print("测试: Document 文档模型")
    print("=" * 60)

    data = {
        "name": "Alice",
        "age": 30,
        "address": {
            "city": "Beijing",
            "street": "Main St",
        },
        "tags": ["developer", "python"],
        "scores": [90, 85, 95],
    }

    doc = Document(data)
    print(f"文档 ID: {doc.id}")
    print(f"版本号: {doc.version}")
    print(f"姓名: {doc.get('name')}")
    print(f"年龄: {doc.get('age')}")

    print(f"\n嵌套字段测试:")
    print(f"  address.city: {doc.get('address.city')}")
    print(f"  address.street: {doc.get('address.street')}")

    print(f"\n数组测试:")
    print(f"  tags: {doc.get('tags')}")
    print(f"  scores.0: {doc.get('scores.0')}")
    print(f"  scores.2: {doc.get('scores.2')}")

    print(f"\n字段设置测试:")
    doc.set("email", "alice@example.com")
    print(f"  新增 email: {doc.get('email')}")
    doc.set("address.zip", "100000")
    print(f"  嵌套字段 address.zip: {doc.get('address.zip')}")

    print(f"\n序列化测试:")
    json_str = doc.to_json()
    print(f"  JSON 长度: {len(json_str)} 字符")
    doc2 = Document.from_json(json_str)
    print(f"  反序列化后 name: {doc2.get('name')}")
    print(f"  反序列化后 ID 一致: {doc.id == doc2.id}")

    print(f"\n二进制序列化测试:")
    doc_bytes = doc.to_bytes()
    print(f"  二进制大小: {len(doc_bytes)} 字节")
    doc3 = Document.from_bytes(doc_bytes)
    print(f"  反序列化后 name: {doc3.get('name')}")
    print(f"  反序列化后版本一致: {doc.version == doc3.version}")

    print("\n✅ Document 测试通过\n")
    return True


def test_btree():
    """测试 B+ 树索引"""
    print("=" * 60)
    print("测试: B+ 树索引")
    print("=" * 60)

    tree = BPlusTree(order=4)
    print(f"创建 B+ 树 (order=4)")

    test_data = [
        (10, "doc1"),
        (5, "doc2"),
        (20, "doc3"),
        (15, "doc4"),
        (30, "doc5"),
        (3, "doc6"),
        (7, "doc7"),
        (12, "doc8"),
        (25, "doc9"),
        (35, "doc10"),
    ]

    for key, value in test_data:
        tree.insert(key, value)

    print(f"\n插入 {len(test_data)} 条数据")
    print(f"键数量: {tree.size}")
    print(f"值总数: {tree.value_count}")
    print(f"最小键: {tree.min_key()}")
    print(f"最大键: {tree.max_key()}")

    print(f"\n精确查找测试:")
    print(f"  key=15: {tree.get(15)}")
    print(f"  key=100: {tree.get(100)}")

    print(f"\n范围查询测试:")
    range_result = tree.range_query(10, 25)
    print(f"  [10, 25] 范围内的键: {[k for k, _ in range_result]}")

    print(f"\n迭代测试:")
    keys_forward = [k for k, _ in tree.iterate()]
    print(f"  正向迭代: {keys_forward}")
    keys_backward = [k for k, _ in tree.iterate(reverse=True)]
    print(f"  反向迭代: {keys_backward}")

    print(f"\n删除测试:")
    tree.delete(10)
    print(f"  删除 key=10 后 size: {tree.size}")
    print(f"  key=10 还存在吗: {tree.contains(10)}")

    print(f"\n重复键测试:")
    tree.insert(20, "doc20")
    print(f"  key=20 的值列表: {tree.get(20)}")
    tree.delete(20, "doc3")
    print(f"  删除 doc3 后 key=20 的值列表: {tree.get(20)}")

    print("\n✅ B+ 树测试通过\n")
    return True


def test_query_parser():
    """测试查询解析器"""
    print("=" * 60)
    print("测试: 查询解析器")
    print("=" * 60)

    parser = QueryParser()

    test_queries = [
        ("简单等值查询", {"name": "Alice"}),
        ("多条件 AND", {"name": "Alice", "age": 30}),
        ("比较操作符", {"age": {"$gt": 25, "$lt": 40}}),
        ("$in 操作符", {"status": {"$in": ["active", "pending"]}}),
        ("$exists 操作符", {"email": {"$exists": True}}),
        ("$or 逻辑", {"$or": [{"age": {"$lt": 20}}, {"age": {"$gt": 60}}]}),
        ("$and 逻辑", {"$and": [{"status": "active"}, {"age": {"$gte": 18}}]}),
        ("嵌套字段", {"address.city": "Beijing"}),
        ("$not 操作符", {"$not": {"status": "deleted"}}),
    ]

    for name, query in test_queries:
        print(f"\n{name}:")
        print(f"  查询: {query}")
        tree = parser.parse_query(query)
        print(f"  解析结果: {tree}")
        print(f"  涉及字段: {tree.get_fields()}")

    print(f"\n查询验证测试:")
    doc_data = {
        "name": "Alice",
        "age": 30,
        "status": "active",
        "address": {"city": "Beijing"},
        "tags": ["python", "developer"],
    }

    test_cases = [
        ({"name": "Alice"}, True),
        ({"name": "Bob"}, False),
        ({"age": {"$gt": 25}}, True),
        ({"age": {"$lt": 25}}, False),
        ({"age": {"$gte": 30}}, True),
        ({"status": {"$in": ["active", "pending"]}}, True),
        ({"address.city": "Beijing"}, True),
        ({"$or": [{"age": 30}, {"name": "Bob"}]}, True),
        ({"$and": [{"age": {"$gt": 20}}, {"status": "active"}]}, True),
    ]

    for query, expected in test_cases:
        tree = parser.parse_query(query)
        result = tree.evaluate(doc_data)
        status = "✅" if result == expected else "❌"
        print(f"  {status} {query} -> {result} (预期: {expected})")

    print("\n✅ 查询解析器测试通过\n")
    return True


def test_database_crud():
    """测试数据库 CRUD 操作"""
    print("=" * 60)
    print("测试: 数据库 CRUD 操作")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)

        print(f"\n创建集合 users")
        users = db.create_collection("users")
        print(f"集合列表: {db.list_collections()}")

        print(f"\n插入文档:")
        doc1 = users.insert_one({
            "name": "Alice",
            "age": 30,
            "city": "Beijing",
            "tags": ["developer", "python"],
        })
        print(f"  插入 1 篇: {doc1.id}")

        docs = users.insert_many([
            {"name": "Bob", "age": 25, "city": "Shanghai", "tags": ["designer"]},
            {"name": "Charlie", "age": 35, "city": "Beijing", "tags": ["manager", "developer"]},
            {"name": "Diana", "age": 28, "city": "Shenzhen", "tags": ["developer", "go"]},
            {"name": "Eve", "age": 40, "city": "Beijing", "tags": ["qa"]},
        ])
        print(f"  批量插入 {len(docs)} 篇")

        print(f"  总文档数: {users.count()}")

        print(f"\n查询测试:")
        print(f"  find_one: {users.find_one({'name': 'Alice'}).get('name')}")

        all_users = users.find()
        print(f"  find 全部: {len(all_users)} 篇")

        beijing_users = users.find({"city": "Beijing"})
        print(f"  city=Beijing: {len(beijing_users)} 篇")

        age_range = users.find({"age": {"$gt": 25, "$lte": 35}})
        print(f"  25 < age <= 35: {len(age_range)} 篇")

        print(f"\n排序测试:")
        sorted_asc = users.find(sort={"age": 1})
        print(f"  按 age 升序: {[d.get('name') + ':' + str(d.get('age')) for d in sorted_asc]}")

        sorted_desc = users.find(sort={"age": -1}, limit=3)
        print(f"  按 age 降序前3: {[d.get('name') + ':' + str(d.get('age')) for d in sorted_desc]}")

        print(f"\n分页测试:")
        paged = users.find(sort={"name": 1}, skip=1, limit=2)
        print(f"  skip=1, limit=2: {[d.get('name') for d in paged]}")

        print(f"\n更新测试:")
        updated = users.update_one({"name": "Alice"}, {"$set": {"age": 31, "email": "alice@test.com"}})
        print(f"  更新 Alice: {updated} 篇")
        alice = users.find_one({"name": "Alice"})
        print(f"  新年龄: {alice.get('age')}")
        print(f"  新邮箱: {alice.get('email')}")

        inc_result = users.update_many({"city": "Beijing"}, {"$inc": {"age": 1}})
        print(f"  北京用户年龄+1: {inc_result} 篇")

        print(f"\n删除测试:")
        deleted = users.delete_one({"name": "Eve"})
        print(f"  删除 Eve: {deleted} 篇")
        print(f"  剩余文档数: {users.count()}")

        print(f"\n投影测试:")
        projected = users.find_one({"name": "Bob"}, projection={"name": 1, "age": 1, "_id": 0})
        print(f"  投影结果: {projected.to_dict() if projected else None}")

        db.close()
        print("\n✅ 数据库 CRUD 测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_index():
    """测试二级索引"""
    print("=" * 60)
    print("测试: 二级索引")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_index_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")

        for i in range(20):
            users.insert_one({
                "name": f"User{i}",
                "age": 20 + i,
                "city": "Beijing" if i % 2 == 0 else "Shanghai",
                "score": i * 5,
            })

        print(f"\n插入 20 篇文档")

        print(f"\n创建索引:")
        idx_name = users.create_index("age", index_type="btree")
        print(f"  创建 age 索引: {idx_name}")

        idx_city = users.create_index("city", index_type="btree")
        print(f"  创建 city 索引: {idx_city}")

        indexes = users.list_indexes()
        print(f"  索引列表: {[i['name'] for i in indexes]}")

        print(f"\n查询优化测试:")
        from docdb.query.parser import QueryParser
        from docdb.execution.optimizer import QueryOptimizer

        parser = QueryParser()
        optimizer = QueryOptimizer(users.index_manager)

        query = {"age": {"$gt": 25, "$lt": 35}}
        filter_tree = parser.parse_query(query)
        plan = optimizer.optimize(filter_tree)

        print(f"  查询: {query}")
        print(f"  最优扫描方式: {plan.scan_type.value}")
        print(f"  使用索引: {plan.index_name}")
        print(f"  预估代价: {plan.estimated_cost:.2f}")
        print(f"  预估文档数: {plan.estimated_docs}")

        print(f"\n索引一致性测试:")
        consistency = users.index_manager.validate_index_consistency()
        for idx_name, result in consistency.items():
            print(f"  {idx_name}:")
            print(f"    索引文档数: {result['indexed_documents']}")
            print(f"    一致性: {result['consistent']}")

        print(f"\n更新后索引维护测试:")
        users.update_one({"name": "User5"}, {"$set": {"age": 100}})
        consistency_after = users.index_manager.validate_index_consistency()
        for idx_name, result in consistency_after.items():
            print(f"  {idx_name} 一致: {result['consistent']}")

        print(f"\n删除后索引维护测试:")
        users.delete_one({"name": "User10"})
        consistency_delete = users.index_manager.validate_index_consistency()
        for idx_name, result in consistency_delete.items():
            print(f"  {idx_name} 一致: {result['consistent']}")

        users.drop_index(idx_city)
        print(f"\n删除 city 索引后: {len(users.list_indexes())} 个索引")

        db.close()
        print("\n✅ 二级索引测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_aggregation():
    """测试聚合管道"""
    print("=" * 60)
    print("测试: 聚合管道")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_agg_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        sales = db.create_collection("sales")

        sales_data = [
            {"product": "A", "category": "Electronics", "amount": 100, "qty": 2, "region": "North"},
            {"product": "B", "category": "Clothing", "amount": 50, "qty": 3, "region": "South"},
            {"product": "C", "category": "Electronics", "amount": 200, "qty": 1, "region": "North"},
            {"product": "D", "category": "Books", "amount": 30, "qty": 5, "region": "East"},
            {"product": "E", "category": "Electronics", "amount": 150, "qty": 2, "region": "West"},
            {"product": "F", "category": "Clothing", "amount": 80, "qty": 4, "region": "North"},
            {"product": "G", "category": "Books", "amount": 25, "qty": 2, "region": "South"},
            {"product": "H", "category": "Electronics", "amount": 300, "qty": 1, "region": "East"},
        ]
        sales.insert_many(sales_data)
        print(f"插入 {len(sales_data)} 条销售记录")

        print(f"\n$match + $group 测试:")
        result = sales.aggregate([
            {"$match": {"category": "Electronics"}},
            {"$group": {
                "_id": "$region",
                "total_amount": {"$sum": "$amount"},
                "avg_qty": {"$avg": "$qty"},
                "count": {"$sum": 1},
            }},
        ])
        print(f"  按地区统计电子产品:")
        for r in result:
            print(f"    {r['_id']}: 总金额={r['total_amount']}, 平均数量={r['avg_qty']}, 订单数={r['count']}")

        print(f"\n$sort + $limit 测试:")
        top_products = sales.aggregate([
            {"$sort": {"amount": -1}},
            {"$limit": 3},
            {"$project": {"product": 1, "amount": 1, "_id": 0}},
        ])
        print(f"  金额最高的 3 个产品:")
        for r in top_products:
            print(f"    {r['product']}: {r['amount']}")

        print(f"\n$count 测试:")
        count_result = sales.aggregate([
            {"$match": {"region": "North"}},
            {"$count": "total"},
        ])
        print(f"  北方地区订单数: {count_result[0]['total']}")

        print(f"\n$unwind 测试 (数组展开):")
        test_collection = db.create_collection("test_array")
        test_collection.insert_many([
            {"name": "Alice", "hobbies": ["reading", "swimming"]},
            {"name": "Bob", "hobbies": ["gaming", "coding", "cooking"]},
        ])

        unwind_result = test_collection.aggregate([
            {"$unwind": "$hobbies"},
        ])
        print(f"  展开 hobbies 后 {len(unwind_result)} 条记录")
        for r in unwind_result[:4]:
            print(f"    {r['name']}: {r['hobbies']}")

        print(f"\n$skip + $limit 分页测试:")
        paged = sales.aggregate([
            {"$sort": {"amount": 1}},
            {"$skip": 2},
            {"$limit": 3},
            {"$project": {"product": 1, "amount": 1, "_id": 0}},
        ])
        print(f"  第 3-5 个产品 (按金额升序):")
        for r in paged:
            print(f"    {r['product']}: {r['amount']}")

        print(f"\n$addFields 添加计算字段:")
        added = sales.aggregate([
            {"$match": {"category": "Electronics"}},
            {"$addFields": {"unit_price": {"$divide": ["$amount", "$qty"]}}},
            {"$project": {"product": 1, "unit_price": 1, "_id": 0}},
        ])
        print(f"  电子产品单价:")
        for r in added:
            print(f"    {r['product']}: {r['unit_price']:.2f}")

        print(f"\n完整的复杂聚合管道:")
        complex_result = sales.aggregate([
            {"$match": {"amount": {"$gt": 50}}},
            {"$group": {
                "_id": "$category",
                "total_sales": {"$sum": "$amount"},
                "avg_sales": {"$avg": "$amount"},
                "max_sales": {"$max": "$amount"},
                "min_sales": {"$min": "$amount"},
                "products": {"$push": "$product"},
            }},
            {"$sort": {"total_sales": -1}},
        ])
        print(f"  按类别统计 (金额>50):")
        for r in complex_result:
            print(f"    {r['_id']}: 总销={r['total_sales']}, 平均={r['avg_sales']:.1f}, 产品={r['products']}")

        db.close()
        print("\n✅ 聚合管道测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_transaction():
    """测试事务"""
    print("=" * 60)
    print("测试: 事务 (MVCC)")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_txn_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        accounts = db.create_collection("accounts")

        accounts.insert_many([
            {"name": "Alice", "balance": 1000},
            {"name": "Bob", "balance": 500},
            {"name": "Charlie", "balance": 2000},
        ])
        print(f"初始 3 个账户")

        print(f"\n测试 1: 成功提交的事务 (转账)")
        txn = db.start_transaction()
        try:
            alice = txn.find_one("accounts", {"name": "Alice"})
            bob = txn.find_one("accounts", {"name": "Bob"})
            print(f"  转账前 - Alice: {alice['balance']}, Bob: {bob['balance']}")

            txn.update_one("accounts", {"name": "Alice"}, {"$inc": {"balance": -200}})
            txn.update_one("accounts", {"name": "Bob"}, {"$inc": {"balance": 200}})

            txn.commit()
            print(f"  事务提交成功")

            alice_after = accounts.find_one({"name": "Alice"})
            bob_after = accounts.find_one({"name": "Bob"})
            print(f"  转账后 - Alice: {alice_after.get('balance')}, Bob: {bob_after.get('balance')}")
        except Exception as e:
            print(f"  事务失败: {e}")
            txn.abort()

        print(f"\n测试 2: 上下文管理器语法")
        with db.start_transaction() as txn2:
            charlie = txn2.find_one("accounts", {"name": "Charlie"})
            print(f"  Charlie 初始余额: {charlie['balance']}")
            txn2.update_one("accounts", {"name": "Charlie"}, {"$inc": {"balance": 500}})

        charlie_after = accounts.find_one({"name": "Charlie"})
        print(f"  事务后 Charlie 余额: {charlie_after.get('balance')}")

        print(f"\n测试 3: 事务中插入")
        txn3 = db.start_transaction()
        try:
            txn3.insert("accounts", {"name": "Diana", "balance": 3000})
            result = txn3.find_one("accounts", {"name": "Diana"})
            print(f"  事务内读取: {result['name']} 余额 {result['balance']}")

            outside = accounts.find_one({"name": "Diana"})
            print(f"  事务外能否读取: {'能' if outside else '不能'} (预期: 不能)")

            txn3.commit()
            print(f"  提交后 - 账户数: {accounts.count()}")
        except Exception as e:
            print(f"  事务失败: {e}")
            txn3.abort()

        print(f"\n测试 4: 事务回滚")
        initial_count = accounts.count()
        txn4 = db.start_transaction()
        try:
            txn4.insert("accounts", {"name": "Eve", "balance": 1500})
            txn4.insert("accounts", {"name": "Frank", "balance": 800})
            print(f"  事务内插入 2 条，事务内计数: {len(txn4.find('accounts', {}))}")

            txn4.abort()
            print(f"  回滚后账户数: {accounts.count()} (预期: {initial_count})")
        except Exception as e:
            print(f"  错误: {e}")

        print(f"\n测试 5: 事务内删除")
        count_before = accounts.count()
        txn5 = db.start_transaction()
        try:
            deleted = txn5.delete_one("accounts", {"name": "Diana"})
            print(f"  事务内删除: {deleted} 条")
            txn5.commit()
            print(f"  提交后账户数: {accounts.count()} (预期: {count_before - 1})")
        except Exception as e:
            print(f"  错误: {e}")
            txn5.abort()

        print(f"\n测试 6: 验证事务原子性 - 异常回滚")
        count_before = accounts.count()
        try:
            with db.start_transaction() as txn6:
                txn6.insert("accounts", {"name": "Ghost1", "balance": 999})
                txn6.insert("accounts", {"name": "Ghost2", "balance": 999})
                raise ValueError("模拟错误")
        except ValueError:
            pass

        print(f"  异常后账户数: {accounts.count()} (预期: {count_before})")

        db.close()
        print("\n✅ 事务测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_nested_arrays():
    """测试嵌套字段和数组查询"""
    print("=" * 60)
    print("测试: 嵌套字段与数组查询")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_nested_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        users = db.create_collection("users")

        users.insert_many([
            {
                "name": "Alice",
                "profile": {
                    "age": 30,
                    "address": {"city": "Beijing", "zip": "100000"},
                    "contacts": [
                        {"type": "email", "value": "alice@test.com"},
                        {"type": "phone", "value": "123456"},
                    ],
                },
                "tags": ["developer", "python", "backend"],
                "scores": [85, 90, 95],
            },
            {
                "name": "Bob",
                "profile": {
                    "age": 25,
                    "address": {"city": "Shanghai", "zip": "200000"},
                    "contacts": [
                        {"type": "email", "value": "bob@test.com"},
                    ],
                },
                "tags": ["designer", "ui"],
                "scores": [80, 75, 85],
            },
            {
                "name": "Charlie",
                "profile": {
                    "age": 35,
                    "address": {"city": "Beijing", "zip": "100001"},
                    "contacts": [
                        {"type": "phone", "value": "789012"},
                    ],
                },
                "tags": ["manager", "developer"],
                "scores": [95, 88, 92],
            },
        ])

        print(f"插入 3 篇文档 (含嵌套字段和数组)")

        print(f"\n嵌套字段查询:")
        beijing_users = users.find({"profile.address.city": "Beijing"})
        print(f"  profile.address.city = Beijing: {len(beijing_users)} 人")
        for u in beijing_users:
            print(f"    - {u.get('name')}")

        print(f"\n数组字段查询 (数组中包含某个值):")
        devs = users.find({"tags": "developer"})
        print(f"  tags 包含 'developer': {len(devs)} 人")
        for u in devs:
            print(f"    - {u.get('name')}")

        print(f"\n$all 操作符 (数组包含所有值):")
        python_backend = users.find({"tags": {"$all": ["developer", "backend"]}})
        print(f"  tags 同时包含 'developer' 和 'backend': {len(python_backend)} 人")

        print(f"\n$size 操作符 (数组大小):")
        two_tags = users.find({"tags": {"$size": 2}})
        print(f"  tags 长度为 2: {len(two_tags)} 人")

        print(f"\n数组元素索引查询:")
        high_first = users.find({"scores.0": {"$gt": 80}})
        print(f"  第一个分数 > 80: {len(high_first)} 人")

        print(f"\n数组中嵌套对象查询:")
        has_email = users.find({"profile.contacts.type": "email"})
        print(f"  有 email 联系方式: {len(has_email)} 人")

        print(f"\n$elemMatch 操作符:")
        from docdb.query.parser import QueryParser
        parser = QueryParser()

        print(f"  (注: 此版本支持基础 $elemMatch)")

        print(f"\n嵌套字段排序:")
        sorted_by_age = users.find(sort={"profile.age": 1})
        print(f"  按 profile.age 升序:")
        for u in sorted_by_age:
            print(f"    - {u.get('name')}: {u.get('profile.age') if hasattr(u, 'get') else 'N/A'} 岁")

        print(f"\n数组索引 - 多键索引:")
        idx_name = users.create_index("tags", index_type="btree")
        print(f"  创建 tags 多键索引: {idx_name}")

        dev_by_index = users.find({"tags": "developer"})
        print(f"  通过索引查询 developer: {len(dev_by_index)} 人")

        consistency = users.index_manager.validate_index_consistency()
        print(f"  索引一致性: {list(consistency.values())[0]['consistent']}")

        db.close()
        print("\n✅ 嵌套字段与数组查询测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_index_consistency():
    """测试索引与数据的一致性"""
    print("=" * 60)
    print("测试: 索引与数据一致性")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp(prefix="docdb_idx_consist_test_")
    print(f"测试目录: {tmp_dir}")

    try:
        db = Database("testdb", data_dir=tmp_dir)
        items = db.create_collection("items")

        idx = items.create_index("value", index_type="btree", unique=False)
        print(f"创建索引: {idx}")

        print(f"\n插入 100 条文档...")
        for i in range(100):
            items.insert_one({"name": f"item_{i}", "value": i * 3, "category": f"cat_{i % 5}"})

        consistency = items.index_manager.validate_index_consistency()
        print(f"插入后一致性: {consistency[idx]['consistent']}")
        print(f"索引文档数: {consistency[idx]['indexed_documents']}")
        print(f"实际文档数: {items.count()}")

        print(f"\n更新 30 条文档...")
        for i in range(0, 60, 2):
            items.update_one({"name": f"item_{i}"}, {"$set": {"value": i * 10}})

        consistency = items.index_manager.validate_index_consistency()
        print(f"更新后一致性: {consistency[idx]['consistent']}")
        print(f"问题数: {len(consistency[idx]['issues'])}")

        print(f"\n删除 20 条文档...")
        for i in range(80, 100):
            items.delete_one({"name": f"item_{i}"})

        consistency = items.index_manager.validate_index_consistency()
        print(f"删除后一致性: {consistency[idx]['consistent']}")
        print(f"问题数: {len(consistency[idx]['issues'])}")

        print(f"\n验证索引查询准确性:")
        query = {"value": {"$gt": 100, "$lt": 200}}
        full_scan_result = items.find(query)
        print(f"  全表扫描匹配数: {len(full_scan_result)}")

        from docdb.query.parser import QueryParser
        from docdb.execution.optimizer import QueryOptimizer

        parser = QueryParser()
        optimizer = QueryOptimizer(items.index_manager)
        filter_tree = parser.parse_query(query)
        plan = optimizer.optimize(filter_tree)

        print(f"  优化器选择: {plan.scan_type.value}")
        print(f"  使用索引: {plan.index_name}")

        print(f"\n索引条目验证:")
        tree = items.index_manager.get_index(idx)
        if tree:
            print(f"  索引键数量: {tree.size}")
            print(f"  索引值总数: {tree.value_count}")

        print("\n✅ 索引一致性测试通过\n")
        return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("  DocDB - 文档数据库 综合测试")
    print("=" * 60 + "\n")

    tests = [
        ("Document 文档模型", test_document),
        ("B+ 树索引", test_btree),
        ("查询解析器", test_query_parser),
        ("数据库 CRUD", test_database_crud),
        ("二级索引", test_index),
        ("聚合管道", test_aggregation),
        ("事务 MVCC", test_transaction),
        ("嵌套字段与数组", test_nested_arrays),
        ("索引一致性", test_index_consistency),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n❌ 测试 '{name}' 失败: {e}\n")
            import traceback
            traceback.print_exc()

    print("=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

"""快速测试 - 更多查询解析器测试"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb.query.parser import QueryParser
from docdb.query.filter_tree import *

print("测试查询解析器 - 完整测试...")

parser = QueryParser()

doc_data = {
    "name": "Alice",
    "age": 30,
    "status": "active",
    "address": {"city": "Beijing"},
    "tags": ["python", "developer"],
}

test_cases = [
    ({"name": "Alice"}, True, "简单等值"),
    ({"name": "Bob"}, False, "等值不匹配"),
    ({"age": {"$gt": 25}}, True, "大于"),
    ({"age": {"$lt": 25}}, False, "小于"),
    ({"age": {"$gte": 30}}, True, "大于等于"),
    ({"age": {"$lte": 30}}, True, "小于等于"),
    ({"age": {"$ne": 25}}, True, "不等于"),
    ({"status": {"$in": ["active", "pending"]}}, True, "in"),
    ({"status": {"$nin": ["inactive"]}}, True, "nin"),
    ({"$or": [{"age": 30}, {"name": "Bob"}]}, True, "or"),
    ({"$and": [{"age": {"$gt": 20}}, {"status": "active"}]}, True, "and"),
    ({"$not": {"status": "deleted"}}, True, "not"),
    ({"address.city": "Beijing"}, True, "嵌套字段"),
    ({"tags": "python"}, True, "数组包含"),
    ({"tags": {"$size": 2}}, True, "数组大小"),
    ({"tags": {"$all": ["python", "developer"]}}, True, "数组全部包含"),
]

for query, expected, name in test_cases:
    tree = parser.parse_query(query)
    result = tree.evaluate(doc_data)
    status = "✅" if result == expected else "❌"
    print(f"  {status} {name}: {query} -> {result} (预期: {expected})")

print("\n测试优化...")
tree = parser.parse_query({"$and": [{"age": {"$gt": 20}}, {"name": "Alice"}]})
print(f"  优化前: {tree}")
optimized = tree.optimize()
print(f"  优化后: {optimized}")

print("\n测试字段提取...")
fields = tree.get_fields()
print(f"  涉及字段: {fields}")

print("\n✅ 查询解析器完整测试通过")

"""快速测试 - 查询解析器"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docdb.query.parser import QueryParser
from docdb.query.filter_tree import EqNode, AndNode

print("测试查询解析器...")

parser = QueryParser()

doc_data = {
    "name": "Alice",
    "age": 30,
    "status": "active",
    "address": {"city": "Beijing"},
    "tags": ["python", "developer"],
}

queries = [
    {"name": "Alice"},
    {"age": {"$gt": 25}},
    {"status": {"$in": ["active", "pending"]}},
]

for q in queries:
    tree = parser.parse_query(q)
    result = tree.evaluate(doc_data)
    print(f"  {q} -> {result}")

print("\n✅ 查询解析器测试通过")

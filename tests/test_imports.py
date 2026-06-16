"""逐步导入测试"""

import sys
import os

print("Step 1: 基础导入...")
import json
import tempfile
import shutil
print("  OK")

print("Step 2: 导入 Document...")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from docdb.core.document import Document
print("  OK")

print("Step 3: 导入 WAL...")
from docdb.storage.wal import WAL
print("  OK")

print("Step 4: 导入 DocumentStore...")
from docdb.storage.document_store import DocumentStore
print("  OK")

print("\n✅ 所有模块导入成功")

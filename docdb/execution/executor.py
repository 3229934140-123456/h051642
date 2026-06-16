"""
QueryExecutor - 查询执行器

根据查询计划执行查询，返回结果。

执行流程:
1. 根据扫描类型获取候选文档 ID
   - FULL_SCAN: 遍历所有文档
   - INDEX_SCAN: 通过索引查找
   - INDEX_RANGE_SCAN: 通过索引范围查找

2. 应用过滤条件
   - 对候选文档应用完整的过滤条件树
   - 索引条件只是部分过滤，可能还需要其他条件

3. 应用投影
   - 只保留指定字段

4. 排序
   - 如果可以利用索引排序则直接返回
   - 否则在内存中排序

5. 分页
   - skip 和 limit
"""

import copy
from typing import List, Dict, Any, Optional, Tuple, Set

from ..core.document import Document
from ..storage.document_store import DocumentStore
from ..index.index_manager import IndexManager
from ..query.filter_tree import (
    FilterNode,
    AndNode,
    OrNode,
    NotNode,
    ComparisonNode,
    EqNode,
    GtNode,
    GteNode,
    LtNode,
    LteNode,
    InNode,
    ConstantNode,
)
from .optimizer import QueryPlan, ScanType


class QueryExecutor:
    """
    查询执行器
    
    负责执行查询计划，返回匹配的文档
    """

    def __init__(self, doc_store: DocumentStore, index_manager: IndexManager):
        """
        初始化执行器
        
        Args:
            doc_store: 文档存储
            index_manager: 索引管理器
        """
        self._doc_store = doc_store
        self._index_manager = index_manager

    def execute(self, plan: QueryPlan) -> List[Document]:
        """
        执行查询计划
        
        Args:
            plan: 查询计划
            
        Returns:
            匹配的文档列表
        """
        if plan.scan_type == ScanType.FULL_SCAN:
            docs = self._execute_full_scan(plan)
        elif plan.scan_type in (ScanType.INDEX_SCAN, ScanType.INDEX_RANGE_SCAN):
            docs = self._execute_index_scan(plan)
        elif plan.scan_type == ScanType.COVERING_INDEX:
            docs = self._execute_covering_index(plan)
        else:
            docs = self._execute_full_scan(plan)

        return docs

    def _execute_full_scan(self, plan: QueryPlan) -> List[Document]:
        """
        执行全表扫描
        
        流程:
        1. 遍历所有文档
        2. 应用过滤条件
        3. 排序
        4. 应用投影
        """
        results = []

        for doc in self._doc_store.iterate():
            if plan.filter_tree and not plan.filter_tree.evaluate(doc):
                continue
            results.append(doc)

        if plan.sort:
            results = self._apply_sort(results, plan.sort)

        if plan.projection:
            results = [self._apply_projection(doc, plan.projection) for doc in results]

        return results

    def _execute_index_scan(self, plan: QueryPlan) -> List[Document]:
        """
        执行索引扫描
        
        流程:
        1. 通过索引获取候选文档 ID
        2. 获取文档
        3. 应用完整过滤条件（索引可能只覆盖部分条件）
        4. 排序
        5. 应用投影
        """
        if not plan.index_name:
            return self._execute_full_scan(plan)

        candidate_ids = self._get_index_candidates(plan)

        if not candidate_ids:
            return []

        results = []
        seen = set()

        for doc_id in candidate_ids:
            if doc_id in seen:
                continue
            seen.add(doc_id)

            doc = self._doc_store.get(doc_id)
            if doc is None:
                continue

            if plan.filter_tree and not plan.filter_tree.evaluate(doc):
                continue

            results.append(doc)

        if plan.sort and not plan.can_use_index_for_sort:
            results = self._apply_sort(results, plan.sort)

        if plan.projection:
            results = [self._apply_projection(doc, plan.projection) for doc in results]

        return results

    def _execute_covering_index(self, plan: QueryPlan) -> List[Document]:
        """
        执行覆盖索引查询
        
        覆盖索引: 查询所需的所有字段都在索引中，无需回表读文档
        这里简化实现，暂时和索引扫描一样
        """
        return self._execute_index_scan(plan)

    def _get_index_candidates(self, plan: QueryPlan) -> List[str]:
        """
        通过索引获取候选文档 ID
        
        Args:
            plan: 查询计划
            
        Returns:
            候选文档 ID 列表
        """
        index_name = plan.index_name
        if not index_name:
            return []

        if plan.scan_type == ScanType.INDEX_SCAN:
            return self._get_index_equality_candidates(plan, index_name)
        elif plan.scan_type == ScanType.INDEX_RANGE_SCAN:
            return self._get_index_range_candidates(plan, index_name)
        else:
            return []

    def _get_index_equality_candidates(
        self, plan: QueryPlan, index_name: str
    ) -> List[str]:
        """
        获取等值查询的索引候选
        
        从过滤树中提取等值条件，通过索引查找
        """
        field = plan.index_field
        if not field:
            return []

        equality_values = self._extract_equality_values(
            plan.filter_tree, field
        )

        if not equality_values:
            return []

        all_ids = []
        for value in equality_values:
            doc_ids = self._index_manager.find_by_index(index_name, value)
            all_ids.extend(doc_ids)

        return all_ids

    def _extract_equality_values(
        self, filter_tree: FilterNode, field: str
    ) -> List[Any]:
        """
        从过滤树中提取字段的等值查询值
        """
        values = []

        def _extract(node: FilterNode):
            if isinstance(node, EqNode) and node.field == field:
                values.append(node.value)
            elif isinstance(node, InNode) and node.field == field:
                values.extend(node.values)
            elif isinstance(node, AndNode):
                for child in node.children:
                    _extract(child)
            elif isinstance(node, OrNode):
                for child in node.children:
                    _extract(child)

        _extract(filter_tree)
        return values

    def _get_index_range_candidates(
        self, plan: QueryPlan, index_name: str
    ) -> List[str]:
        """
        获取范围查询的索引候选
        """
        if not plan.index_range:
            return []

        start, end, include_start, include_end = plan.index_range

        results = self._index_manager.range_by_index(
            index_name, start, end, include_start, include_end
        )

        all_ids = []
        for _, doc_ids in results:
            all_ids.extend(doc_ids)

        return all_ids

    def _apply_sort(
        self, docs: List[Document], sort_rules: List[Tuple[str, int]]
    ) -> List[Document]:
        """
        对文档列表排序
        
        Args:
            docs: 文档列表
            sort_rules: 排序规则 [(field, direction), ...]
            
        Returns:
            排序后的文档列表
        """
        if not sort_rules:
            return docs

        def sort_key(doc: Document):
            keys = []
            for field, direction in sort_rules:
                value = doc.get(field)
                if value is None:
                    value = float("-inf") if direction > 0 else float("inf")
                if direction < 0:
                    if isinstance(value, (int, float)):
                        value = -value
                    elif isinstance(value, str):
                        value = "".join(chr(0xFFFF - ord(c)) for c in value)
                keys.append(value)
            return tuple(keys)

        try:
            return sorted(docs, key=sort_key)
        except TypeError:
            return docs

    def _apply_projection(
        self, doc: Document, projection: Dict[str, Any]
    ) -> Document:
        """
        应用投影，只保留指定字段
        
        Args:
            doc: 原文档
            projection: 投影规则 {"type": "include"/"exclude", "fields": [...]}
            
        Returns:
            投影后的文档（标记为投影模式，to_dict() 只返回投影后的字段）
        """
        proj_type = projection.get("type", "include")
        fields = projection.get("fields", [])

        if proj_type == "include":
            new_data: Dict[str, Any] = {}
            for field in fields:
                value = doc.get(field)
                if value is not None or field == "_id":
                    self._set_nested_field(new_data, field, value)
        else:
            new_data = doc.to_dict(include_system_fields=True)
            for field in fields:
                self._delete_nested_field(new_data, field)

        return Document(new_data, is_projection=True)

    def _set_nested_field(
        self, data: Dict[str, Any], field: str, value: Any
    ) -> None:
        """设置嵌套字段"""
        parts = field.split(".")
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def _delete_nested_field(self, data: Dict[str, Any], field: str) -> None:
        """删除嵌套字段"""
        parts = field.split(".")
        current = data
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return
        if isinstance(current, dict) and parts[-1] in current:
            del current[parts[-1]]

    def count(self, plan: QueryPlan) -> int:
        """
        统计匹配的文档数量
        
        Args:
            plan: 查询计划
            
        Returns:
            匹配的文档数量
        """
        docs = self.execute(plan)
        return len(docs)

    def distinct(self, plan: QueryPlan, field: str) -> List[Any]:
        """
        获取字段的去重值
        
        Args:
            plan: 查询计划
            field: 字段名
            
        Returns:
            去重的值列表
        """
        docs = self.execute(plan)
        values = set()
        for doc in docs:
            val = doc.get(field)
            if val is not None:
                if isinstance(val, list):
                    values.update(val)
                else:
                    values.add(val)
        return list(values)

"""
QueryOptimizer - 查询优化器

查询优化器负责:
- 分析查询条件
- 评估可用索引
- 选择最优的执行计划（索引扫描 vs 全表扫描）
- 生成查询计划

索引选择策略:
1. 对于等值查询 ($eq, $in)，优先使用索引
2. 对于范围查询 ($gt, $lt, $gte, $lte)，使用 B+ 树索引
3. 计算选择性: 预估扫描行数，选择代价最小的方案
4. 如果索引选择性低（如返回大部分文档），全表扫描可能更快
5. 多条件查询时，选择最具选择性的索引

代价估算:
- 全表扫描代价 = 文档总数 * 单文档读取代价
- 索引扫描代价 = 匹配文档数 * (索引读取 + 文档读取)
- 排序代价: 如果索引有序，可以避免排序
"""

from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

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
    NeNode,
    InNode,
    NinNode,
    ExistsNode,
    TypeNode,
    RegexNode,
    AllNode,
    SizeNode,
    ElemMatchNode,
    ConstantNode,
)
from ..index.index_manager import IndexManager


class ScanType(Enum):
    """扫描类型"""
    FULL_SCAN = "full_scan"           # 全表扫描
    INDEX_SCAN = "index_scan"         # 索引扫描
    INDEX_RANGE_SCAN = "index_range"  # 索引范围扫描
    COVERING_INDEX = "covering_index" # 覆盖索引（无需回表）


class QueryPlan:
    """
    查询计划
    
    描述查询的执行方式
    """

    def __init__(self):
        self.scan_type: ScanType = ScanType.FULL_SCAN
        self.index_name: Optional[str] = None
        self.index_field: Optional[str] = None
        self.filter_tree: Optional[FilterNode] = None
        self.projection: Optional[Dict[str, Any]] = None
        self.sort: Optional[List[Tuple[str, int]]] = None
        self.estimated_cost: float = 0.0
        self.estimated_docs: int = 0
        self.can_use_index_for_sort: bool = False
        self.index_range: Optional[Tuple[Any, Any, bool, bool]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_type": self.scan_type.value,
            "index_name": self.index_name,
            "index_field": self.index_field,
            "estimated_cost": self.estimated_cost,
            "estimated_docs": self.estimated_docs,
            "can_use_index_for_sort": self.can_use_index_for_sort,
            "has_projection": self.projection is not None,
            "has_sort": self.sort is not None,
        }

    def __repr__(self) -> str:
        return (
            f"QueryPlan(scan={self.scan_type.value}, "
            f"index={self.index_name}, "
            f"cost={self.estimated_cost:.2f}, "
            f"docs~={self.estimated_docs})"
        )


class QueryOptimizer:
    """
    查询优化器
    
    根据查询条件和索引信息，生成最优查询计划
    """

    FULL_SCAN_COST_PER_DOC = 1.0
    INDEX_LOOKUP_COST = 0.5
    DOC_FETCH_COST = 0.8
    SORT_COST_PER_DOC = 2.0

    def __init__(self, index_manager: IndexManager):
        """
        初始化优化器
        
        Args:
            index_manager: 索引管理器
        """
        self._index_manager = index_manager

    def optimize(
        self,
        filter_tree: FilterNode,
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, int]] = None,
    ) -> QueryPlan:
        """
        生成最优查询计划
        
        Args:
            filter_tree: 过滤条件树
            projection: 投影
            sort: 排序
            
        Returns:
            最优查询计划
        """
        plans = self._generate_plans(filter_tree, projection, sort)
        best_plan = min(plans, key=lambda p: p.estimated_cost)
        best_plan.filter_tree = filter_tree
        best_plan.projection = projection
        best_plan.sort = self._parse_sort(sort)

        return best_plan

    def _parse_sort(
        self, sort: Optional[Dict[str, int]]
    ) -> Optional[List[Tuple[str, int]]]:
        """解析排序规则"""
        if not sort:
            return None
        return [(field, direction) for field, direction in sort.items()]

    def _generate_plans(
        self,
        filter_tree: FilterNode,
        projection: Optional[Dict[str, Any]],
        sort: Optional[Dict[str, int]],
    ) -> List[QueryPlan]:
        """
        生成所有可能的查询计划
        
        包括:
        - 全表扫描计划
        - 每个可用索引的索引扫描计划
        """
        plans = []

        full_scan_plan = self._generate_full_scan_plan(filter_tree, sort)
        plans.append(full_scan_plan)

        index_plans = self._generate_index_plans(filter_tree, projection, sort)
        plans.extend(index_plans)

        return plans

    def _generate_full_scan_plan(
        self, filter_tree: FilterNode, sort: Optional[Dict[str, int]]
    ) -> QueryPlan:
        """生成全表扫描计划"""
        plan = QueryPlan()
        plan.scan_type = ScanType.FULL_SCAN

        total_docs = self._index_manager._doc_store.count()
        plan.estimated_docs = total_docs

        scan_cost = total_docs * self.FULL_SCAN_COST_PER_DOC

        if sort:
            sort_cost = total_docs * self.SORT_COST_PER_DOC
            plan.estimated_cost = scan_cost + sort_cost
        else:
            plan.estimated_cost = scan_cost

        return plan

    def _generate_index_plans(
        self,
        filter_tree: FilterNode,
        projection: Optional[Dict[str, Any]],
        sort: Optional[Dict[str, int]],
    ) -> List[QueryPlan]:
        """
        为每个可用索引生成索引扫描计划
        """
        plans = []
        usable_indexes = self._find_usable_indexes(filter_tree)

        for idx_name, idx_info in usable_indexes:
            plan = self._generate_index_plan(
                filter_tree, idx_name, idx_info, projection, sort
            )
            if plan:
                plans.append(plan)

        return plans

    def _find_usable_indexes(
        self, filter_tree: FilterNode
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        找出查询可以使用的索引
        
        遍历过滤树，找出所有比较节点，
        检查其字段上是否有索引
        """
        indexes_info = self._index_manager.list_indexes()
        index_fields = {idx["field"]: idx for idx in indexes_info}

        usable = []
        fields = filter_tree.get_fields()

        for field in fields:
            if field in index_fields:
                idx = index_fields[field]
                usable.append((idx["name"], idx))

        return usable

    def _generate_index_plan(
        self,
        filter_tree: FilterNode,
        index_name: str,
        index_info: Dict[str, Any],
        projection: Optional[Dict[str, Any]],
        sort: Optional[Dict[str, int]],
    ) -> Optional[QueryPlan]:
        """
        为指定索引生成查询计划
        
        估算索引扫描的代价
        """
        plan = QueryPlan()
        plan.index_name = index_name
        plan.index_field = index_info["field"]

        field = index_info["field"]
        conditions = self._extract_field_conditions(filter_tree, field)

        if not conditions:
            return None

        is_equality = any(isinstance(c, (EqNode, InNode)) for c in conditions)
        is_range = any(
            isinstance(c, (GtNode, GteNode, LtNode, LteNode))
            for c in conditions
        )

        if is_equality:
            plan.scan_type = ScanType.INDEX_SCAN
        elif is_range:
            plan.scan_type = ScanType.INDEX_RANGE_SCAN
            plan.index_range = self._extract_index_range(conditions)
        else:
            return None

        estimated_matches = self._estimate_matches(
            field, conditions, is_equality, is_range
        )
        plan.estimated_docs = estimated_matches

        index_lookup_cost = estimated_matches * self.INDEX_LOOKUP_COST
        doc_fetch_cost = estimated_matches * self.DOC_FETCH_COST

        can_use_index_for_sort = False
        sort_cost = 0

        if sort:
            sort_field = list(sort.keys())[0]
            if sort_field == field and is_range == False:
                can_use_index_for_sort = True
                plan.can_use_index_for_sort = True
            else:
                sort_cost = estimated_matches * self.SORT_COST_PER_DOC

        plan.estimated_cost = index_lookup_cost + doc_fetch_cost + sort_cost

        if plan.estimated_docs == 0:
            plan.estimated_cost = 0.1

        return plan

    def _extract_field_conditions(
        self, filter_tree: FilterNode, field: str
    ) -> List[ComparisonNode]:
        """
        从过滤树中提取指定字段的所有比较条件
        """
        conditions = []
        self._extract_conditions_recursive(filter_tree, field, conditions)
        return conditions

    def _extract_conditions_recursive(
        self, node: FilterNode, field: str, conditions: List[ComparisonNode]
    ) -> None:
        """递归提取字段条件"""
        if isinstance(node, ComparisonNode) and node.field == field:
            conditions.append(node)
        elif isinstance(node, AndNode):
            for child in node.children:
                self._extract_conditions_recursive(child, field, conditions)
        elif isinstance(node, OrNode):
            for child in node.children:
                self._extract_conditions_recursive(child, field, conditions)
        elif isinstance(node, NotNode):
            self._extract_conditions_recursive(node.child, field, conditions)
        elif isinstance(node, ElemMatchNode):
            self._extract_conditions_recursive(node.conditions, field, conditions)

    def _extract_index_range(
        self, conditions: List[ComparisonNode]
    ) -> Optional[Tuple[Any, Any, bool, bool]]:
        """
        从条件中提取索引范围
        
        Returns:
            (start, end, include_start, include_end) 或 None
        """
        start = None
        end = None
        include_start = False
        include_end = False

        for cond in conditions:
            if isinstance(cond, GtNode):
                start = cond.value
                include_start = False
            elif isinstance(cond, GteNode):
                start = cond.value
                include_start = True
            elif isinstance(cond, LtNode):
                end = cond.value
                include_end = False
            elif isinstance(cond, LteNode):
                end = cond.value
                include_end = True

        if start is None and end is None:
            return None

        return (start, end, include_start, include_end)

    def _estimate_matches(
        self,
        field: str,
        conditions: List[ComparisonNode],
        is_equality: bool,
        is_range: bool,
    ) -> int:
        """
        估算匹配的文档数量
        
        简化估算:
        - 等值查询: 假设 1-10 个结果
        - $in 查询: 假设 in 列表大小 * 等值结果
        - 范围查询: 假设返回 10%-50% 的文档
        - 没有统计信息时使用保守估计
        """
        total_docs = self._index_manager._doc_store.count()

        if total_docs == 0:
            return 0

        if is_equality:
            in_conditions = [c for c in conditions if isinstance(c, InNode)]
            if in_conditions:
                in_size = max(len(c.values) for c in in_conditions)
                return max(1, min(total_docs, in_size * 5))
            return max(1, min(total_docs // 100, 10))

        if is_range:
            return max(1, total_docs // 4)

        return total_docs

    def explain(
        self,
        filter_tree: FilterNode,
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        解释查询计划
        
        Returns:
            详细的查询计划解释
        """
        plans = self._generate_plans(filter_tree, projection, sort)
        best_plan = self.optimize(filter_tree, projection, sort)

        return {
            "all_plans": [p.to_dict() for p in plans],
            "winning_plan": best_plan.to_dict(),
            "indexes_considered": [p.index_name for p in plans if p.index_name],
        }

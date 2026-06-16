"""
QueryParser - 查询解析器

将用户提供的查询字典解析为过滤条件树（FilterNode）。

查询语法（类 MongoDB 风格）:
{
    "field": value,                          // 等于
    "field": { "$gt": value },               // 大于
    "field": { "$gte": value },              // 大于等于
    "field": { "$lt": value },               // 小于
    "field": { "$lte": value },              // 小于等于
    "field": { "$ne": value },               // 不等于
    "field": { "$in": [v1, v2] },            // 包含
    "field": { "$nin": [v1, v2] },           // 不包含
    "field": { "$exists": true },            // 存在性
    "field": { "$type": "string" },          // 类型检查
    "field": { "$regex": "pattern" },        // 正则匹配
    "field": { "$all": [v1, v2] },           // 数组全部包含
    "field": { "$size": 3 },                 // 数组大小
    "field": { "$elemMatch": { ... } },      // 数组元素匹配
    "$and": [ {...}, {...} ],                // 逻辑与
    "$or": [ {...}, {...} ],                 // 逻辑或
    "$not": { ... },                         // 逻辑非
}

嵌套字段: "nested.field.subfield"
数组字段: "tags" 或 "items.0.name"
"""

import re
from typing import Dict, Any, List, Optional

from .filter_tree import (
    FilterNode,
    AndNode,
    OrNode,
    NotNode,
    ConstantNode,
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
)


class QueryParser:
    """
    查询解析器
    
    将查询字典解析为过滤条件树
    """

    COMPARISON_OPERATORS = {
        "$gt": GtNode,
        "$gte": GteNode,
        "$lt": LtNode,
        "$lte": LteNode,
        "$ne": NeNode,
        "$eq": EqNode,
    }

    LOGICAL_OPERATORS = {"$and", "$or", "$not", "$nor"}

    def __init__(self):
        pass

    def parse_query(self, query: Optional[Dict[str, Any]]) -> FilterNode:
        """
        解析查询字典
        
        Args:
            query: 查询条件字典
            
        Returns:
            过滤条件树的根节点
        """
        if not query:
            return ConstantNode(True)

        if not isinstance(query, dict):
            raise ValueError(f"Query must be a dictionary, got {type(query)}")

        nodes = self._parse_query_dict(query)

        if len(nodes) == 0:
            return ConstantNode(True)
        if len(nodes) == 1:
            return nodes[0].optimize()

        return AndNode(nodes).optimize()

    def _parse_query_dict(self, query: Dict[str, Any]) -> List[FilterNode]:
        """
        解析查询字典，返回节点列表
        
        顶层字段默认为 AND 关系
        """
        nodes = []

        for key, value in query.items():
            if key in self.LOGICAL_OPERATORS:
                nodes.append(self._parse_logical_operator(key, value))
            elif key.startswith("$"):
                raise ValueError(f"Unknown operator: {key}")
            else:
                nodes.extend(self._parse_field_condition(key, value))

        return nodes

    def _parse_logical_operator(
        self, operator: str, value: Any
    ) -> FilterNode:
        """
        解析逻辑操作符
        
        Args:
            operator: 操作符名称
            value: 操作符的值
            
        Returns:
            逻辑节点
        """
        if operator == "$and":
            if not isinstance(value, list):
                raise ValueError("$and expects a list")
            children = []
            for sub_query in value:
                child = self.parse_query(sub_query)
                children.append(child)
            return AndNode(children)

        elif operator == "$or":
            if not isinstance(value, list):
                raise ValueError("$or expects a list")
            children = []
            for sub_query in value:
                child = self.parse_query(sub_query)
                children.append(child)
            return OrNode(children)

        elif operator == "$not":
            if not isinstance(value, dict):
                raise ValueError("$not expects a dictionary")
            child = self.parse_query(value)
            return NotNode(child)

        elif operator == "$nor":
            if not isinstance(value, list):
                raise ValueError("$nor expects a list")
            children = []
            for sub_query in value:
                child = self.parse_query(sub_query)
                children.append(NotNode(child))
            return AndNode(children)

        else:
            raise ValueError(f"Unknown logical operator: {operator}")

    def _parse_field_condition(
        self, field: str, value: Any
    ) -> List[FilterNode]:
        """
        解析字段条件
        
        如果 value 是字典，可能包含多个操作符
        如果 value 是普通值，就是等于比较
        
        Args:
            field: 字段路径
            value: 条件值
            
        Returns:
            条件节点列表
        """
        if isinstance(value, dict) and self._is_operator_dict(value):
            return self._parse_operator_dict(field, value)
        else:
            return [EqNode(field, value)]

    def _is_operator_dict(self, value: Dict[str, Any]) -> bool:
        """判断字典是否全是操作符"""
        if not isinstance(value, dict):
            return False

        for key in value.keys():
            if not key.startswith("$"):
                return False
            if key not in self.COMPARISON_OPERATORS and key not in {
                "$in",
                "$nin",
                "$exists",
                "$type",
                "$regex",
                "$options",
                "$all",
                "$size",
                "$elemMatch",
                "$eq",
                "$ne",
            }:
                return False

        return len(value) > 0

    def _parse_operator_dict(
        self, field: str, operators: Dict[str, Any]
    ) -> List[FilterNode]:
        """
        解析字段上的操作符字典
        
        例如: {"$gt": 10, "$lt": 20}
        """
        nodes = []

        regex_pattern = None
        regex_options = ""

        for op, value in operators.items():
            if op in self.COMPARISON_OPERATORS:
                node_class = self.COMPARISON_OPERATORS[op]
                nodes.append(node_class(field, value))

            elif op == "$in":
                if not isinstance(value, list):
                    raise ValueError("$in expects a list")
                nodes.append(InNode(field, value))

            elif op == "$nin":
                if not isinstance(value, list):
                    raise ValueError("$nin expects a list")
                nodes.append(NinNode(field, value))

            elif op == "$exists":
                nodes.append(ExistsNode(field, bool(value)))

            elif op == "$type":
                nodes.append(TypeNode(field, value))

            elif op == "$regex":
                regex_pattern = value

            elif op == "$options":
                regex_options = value

            elif op == "$all":
                if not isinstance(value, list):
                    raise ValueError("$all expects a list")
                nodes.append(AllNode(field, value))

            elif op == "$size":
                if not isinstance(value, int):
                    raise ValueError("$size expects an integer")
                nodes.append(SizeNode(field, value))

            elif op == "$elemMatch":
                if not isinstance(value, dict):
                    raise ValueError("$elemMatch expects a dictionary")
                sub_tree = self.parse_query(value)
                nodes.append(ElemMatchNode(field, sub_tree))

            elif op == "$eq":
                nodes.append(EqNode(field, value))

            elif op == "$ne":
                nodes.append(NeNode(field, value))

            else:
                raise ValueError(f"Unknown operator: {op}")

        if regex_pattern is not None:
            nodes.append(RegexNode(field, regex_pattern, regex_options))

        return nodes

    def parse_projection(
        self, projection: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        解析投影
        
        Args:
            projection: 投影字典 {field: 1/0}
            
        Returns:
            处理后的投影信息
        """
        if not projection:
            return None

        if not isinstance(projection, dict):
            raise ValueError("Projection must be a dictionary")

        include_fields = set()
        exclude_fields = set()
        id_include = False
        id_set = False

        for field, value in projection.items():
            if field == "_id":
                id_include = bool(value)
                id_set = True
            elif value:
                include_fields.add(field)
            else:
                exclude_fields.add(field)

        if include_fields and exclude_fields:
            raise ValueError(
                "Projection cannot have both include and exclude fields (except _id)"
            )

        if include_fields:
            fields = list(include_fields)
            if id_include:
                fields.append("_id")
            return {"type": "include", "fields": fields, "id_explicit": id_set}
        elif exclude_fields:
            fields = list(exclude_fields)
            if not id_include and id_set:
                fields.append("_id")
            return {"type": "exclude", "fields": fields}
        else:
            if id_set and not id_include:
                return {"type": "exclude", "fields": ["_id"]}
            return None

    def parse_sort(self, sort: Optional[Dict[str, int]]) -> Optional[List[tuple]]:
        """
        解析排序规则
        
        Args:
            sort: 排序字典 {field: 1/-1}
            
        Returns:
            排序规则列表 [(field, direction), ...]
        """
        if not sort:
            return None

        if isinstance(sort, list):
            result = []
            for item in sort:
                if isinstance(item, tuple):
                    field, direction = item
                else:
                    field = item
                    direction = 1
                result.append((field, direction))
            return result

        if isinstance(sort, dict):
            result = []
            for field, direction in sort.items():
                if direction not in (1, -1):
                    raise ValueError("Sort direction must be 1 (asc) or -1 (desc)")
                result.append((field, direction))
            return result

        raise ValueError("Sort must be a dictionary or list")

    def validate_query(self, query: Dict[str, Any]) -> List[str]:
        """
        验证查询语法
        
        Args:
            query: 查询条件
            
        Returns:
            错误列表（空列表表示有效）
        """
        errors = []

        try:
            self.parse_query(query)
        except Exception as e:
            errors.append(str(e))

        return errors

    def explain(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        解释查询，返回查询树结构
        
        Args:
            query: 查询条件
            
        Returns:
            查询解释信息
        """
        tree = self.parse_query(query)
        fields = tree.get_fields()

        return {
            "query": query,
            "fields": list(fields),
            "tree_repr": repr(tree),
            "optimized": tree.optimize().__repr__(),
        }

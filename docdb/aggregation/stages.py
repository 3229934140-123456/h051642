"""
聚合管道阶段

所有聚合管道阶段的实现

支持的阶段:
- $match: 过滤文档
- $project: 投影/重塑文档
- $group: 分组聚合
- $sort: 排序
- $skip: 跳过 N 个文档
- $limit: 限制输出数量
- $unwind: 展开数组字段
- $count: 统计文档数量
- $addFields: 添加字段
"""

import copy
import re
from typing import List, Dict, Any, Optional

from .pipeline import PipelineStage


class MatchStage(PipelineStage):
    """
    $match - 过滤文档
    
    类似 SQL 的 WHERE 子句
    使用查询解析器来过滤文档
    """

    STAGE_NAME = "$match"

    def __init__(self, query: Dict[str, Any]):
        super().__init__(query)
        self._query = query
        self._filter_tree = None

    def _ensure_filter_tree(self):
        """确保过滤树已初始化"""
        if self._filter_tree is None:
            from ..query.parser import QueryParser
            parser = QueryParser()
            self._filter_tree = parser.parse_query(self._query)

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """过滤文档"""
        self._ensure_filter_tree()

        results = []
        for doc in documents:
            if self._filter_tree.evaluate(doc):
                results.append(doc)
        return results


class ProjectStage(PipelineStage):
    """
    $project - 投影/重塑文档
    
    支持:
    - 包含/排除字段
    - 重命名字段
    - 计算字段
    - 嵌套字段投影
    """

    STAGE_NAME = "$project"

    def __init__(self, spec: Dict[str, Any]):
        super().__init__(spec)
        self._spec = spec

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """投影文档"""
        results = []

        include_mode = None
        for field, value in self._spec.items():
            if field == "_id":
                continue
            if isinstance(value, bool) or value in (0, 1):
                include_mode = bool(value)
                break

        if include_mode is None:
            include_mode = True

        for doc in documents:
            if include_mode:
                new_doc = {}
                for field, value in self._spec.items():
                    if field == "_id":
                        if value is False or value == 0:
                            continue
                        if "_id" in doc:
                            new_doc["_id"] = doc["_id"]
                        continue

                    if isinstance(value, (bool, int)) and value:
                        self._copy_field(doc, new_doc, field)
                    elif isinstance(value, str) and value.startswith("$"):
                        field_value = value[1:]
                        self._set_field(new_doc, field, self._get_field(doc, field_value))
                    elif callable(value):
                        self._set_field(new_doc, field, value(doc))
                    elif isinstance(value, dict):
                        computed = self._compute_expression(doc, value)
                        self._set_field(new_doc, field, computed)
                    else:
                        self._set_field(new_doc, field, value)
            else:
                new_doc = copy.deepcopy(doc)
                for field, value in self._spec.items():
                    if isinstance(value, (bool, int)) and not value:
                        self._delete_field(new_doc, field)

            results.append(new_doc)

        return results

    def _get_field(self, doc: Dict[str, Any], path: str) -> Any:
        """按路径获取字段值"""
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _set_field(self, doc: Dict[str, Any], path: str, value: Any) -> None:
        """按路径设置字段值"""
        parts = path.split(".")
        current = doc
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def _copy_field(
        self, source: Dict[str, Any], target: Dict[str, Any], path: str
    ) -> None:
        """复制字段"""
        value = self._get_field(source, path)
        if value is not None:
            self._set_field(target, path, value)

    def _delete_field(self, doc: Dict[str, Any], path: str) -> None:
        """删除字段"""
        parts = path.split(".")
        current = doc
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return
        if isinstance(current, dict) and parts[-1] in current:
            del current[parts[-1]]

    def _compute_expression(self, doc: Dict[str, Any], expr: Dict[str, Any]) -> Any:
        """计算表达式"""
        for op, value in expr.items():
            if op == "$add":
                return self._resolve_value(doc, value[0]) + self._resolve_value(doc, value[1])
            elif op == "$subtract":
                return self._resolve_value(doc, value[0]) - self._resolve_value(doc, value[1])
            elif op == "$multiply":
                return self._resolve_value(doc, value[0]) * self._resolve_value(doc, value[1])
            elif op == "$divide":
                return self._resolve_value(doc, value[0]) / self._resolve_value(doc, value[1])
            elif op == "$concat":
                return "".join(str(self._resolve_value(doc, v)) for v in value)
            elif op == "$toUpper":
                return str(self._resolve_value(doc, value)).upper()
            elif op == "$toLower":
                return str(self._resolve_value(doc, value)).lower()
        return None

    def _resolve_value(self, doc: Dict[str, Any], value: Any) -> Any:
        """解析值（如果是字段引用则取值）"""
        if isinstance(value, str) and value.startswith("$"):
            return self._get_field(doc, value[1:])
        return value


class GroupStage(PipelineStage):
    """
    $group - 分组聚合
    
    支持的累加器:
    - $sum: 求和
    - $avg: 平均值
    - $min: 最小值
    - $max: 最大值
    - $first: 第一个值
    - $last: 最后一个值
    - $push: 收集为数组
    - $addToSet: 收集为集合（去重）
    - $count: 计数
    """

    STAGE_NAME = "$group"

    def __init__(self, spec: Dict[str, Any]):
        super().__init__(spec)
        self._spec = spec
        self._id_expr = spec.get("_id")
        self._accumulators = {
            k: v for k, v in spec.items() if k != "_id"
        }

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """分组聚合"""
        groups: Dict[Any, List[Dict[str, Any]]] = {}

        for doc in documents:
            group_key = self._get_group_key(doc)

            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(doc)

        results = []
        for group_key, group_docs in groups.items():
            result = {"_id": group_key}

            for field, acc_spec in self._accumulators.items():
                result[field] = self._compute_accumulator(group_docs, acc_spec)

            results.append(result)

        return results

    def _get_group_key(self, doc: Dict[str, Any]) -> Any:
        """获取分组键"""
        if self._id_expr is None:
            return None

        if isinstance(self._id_expr, str) and self._id_expr.startswith("$"):
            field_path = self._id_expr[1:]
            return self._get_field(doc, field_path)

        if isinstance(self._id_expr, dict):
            result = {}
            for k, v in self._id_expr.items():
                if isinstance(v, str) and v.startswith("$"):
                    result[k] = self._get_field(doc, v[1:])
                else:
                    result[k] = v
            return self._make_hashable(result)

        return self._id_expr

    def _make_hashable(self, obj: Any) -> Any:
        """转换为可哈希的类型"""
        if isinstance(obj, dict):
            return tuple(sorted((k, self._make_hashable(v)) for k, v in obj.items()))
        if isinstance(obj, list):
            return tuple(self._make_hashable(v) for v in obj)
        return obj

    def _get_field(self, doc: Dict[str, Any], path: str) -> Any:
        """获取字段值"""
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _compute_accumulator(
        self, docs: List[Dict[str, Any]], acc_spec: Any
    ) -> Any:
        """计算累加器"""
        if isinstance(acc_spec, dict):
            for op, value in acc_spec.items():
                if op == "$sum":
                    return self._acc_sum(docs, value)
                elif op == "$avg":
                    return self._acc_avg(docs, value)
                elif op == "$min":
                    return self._acc_min(docs, value)
                elif op == "$max":
                    return self._acc_max(docs, value)
                elif op == "$first":
                    return self._acc_first(docs, value)
                elif op == "$last":
                    return self._acc_last(docs, value)
                elif op == "$push":
                    return self._acc_push(docs, value)
                elif op == "$addToSet":
                    return self._acc_addToSet(docs, value)
                elif op == "$count":
                    return len(docs)
        elif acc_spec == 1 or acc_spec is True:
            return len(docs)
        return None

    def _resolve_field(self, doc: Dict[str, Any], value: Any) -> Any:
        """解析字段值"""
        if isinstance(value, str) and value.startswith("$"):
            return self._get_field(doc, value[1:])
        return value

    def _acc_sum(self, docs: List[Dict[str, Any]], expr: Any) -> float:
        """求和"""
        if expr == 1:
            return len(docs)
        total = 0
        for doc in docs:
            val = self._resolve_field(doc, expr)
            if isinstance(val, (int, float)):
                total += val
        return total

    def _acc_avg(self, docs: List[Dict[str, Any]], expr: Any) -> float:
        """平均值"""
        total = 0
        count = 0
        for doc in docs:
            val = self._resolve_field(doc, expr)
            if isinstance(val, (int, float)):
                total += val
                count += 1
        return total / count if count > 0 else 0

    def _acc_min(self, docs: List[Dict[str, Any]], expr: Any) -> Any:
        """最小值"""
        values = []
        for doc in docs:
            val = self._resolve_field(doc, expr)
            if val is not None:
                values.append(val)
        return min(values) if values else None

    def _acc_max(self, docs: List[Dict[str, Any]], expr: Any) -> Any:
        """最大值"""
        values = []
        for doc in docs:
            val = self._resolve_field(doc, expr)
            if val is not None:
                values.append(val)
        return max(values) if values else None

    def _acc_first(self, docs: List[Dict[str, Any]], expr: Any) -> Any:
        """第一个值"""
        if not docs:
            return None
        return self._resolve_field(docs[0], expr)

    def _acc_last(self, docs: List[Dict[str, Any]], expr: Any) -> Any:
        """最后一个值"""
        if not docs:
            return None
        return self._resolve_field(docs[-1], expr)

    def _acc_push(self, docs: List[Dict[str, Any]], expr: Any) -> List[Any]:
        """收集为数组"""
        result = []
        for doc in docs:
            result.append(self._resolve_field(doc, expr))
        return result

    def _acc_addToSet(self, docs: List[Dict[str, Any]], expr: Any) -> List[Any]:
        """收集为集合"""
        result = set()
        for doc in docs:
            val = self._resolve_field(doc, expr)
            try:
                result.add(val)
            except TypeError:
                pass
        return list(result)


class SortStage(PipelineStage):
    """
    $sort - 排序
    
    按指定字段排序
    1 = 升序, -1 = 降序
    """

    STAGE_NAME = "$sort"

    def __init__(self, sort_spec: Dict[str, int]):
        super().__init__(sort_spec)
        self._sort_spec = sort_spec

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """排序文档"""
        if not self._sort_spec:
            return documents

        def sort_key(doc: Dict[str, Any]):
            keys = []
            for field, direction in self._sort_spec.items():
                value = self._get_field(doc, field)
                if value is None:
                    value = float("-inf") if direction > 0 else float("inf")
                if direction < 0:
                    value = self._negate_value(value)
                keys.append(value)
            return tuple(keys)

        try:
            return sorted(documents, key=sort_key)
        except TypeError:
            return documents

    def _get_field(self, doc: Dict[str, Any], path: str) -> Any:
        """获取字段值"""
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _negate_value(self, value: Any) -> Any:
        """取反值用于降序排序"""
        if isinstance(value, (int, float)):
            return -value
        if isinstance(value, str):
            return "".join(chr(0xFFFF - ord(c)) for c in value)
        return value


class SkipStage(PipelineStage):
    """
    $skip - 跳过 N 个文档
    """

    STAGE_NAME = "$skip"

    def __init__(self, count: int):
        super().__init__(count)
        self._count = int(count)

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """跳过文档"""
        if self._count <= 0:
            return documents
        return documents[self._count :]


class LimitStage(PipelineStage):
    """
    $limit - 限制输出数量
    """

    STAGE_NAME = "$limit"

    def __init__(self, count: int):
        super().__init__(count)
        self._count = int(count)

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """限制数量"""
        if self._count <= 0:
            return []
        return documents[: self._count]


class UnwindStage(PipelineStage):
    """
    $unwind - 展开数组字段
    
    将数组字段的每个元素展开为独立文档
    """

    STAGE_NAME = "$unwind"

    def __init__(self, field_path: str):
        super().__init__(field_path)
        if isinstance(field_path, str) and field_path.startswith("$"):
            self._field_path = field_path[1:]
        elif isinstance(field_path, dict):
            path = field_path.get("path", "")
            if path.startswith("$"):
                path = path[1:]
            self._field_path = path
        else:
            self._field_path = field_path

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """展开数组"""
        results = []

        for doc in documents:
            arr_value = self._get_field(doc, self._field_path)

            if arr_value is None:
                continue

            if not isinstance(arr_value, list):
                new_doc = copy.deepcopy(doc)
                results.append(new_doc)
                continue

            if len(arr_value) == 0:
                continue

            for item in arr_value:
                new_doc = copy.deepcopy(doc)
                self._set_field(new_doc, self._field_path, item)
                results.append(new_doc)

        return results

    def _get_field(self, doc: Dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _set_field(self, doc: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        current = doc
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value


class CountStage(PipelineStage):
    """
    $count - 统计文档数量
    
    返回一个包含 count 的文档
    """

    STAGE_NAME = "$count"

    def __init__(self, field_name: str):
        super().__init__(field_name)
        self._field_name = field_name

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """统计数量"""
        return [{self._field_name: len(documents)}]


class AddFieldsStage(PipelineStage):
    """
    $addFields - 添加字段
    
    向文档中添加新字段，保留原有字段
    """

    STAGE_NAME = "$addFields"

    def __init__(self, spec: Dict[str, Any]):
        super().__init__(spec)
        self._spec = spec

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """添加字段"""
        results = []

        for doc in documents:
            new_doc = copy.deepcopy(doc)
            for field, value in self._spec.items():
                if isinstance(value, str) and value.startswith("$"):
                    field_value = self._get_field(doc, value[1:])
                    self._set_field(new_doc, field, field_value)
                elif isinstance(value, dict):
                    computed = self._compute_expression(doc, value)
                    self._set_field(new_doc, field, computed)
                else:
                    self._set_field(new_doc, field, value)
            results.append(new_doc)

        return results

    def _get_field(self, doc: Dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _set_field(self, doc: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        current = doc
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def _compute_expression(self, doc: Dict[str, Any], expr: Dict[str, Any]) -> Any:
        for op, value in expr.items():
            if op == "$add":
                return self._resolve_value(doc, value[0]) + self._resolve_value(doc, value[1])
            elif op == "$subtract":
                return self._resolve_value(doc, value[0]) - self._resolve_value(doc, value[1])
            elif op == "$multiply":
                return self._resolve_value(doc, value[0]) * self._resolve_value(doc, value[1])
            elif op == "$divide":
                return self._resolve_value(doc, value[0]) / self._resolve_value(doc, value[1])
        return None

    def _resolve_value(self, doc: Dict[str, Any], value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self._get_field(doc, value[1:])
        return value

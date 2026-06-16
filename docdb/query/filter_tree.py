"""
过滤条件树

查询解析后生成的抽象语法树（AST），表示过滤条件。
树的每个节点是一个过滤条件，可以组合成复杂的查询。

节点类型:
- 逻辑节点: AND, OR, NOT
- 比较节点: =, >, >=, <, <=, !=, IN, NIN
- 存在性节点: EXISTS, TYPE
- 数组节点: ALL, SIZE, ELEM_MATCH
- 正则节点: REGEX

每个节点支持:
- evaluate(doc): 对文档求值，返回是否匹配
- get_fields(): 获取涉及的字段（用于索引选择）
- optimize(): 优化树结构（常量折叠、简化等）
"""

from typing import List, Optional, Any, Set, Callable
import re


class FilterNode:
    """过滤条件树的基类节点"""

    def evaluate(self, doc: Any) -> bool:
        """
        对文档求值
        
        Args:
            doc: 文档对象
            
        Returns:
            是否匹配
        """
        raise NotImplementedError

    def get_fields(self) -> Set[str]:
        """
        获取查询涉及的所有字段
        
        Returns:
            字段路径集合
        """
        raise NotImplementedError

    def optimize(self) -> "FilterNode":
        """
        优化过滤树
        
        Returns:
            优化后的节点
        """
        return self

    def _doc_to_dict(self, doc: Any) -> Dict[str, Any]:
        """将文档转换为字典形式（支持 Document 对象和 dict）"""
        if hasattr(doc, "to_dict"):
            return doc.to_dict(include_system_fields=True)
        if isinstance(doc, dict):
            return doc
        return {}

    def __repr__(self) -> str:
        return self.__class__.__name__


class AndNode(FilterNode):
    """逻辑与节点 - 所有子条件都必须满足"""

    def __init__(self, children: List[FilterNode]):
        self.children = children

    def evaluate(self, doc: Any) -> bool:
        return all(child.evaluate(doc) for child in self.children)

    def get_fields(self) -> Set[str]:
        fields: Set[str] = set()
        for child in self.children:
            fields.update(child.get_fields())
        return fields

    def optimize(self) -> FilterNode:
        optimized = [child.optimize() for child in self.children]

        flat = []
        for child in optimized:
            if isinstance(child, AndNode):
                flat.extend(child.children)
            else:
                flat.append(child)

        constant_true = []
        result = []
        for child in flat:
            if isinstance(child, ConstantNode) and child.value:
                constant_true.append(child)
            elif isinstance(child, ConstantNode) and not child.value:
                return ConstantNode(False)
            else:
                result.append(child)

        if len(result) == 0:
            return ConstantNode(True)
        if len(result) == 1:
            return result[0]

        self.children = result
        return self

    def __repr__(self) -> str:
        return f"AND({', '.join(repr(c) for c in self.children)})"


class OrNode(FilterNode):
    """逻辑或节点 - 任意一个子条件满足即可"""

    def __init__(self, children: List[FilterNode]):
        self.children = children

    def evaluate(self, doc: Any) -> bool:
        return any(child.evaluate(doc) for child in self.children)

    def get_fields(self) -> Set[str]:
        fields: Set[str] = set()
        for child in self.children:
            fields.update(child.get_fields())
        return fields

    def optimize(self) -> FilterNode:
        optimized = [child.optimize() for child in self.children]

        flat = []
        for child in optimized:
            if isinstance(child, OrNode):
                flat.extend(child.children)
            else:
                flat.append(child)

        result = []
        for child in flat:
            if isinstance(child, ConstantNode) and child.value:
                return ConstantNode(True)
            elif isinstance(child, ConstantNode) and not child.value:
                pass
            else:
                result.append(child)

        if len(result) == 0:
            return ConstantNode(False)
        if len(result) == 1:
            return result[0]

        self.children = result
        return self

    def __repr__(self) -> str:
        return f"OR({', '.join(repr(c) for c in self.children)})"


class NotNode(FilterNode):
    """逻辑非节点 - 取反"""

    def __init__(self, child: FilterNode):
        self.child = child

    def evaluate(self, doc: Any) -> bool:
        return not self.child.evaluate(doc)

    def get_fields(self) -> Set[str]:
        return self.child.get_fields()

    def optimize(self) -> FilterNode:
        optimized = self.child.optimize()

        if isinstance(optimized, ConstantNode):
            return ConstantNode(not optimized.value)

        if isinstance(optimized, NotNode):
            return optimized.child

        self.child = optimized
        return self

    def __repr__(self) -> str:
        return f"NOT({repr(self.child)})"


class ConstantNode(FilterNode):
    """常量节点 - 总是返回固定值"""

    def __init__(self, value: bool):
        self.value = value

    def evaluate(self, doc: Any) -> bool:
        return self.value

    def get_fields(self) -> Set[str]:
        return set()

    def __repr__(self) -> str:
        return f"CONST({self.value})"


class ComparisonNode(FilterNode):
    """比较节点基类"""

    def __init__(self, field: str, value: Any):
        self.field = field
        self.value = value

    def _get_field_value(self, doc: Any) -> Any:
        """
        从文档中获取字段值
        
        支持:
        - 嵌套字段: "a.b.c"
        - 数组索引: "arr.0"
        - 数组元素匹配: 数组中任意元素匹配即可
        """
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    if 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return None
                except ValueError:
                    return None
            else:
                return None

        return current

    def _matches_array(self, doc: Any, matcher: Callable[[Any], bool]) -> bool:
        """
        检查数组中是否有元素匹配（用于数组字段查询）
        
        如果字段值是数组，则检查数组中是否有元素匹配条件
        """
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for i, part in enumerate(parts[:-1]):
            if isinstance(current, dict):
                if part not in current:
                    return False
                current = current[part]
            elif isinstance(current, list):
                for item in current:
                    if isinstance(item, dict):
                        sub_field = ".".join(parts[i + 1 :])
                        sub_node = type(self)(sub_field, self.value)
                        if sub_node._matches_value(item, matcher):
                            return True
                return False
            else:
                return False

        last_part = parts[-1]
        if isinstance(current, dict):
            if last_part not in current:
                return False
            field_val = current[last_part]
        else:
            return False

        if isinstance(field_val, list):
            return any(matcher(item) for item in field_val)

        return matcher(field_val)

    def _matches_value(self, doc: Any, matcher: Callable[[Any], bool]) -> bool:
        """对文档值应用匹配器"""
        val = self._get_field_value(doc)
        return matcher(val)

    def evaluate(self, doc: Any) -> bool:
        raise NotImplementedError

    def get_fields(self) -> Set[str]:
        return {self.field}

    def compare(self, a: Any, b: Any) -> bool:
        """比较方法，由子类实现"""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.field}, {self.value})"


class EqNode(ComparisonNode):
    """等于比较"""

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            if val is None:
                return self.value is None
            return val == self.value

        return self._matches_array(doc, matcher)

    def compare(self, a: Any, b: Any) -> bool:
        return a == b


class NeNode(ComparisonNode):
    """不等于比较"""

    def evaluate(self, doc: Any) -> bool:
        val = self._get_field_value(doc)
        if val is None:
            return True
        return val != self.value

    def compare(self, a: Any, b: Any) -> bool:
        return a != b


class GtNode(ComparisonNode):
    """大于比较"""

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            if val is None:
                return False
            try:
                return val > self.value
            except TypeError:
                return False

        return self._matches_array(doc, matcher)

    def compare(self, a: Any, b: Any) -> bool:
        return a > b


class GteNode(ComparisonNode):
    """大于等于比较"""

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            if val is None:
                return False
            try:
                return val >= self.value
            except TypeError:
                return False

        return self._matches_array(doc, matcher)

    def compare(self, a: Any, b: Any) -> bool:
        return a >= b


class LtNode(ComparisonNode):
    """小于比较"""

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            if val is None:
                return False
            try:
                return val < self.value
            except TypeError:
                return False

        return self._matches_array(doc, matcher)

    def compare(self, a: Any, b: Any) -> bool:
        return a < b


class LteNode(ComparisonNode):
    """小于等于比较"""

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            if val is None:
                return False
            try:
                return val <= self.value
            except TypeError:
                return False

        return self._matches_array(doc, matcher)

    def compare(self, a: Any, b: Any) -> bool:
        return a <= b


class InNode(ComparisonNode):
    """包含比较 - 值在列表中"""

    def __init__(self, field: str, values: List[Any]):
        super().__init__(field, values)
        self.values = values

    def evaluate(self, doc: Any) -> bool:
        def matcher(val):
            return val in self.values

        return self._matches_array(doc, matcher)

    def __repr__(self) -> str:
        return f"IN({self.field}, {self.values})"


class NinNode(ComparisonNode):
    """不包含比较 - 值不在列表中"""

    def __init__(self, field: str, values: List[Any]):
        super().__init__(field, values)
        self.values = values

    def evaluate(self, doc: Any) -> bool:
        val = self._get_field_value(doc)
        if val is None:
            return True
        return val not in self.values

    def __repr__(self) -> str:
        return f"NIN({self.field}, {self.values})"


class ExistsNode(FilterNode):
    """存在性检查 - 字段是否存在"""

    def __init__(self, field: str, exists: bool = True):
        self.field = field
        self.exists = exists

    def _field_exists(self, doc: Any) -> bool:
        """检查字段是否存在"""
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return False
                current = current[part]
            elif isinstance(current, list):
                if part.isdigit():
                    idx = int(part)
                    if 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return False
                else:
                    for item in current:
                        if isinstance(item, dict) and part in item:
                            return True
                    return False
            else:
                return False

        return True

    def evaluate(self, doc: Any) -> bool:
        exists = self._field_exists(doc)
        return exists == self.exists

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"EXISTS({self.field}, {self.exists})"


class TypeNode(FilterNode):
    """类型检查 - 字段值的类型"""

    TYPE_MAP = {
        "null": type(None),
        "bool": bool,
        "int": int,
        "float": float,
        "number": (int, float),
        "string": str,
        "array": list,
        "object": dict,
    }

    def __init__(self, field: str, type_name: str):
        self.field = field
        self.type_name = type_name

    def _get_field_type(self, doc: Any) -> Optional[type]:
        """获取字段值的类型"""
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None

        return type(current)

    def evaluate(self, doc: Any) -> bool:
        doc_dict = self._doc_to_dict(doc)
        val_type = self._get_field_type(doc_dict)
        if val_type is None:
            return False

        expected_type = self.TYPE_MAP.get(self.type_name)
        if expected_type is None:
            return False

        if isinstance(expected_type, tuple):
            return issubclass(val_type, expected_type)
        return val_type == expected_type

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"TYPE({self.field}, {self.type_name})"


class RegexNode(FilterNode):
    """正则匹配"""

    def __init__(self, field: str, pattern: str, options: str = ""):
        self.field = field
        self.pattern = pattern
        self.options = options
        flags = 0
        if "i" in options:
            flags |= re.IGNORECASE
        if "m" in options:
            flags |= re.MULTILINE
        if "s" in options:
            flags |= re.DOTALL
        self._regex = re.compile(pattern, flags)

    def _get_field_value(self, doc: Any) -> Any:
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None

        return current

    def evaluate(self, doc: Any) -> bool:
        val = self._get_field_value(doc)
        if not isinstance(val, str):
            return False
        return bool(self._regex.search(val))

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"REGEX({self.field}, /{self.pattern}/{self.options})"


class AllNode(FilterNode):
    """数组全部匹配 - 数组包含所有指定元素"""

    def __init__(self, field: str, values: List[Any]):
        self.field = field
        self.values = values

    def _get_field_value(self, doc: Any) -> Any:
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None

        return current

    def evaluate(self, doc: Any) -> bool:
        val = self._get_field_value(doc)
        if not isinstance(val, list):
            return False

        for v in self.values:
            if v not in val:
                return False
        return True

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"ALL({self.field}, {self.values})"


class SizeNode(FilterNode):
    """数组大小检查"""

    def __init__(self, field: str, size: int):
        self.field = field
        self.size = size

    def _get_field_value(self, doc: Any) -> Any:
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None

        return current

    def evaluate(self, doc: Any) -> bool:
        val = self._get_field_value(doc)
        if not isinstance(val, list):
            return False
        return len(val) == self.size

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"SIZE({self.field}, {self.size})"


class ElemMatchNode(FilterNode):
    """数组元素匹配 - 数组中至少有一个元素满足所有条件"""

    def __init__(self, field: str, conditions: FilterNode):
        self.field = field
        self.conditions = conditions

    def _get_array_value(self, doc: Any) -> Optional[list]:
        doc_dict = self._doc_to_dict(doc)
        parts = self.field.split(".")
        current = doc_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None

        return current if isinstance(current, list) else None

    def evaluate(self, doc: Any) -> bool:
        arr = self._get_array_value(doc)
        if arr is None:
            return False

        for item in arr:
            if isinstance(item, dict):
                if self.conditions.evaluate(item):
                    return True

        return False

    def get_fields(self) -> Set[str]:
        return {self.field}

    def __repr__(self) -> str:
        return f"ELEM_MATCH({self.field}, {self.conditions})"

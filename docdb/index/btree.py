"""
B+ 树实现

B+ 树是一种多路平衡查找树，特点:
- 所有数据都存储在叶子节点
- 叶子节点之间通过链表连接，支持范围查询
- 内部节点只存储索引键和子节点指针
- 支持 O(log n) 的查找、插入、删除

二级索引的键是字段值，值是文档 ID 的列表

本实现是内存版 B+ 树，用于理解索引原理。
"""

import bisect
from typing import List, Tuple, Optional, Any, Iterator


class BPlusTreeNode:
    """B+ 树节点"""

    def __init__(self, order: int, is_leaf: bool = True):
        """
        初始化节点
        
        Args:
            order: 树的阶数（每个节点最多有 order 个子节点）
            is_leaf: 是否为叶子节点
        """
        self.order = order
        self.is_leaf = is_leaf
        self.keys: List[Any] = []  # 键列表
        self.children: List["BPlusTreeNode"] = []  # 内部节点的子节点
        self.values: List[List[str]] = []  # 叶子节点的值（文档 ID 列表）
        self.next: Optional["BPlusTreeNode"] = None  # 叶子节点的下一个节点（链表）
        self.parent: Optional["BPlusTreeNode"] = None  # 父节点

    @property
    def max_keys(self) -> int:
        """最大键数"""
        return self.order - 1

    @property
    def min_keys(self) -> int:
        """最小键数（除根节点外）"""
        return (self.order - 1) // 2

    @property
    def is_full(self) -> bool:
        """节点是否已满"""
        return len(self.keys) >= self.max_keys

    @property
    def is_minimal(self) -> bool:
        """节点是否达到最小键数"""
        return len(self.keys) <= self.min_keys


class BPlusTree:
    """
    B+ 树索引实现
    
    支持:
    - 精确查找 (get)
    - 范围查询 (range_query)
    - 插入 (insert)
    - 删除 (delete)
    - 迭代遍历 (iterate)
    
    键值对: key -> [doc_id1, doc_id2, ...]
    同一个键可以对应多个文档 ID
    """

    def __init__(self, order: int = 32):
        """
        初始化 B+ 树
        
        Args:
            order: 树的阶数
        """
        self.order = order
        self.root = BPlusTreeNode(order, is_leaf=True)
        self._size = 0  # 键的数量（不重复的键）
        self._value_count = 0  # 值的总数（所有文档 ID）

    @property
    def size(self) -> int:
        """返回不重复的键数量"""
        return self._size

    @property
    def value_count(self) -> int:
        """返回所有值的总数"""
        return self._value_count

    def _find_leaf(self, key: Any) -> BPlusTreeNode:
        """
        查找键所在的叶子节点
        
        Args:
            key: 查找的键
            
        Returns:
            叶子节点
        """
        node = self.root
        while not node.is_leaf:
            idx = bisect.bisect_right(node.keys, key)
            node = node.children[idx]
        return node

    def get(self, key: Any) -> List[str]:
        """
        精确查找
        
        Args:
            key: 查找的键
            
        Returns:
            文档 ID 列表
        """
        if self._size == 0:
            return []

        leaf = self._find_leaf(key)
        idx = bisect.bisect_left(leaf.keys, key)
        if idx < len(leaf.keys) and leaf.keys[idx] == key:
            return list(leaf.values[idx])
        return []

    def contains(self, key: Any) -> bool:
        """检查键是否存在"""
        return len(self.get(key)) > 0

    def insert(self, key: Any, value: str) -> None:
        """
        插入键值对
        
        如果键已存在，追加值到列表中
        如果键不存在，插入新键
        
        Args:
            key: 索引键
            value: 文档 ID
        """
        leaf = self._find_leaf(key)
        idx = bisect.bisect_left(leaf.keys, key)

        if idx < len(leaf.keys) and leaf.keys[idx] == key:
            if value not in leaf.values[idx]:
                leaf.values[idx].append(value)
                self._value_count += 1
            return

        leaf.keys.insert(idx, key)
        leaf.values.insert(idx, [value])
        self._size += 1
        self._value_count += 1

        if leaf.is_full:
            self._split_leaf(leaf)

    def _split_leaf(self, leaf: BPlusTreeNode) -> None:
        """
        分裂叶子节点
        
        Args:
            leaf: 要分裂的叶子节点
        """
        mid = len(leaf.keys) // 2

        new_leaf = BPlusTreeNode(self.order, is_leaf=True)
        new_leaf.keys = leaf.keys[mid:]
        new_leaf.values = leaf.values[mid:]
        new_leaf.parent = leaf.parent

        leaf.keys = leaf.keys[:mid]
        leaf.values = leaf.values[:mid]

        new_leaf.next = leaf.next
        leaf.next = new_leaf

        self._insert_into_parent(leaf, new_leaf.keys[0], new_leaf)

    def _insert_into_parent(
        self, left: BPlusTreeNode, key: Any, right: BPlusTreeNode
    ) -> None:
        """
        将分裂后的新节点插入父节点
        
        Args:
            left: 左节点
            key: 分隔键
            right: 右节点
        """
        parent = left.parent

        if parent is None:
            new_root = BPlusTreeNode(self.order, is_leaf=False)
            new_root.keys = [key]
            new_root.children = [left, right]
            left.parent = new_root
            right.parent = new_root
            self.root = new_root
            return

        idx = bisect.bisect_left(parent.keys, key)
        parent.keys.insert(idx, key)
        parent.children.insert(idx + 1, right)
        right.parent = parent

        if parent.is_full:
            self._split_internal(parent)

    def _split_internal(self, node: BPlusTreeNode) -> None:
        """
        分裂内部节点
        
        Args:
            node: 要分裂的内部节点
        """
        mid = len(node.keys) // 2
        mid_key = node.keys[mid]

        new_node = BPlusTreeNode(self.order, is_leaf=False)
        new_node.keys = node.keys[mid + 1 :]
        new_node.children = node.children[mid + 1 :]
        new_node.parent = node.parent

        for child in new_node.children:
            child.parent = new_node

        node.keys = node.keys[:mid]
        node.children = node.children[: mid + 1]

        self._insert_into_parent(node, mid_key, new_node)

    def delete(self, key: Any, value: Optional[str] = None) -> bool:
        """
        删除键值对
        
        Args:
            key: 要删除的键
            value: 要删除的值，如果为 None 则删除整个键
            
        Returns:
            是否成功删除
        """
        if self._size == 0:
            return False

        leaf = self._find_leaf(key)
        idx = bisect.bisect_left(leaf.keys, key)

        if idx >= len(leaf.keys) or leaf.keys[idx] != key:
            return False

        if value is not None:
            if value in leaf.values[idx]:
                leaf.values[idx].remove(value)
                self._value_count -= 1
                if len(leaf.values[idx]) > 0:
                    return True
            else:
                return False

        del leaf.keys[idx]
        del leaf.values[idx]
        self._size -= 1
        if value is None:
            self._value_count -= len(leaf.values[idx]) if idx < len(leaf.values) else 0

        if leaf is not self.root and len(leaf.keys) < leaf.min_keys:
            self._balance_after_delete(leaf)

        return True

    def _balance_after_delete(self, node: BPlusTreeNode) -> None:
        """
        删除后平衡树
        
        策略:
        1. 尝试从左兄弟借
        2. 尝试从右兄弟借
        3. 否则合并
        """
        parent = node.parent
        if parent is None:
            return

        idx = parent.children.index(node)

        left_sibling = parent.children[idx - 1] if idx > 0 else None
        right_sibling = parent.children[idx + 1] if idx < len(parent.children) - 1 else None

        if left_sibling and len(left_sibling.keys) > left_sibling.min_keys:
            self._borrow_from_left(node, left_sibling, parent, idx - 1)
        elif right_sibling and len(right_sibling.keys) > right_sibling.min_keys:
            self._borrow_from_right(node, right_sibling, parent, idx)
        elif left_sibling:
            self._merge_nodes(left_sibling, node, parent, idx - 1)
        elif right_sibling:
            self._merge_nodes(node, right_sibling, parent, idx)

    def _borrow_from_left(
        self,
        node: BPlusTreeNode,
        left_sibling: BPlusTreeNode,
        parent: BPlusTreeNode,
        parent_key_idx: int,
    ) -> None:
        """从左兄弟借一个键"""
        if node.is_leaf:
            node.keys.insert(0, left_sibling.keys[-1])
            node.values.insert(0, left_sibling.values[-1])
            left_sibling.keys.pop()
            left_sibling.values.pop()
            parent.keys[parent_key_idx] = node.keys[0]
        else:
            node.keys.insert(0, parent.keys[parent_key_idx])
            node.children.insert(0, left_sibling.children[-1])
            left_sibling.children[-1].parent = node
            parent.keys[parent_key_idx] = left_sibling.keys[-1]
            left_sibling.keys.pop()
            left_sibling.children.pop()

    def _borrow_from_right(
        self,
        node: BPlusTreeNode,
        right_sibling: BPlusTreeNode,
        parent: BPlusTreeNode,
        parent_key_idx: int,
    ) -> None:
        """从右兄弟借一个键"""
        if node.is_leaf:
            node.keys.append(right_sibling.keys[0])
            node.values.append(right_sibling.values[0])
            right_sibling.keys.pop(0)
            right_sibling.values.pop(0)
            parent.keys[parent_key_idx] = right_sibling.keys[0] if right_sibling.keys else None
        else:
            node.keys.append(parent.keys[parent_key_idx])
            node.children.append(right_sibling.children[0])
            right_sibling.children[0].parent = node
            parent.keys[parent_key_idx] = right_sibling.keys[0]
            right_sibling.keys.pop(0)
            right_sibling.children.pop(0)

    def _merge_nodes(
        self,
        left: BPlusTreeNode,
        right: BPlusTreeNode,
        parent: BPlusTreeNode,
        parent_key_idx: int,
    ) -> None:
        """合并两个节点"""
        if left.is_leaf:
            left.keys.extend(right.keys)
            left.values.extend(right.values)
            left.next = right.next
        else:
            left.keys.append(parent.keys[parent_key_idx])
            left.keys.extend(right.keys)
            left.children.extend(right.children)
            for child in right.children:
                child.parent = left

        del parent.keys[parent_key_idx]
        del parent.children[parent_key_idx + 1]

        if parent is self.root and len(parent.keys) == 0:
            if parent.children:
                self.root = parent.children[0]
                self.root.parent = None
            else:
                self.root = BPlusTreeNode(self.order, is_leaf=True)
        elif parent is not self.root and len(parent.keys) < parent.min_keys:
            self._balance_after_delete(parent)

    def range_query(
        self,
        start_key: Optional[Any] = None,
        end_key: Optional[Any] = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> List[Tuple[Any, List[str]]]:
        """
        范围查询
        
        Args:
            start_key: 起始键（None 表示从头开始）
            end_key: 结束键（None 表示到末尾）
            include_start: 是否包含起始键
            include_end: 是否包含结束键
            
        Returns:
            键值对列表 [(key, [doc_ids]), ...]
        """
        results = []

        if self._size == 0:
            return results

        if start_key is not None:
            leaf = self._find_leaf(start_key)
            start_idx = bisect.bisect_left(leaf.keys, start_key)
            if not include_start and start_idx < len(leaf.keys) and leaf.keys[start_idx] == start_key:
                start_idx += 1
        else:
            leaf = self._get_first_leaf()
            start_idx = 0

        current = leaf
        current_idx = start_idx

        while current:
            while current_idx < len(current.keys):
                key = current.keys[current_idx]

                if end_key is not None:
                    if include_end and key > end_key:
                        return results
                    if not include_end and key >= end_key:
                        return results

                results.append((key, list(current.values[current_idx])))
                current_idx += 1

            current = current.next
            current_idx = 0

        return results

    def _get_first_leaf(self) -> BPlusTreeNode:
        """获取最左边的叶子节点"""
        node = self.root
        while not node.is_leaf:
            node = node.children[0]
        return node

    def _get_last_leaf(self) -> BPlusTreeNode:
        """获取最右边的叶子节点"""
        node = self.root
        while not node.is_leaf:
            node = node.children[-1]
        return node

    def iterate(self, reverse: bool = False) -> Iterator[Tuple[Any, List[str]]]:
        """
        迭代所有键值对
        
        Args:
            reverse: 是否逆序迭代
            
        Yields:
            (key, [doc_ids])
        """
        if reverse:
            current = self._get_last_leaf()
            prev_leaf = None
            while current:
                for i in range(len(current.keys) - 1, -1, -1):
                    yield current.keys[i], list(current.values[i])
                prev_leaf = current
                current = self._find_prev_leaf(current) if prev_leaf else None
        else:
            current = self._get_first_leaf()
            while current:
                for i in range(len(current.keys)):
                    yield current.keys[i], list(current.values[i])
                current = current.next

    def _find_prev_leaf(self, leaf: BPlusTreeNode) -> Optional[BPlusTreeNode]:
        """查找前一个叶子节点（通过父节点回溯）"""
        if not leaf.parent:
            return None

        parent = leaf.parent
        idx = parent.children.index(leaf)

        if idx > 0:
            prev_child = parent.children[idx - 1]
            while not prev_child.is_leaf:
                prev_child = prev_child.children[-1]
            return prev_child
        else:
            ancestor = self._find_prev_leaf(parent)
            if ancestor:
                return ancestor
            return None

    def min_key(self) -> Optional[Any]:
        """获取最小键"""
        if self._size == 0:
            return None
        leaf = self._get_first_leaf()
        return leaf.keys[0] if leaf.keys else None

    def max_key(self) -> Optional[Any]:
        """获取最大键"""
        if self._size == 0:
            return None
        leaf = self._get_last_leaf()
        return leaf.keys[-1] if leaf.keys else None

    def __len__(self) -> int:
        return self._size

    def __repr__(self) -> str:
        return f"BPlusTree(order={self.order}, size={self._size}, values={self._value_count})"

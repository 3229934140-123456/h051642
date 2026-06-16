"""
聚合管道

聚合管道的核心实现

聚合管道原理:
- 文档依次经过多个阶段（Stage），每个阶段对输入文档进行转换，输出给下一个阶段。
- 类似 Unix 的管道 | 操作符

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

每个阶段接收文档流:
- 输入: 文档或文档列表
- 处理: 转换文档流
- 输出: 转换后的文档列表
"""

import copy
from typing import List, Dict, Any, Optional

from ..core.collection import Collection
from ..core.document import Document


class PipelineStage:
    """
    管道阶段基类
    
    所有聚合阶段的基类
    """

    STAGE_NAME = "base"

    def __init__(self, params: Any = None):
        """
        初始化阶段
        
        Args:
            params: 阶段参数
        """
        self.params = params

    def process(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理文档列表
        
        Args:
            documents: 输入文档列表
            
        Returns:
            输出文档列表
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.STAGE_NAME}({self.params})"


class AggregationPipeline:
    """
    聚合管道
    
    将多个阶段串联起来，依次处理
    
    使用方法:
    pipeline = AggregationPipeline([
        {"$match": {...}},
        {"$group": {...}},
        {"$sort": {...}},
        {"$limit": 10}
    ], collection)
    
    results = pipeline.execute()
    """

    def __init__(self, pipeline: List[Dict[str, Any]], collection: Collection):
        """
        初始化聚合管道
        
        Args:
            pipeline: 管道阶段定义列表
            collection: 集合引用
        """
        self._pipeline_def = pipeline
        self._collection = collection
        self._stages: List[PipelineStage] = self._parse_pipeline(pipeline)

    def _parse_pipeline(self, pipeline: List[Dict[str, Any]]) -> List[PipelineStage]:
        """
        解析管道定义，创建阶段对象
        
        Args:
            pipeline: 管道定义列表
            
        Returns:
            阶段对象列表
        """
        from .stages import (
            MatchStage,
            ProjectStage,
            GroupStage,
            SortStage,
            SkipStage,
            LimitStage,
            UnwindStage,
            CountStage,
            AddFieldsStage,
        )

        STAGE_MAP = {
            "$match": MatchStage,
            "$project": ProjectStage,
            "$group": GroupStage,
            "$sort": SortStage,
            "$skip": SkipStage,
            "$limit": LimitStage,
            "$unwind": UnwindStage,
            "$count": CountStage,
            "$addFields": AddFieldsStage,
        }

        stages = []

        for stage_def in pipeline:
            if not isinstance(stage_def, dict):
                raise ValueError("Invalid stage definition: must be a dict")

            if len(stage_def) != 1:
                raise ValueError("Stage must have exactly one operator")

            op_name = list(stage_def.keys())[0]
            params = stage_def[op_name]

            if op_name not in STAGE_MAP:
                raise ValueError(f"Unknown aggregation operator: {op_name}")

            stage_class = STAGE_MAP[op_name]
            stage = stage_class(params)
            stages.append(stage)

        return stages

    def execute(self) -> List[Dict[str, Any]]:
        """
        执行聚合管道
        
        Returns:
            聚合结果列表
        """
        initial_docs = self._get_initial_documents()

        current_docs = initial_docs

        for stage in self._stages:
            current_docs = stage.process(current_docs)

        return current_docs

    def _get_initial_documents(self) -> List[Dict[str, Any]]:
        """
        获取初始文档列表
        
        优化: 如果第一个阶段是 $match，可以利用索引
        """
        docs = list(self._collection.iterate())
        return [doc.to_dict() for doc in docs]

    def explain(self) -> List[Dict[str, Any]]:
        """
        解释聚合管道
        
        Returns:
            管道阶段信息列表
        """
        return [
            {"stage": stage.STAGE_NAME, "params": stage.params}
            for stage in self._stages
        ]

    def __repr__(self) -> str:
        return f"AggregationPipeline(stages={len(self._stages)})"

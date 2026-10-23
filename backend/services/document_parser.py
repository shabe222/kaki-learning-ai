"""
文档解析服务 — 支持 PDF / DOCX / TXT / MD 格式
提取纯文本 + 结构化信息（教育背景、课程、项目、技能）
"""

import logging
from typing import Dict, List, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class DocumentParser:
    """多格式文档解析器"""

    SUPPORTED_EXTENSIONS = {'.txt', '.md', '.pdf', '.docx'}

    @staticmethod
    async def parse(file_path: str) -> Dict[str, Any]:
        """解析文档，返回纯文本 + 结构化的元信息"""
        ext = Path(file_path).suffix.lower()
        logger.info(f"[DocumentParser] 开始解析: {file_path} (格式: {ext})")

        if ext not in DocumentParser.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的格式: {ext}，支持: {DocumentParser.SUPPORTED_EXTENSIONS}")

        raw_text = ""
        if ext == '.txt':
            raw_text = DocumentParser._parse_txt(file_path)
        elif ext == '.md':
            raw_text = DocumentParser._parse_md(file_path)
        elif ext == '.pdf':
            raw_text = DocumentParser._parse_pdf(file_path)
        elif ext == '.docx':
            raw_text = DocumentParser._parse_docx(file_path)

        # 提取结构化信息
        extracted = DocumentParser._extract_info(raw_text)

        logger.info(f"[DocumentParser] 解析完成，提取文本 {len(raw_text)} 字符")
        return {
            "raw_text": raw_text[:50000],  # 截断，避免过长
            "char_count": len(raw_text),
            "format": ext,
            "extracted_info": extracted
        }

    @staticmethod
    def _parse_txt(file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    @staticmethod
    def _parse_md(file_path: str) -> str:
        """解析 Markdown，去除常见标记但保留内容"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    @staticmethod
    def _parse_pdf(file_path: str) -> str:
        """使用 PyPDF2 解析 PDF"""
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            texts = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
            return "\n".join(texts)
        except ImportError:
            logger.warning("[DocumentParser] PyPDF2 未安装，返回空文本")
            return ""

    @staticmethod
    def _parse_docx(file_path: str) -> str:
        """使用 python-docx 解析 Word 文档"""
        try:
            from docx import Document
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        except ImportError:
            logger.warning("[DocumentParser] python-docx 未安装，返回空文本")
            return ""

    @staticmethod
    def _extract_info(text: str) -> Dict[str, Any]:
        """
        从文本中提取结构化信息
        使用关键词匹配做初步提取（后续可接入 LLM 做深度解析）
        """
        info = {
            "education": {},
            "courses_taken": [],
            "projects": [],
            "self_assessment": [],
            "learning_goals": []
        }

        lines = text.split('\n')

        # 教育背景关键词
        edu_keywords = ['大学', '本科', '硕士', '博士', '学院', '专业', '年级', '计算机',
                        '数学', '统计', '人工智能', '软件工程', '数据科学']
        project_keywords = ['项目', '实训', '比赛', '竞赛', 'kaggle', '天池',
                           '课设', '毕业设计', '手写数字', '房价预测', '分类', '回归']
        course_keywords = ['课程', '学过', '掌握', '熟练', 'Python', 'C++', 'Java',
                          '数据结构', '算法', '数据库', '操作系统', '机器学习', '深度学习',
                          'sklearn', 'pytorch', 'tensorflow', 'numpy', 'pandas']
        skill_keywords = ['熟练', '熟悉', '了解', '精通', '掌握', '用过', '会']

        edu_lines = []
        project_lines = []
        course_lines = []
        skill_lines = []
        goal_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if any(k in line for k in edu_keywords):
                edu_lines.append(line)
            if any(k in line for k in project_keywords):
                project_lines.append(line)
            if any(k in line for k in course_keywords):
                course_lines.append(line)
            if any(k in line for k in skill_keywords):
                skill_lines.append(line)

        if edu_lines:
            info["education"] = {"raw_lines": edu_lines[:10]}
        if course_lines:
            info["courses_taken"] = course_lines[:15]
        if project_lines:
            info["projects"] = project_lines[:10]
        if skill_lines:
            info["self_assessment"] = skill_lines[:10]

        return info


# 创建单例
document_parser = DocumentParser()
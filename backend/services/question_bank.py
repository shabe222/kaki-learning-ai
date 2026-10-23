"""
机器学习题库 — 覆盖 9 个章节，45 个知识点
支持自适应跳题逻辑
"""

from __future__ import annotations

# ===== 题库定义 =====
# 每道题包含: id, chapter, knowledge_points[], difficulty(1-5), question, options, answer(0-based), explanation

QUESTIONS = [
    # ===== 机器学习概述 (2题) =====
    {
        "id": "overview_1",
        "chapter": "ml_overview",
        "knowledge_points": ["ml_definition", "supervised_unsupervised"],
        "difficulty": 1,
        "question": "以下哪项最准确地描述了\"机器学习\"？",
        "options": [
            "计算机通过明确的规则编程来执行任务",
            "计算机从数据中自动学习模式和规律",
            "计算机模拟人类大脑的所有功能",
            "计算机执行预先定义的逻辑推理"
        ],
        "answer": 1,
        "explanation": "机器学习的核心是从数据中自动学习规律，而无需显式编程。"
    },
    {
        "id": "overview_2",
        "chapter": "ml_overview",
        "knowledge_points": ["bias_variance"],
        "difficulty": 2,
        "question": "当模型复杂度增加时，偏差和方差分别如何变化？",
        "options": [
            "偏差增大，方差减小",
            "偏差减小，方差增大",
            "偏差和方差都增大",
            "偏差和方差都减小"
        ],
        "answer": 1,
        "explanation": "模型复杂度增加时，偏差减小（拟合能力更强），但方差增大（对训练数据更敏感），这就是偏差-方差权衡。"
    },

    # ===== 数学基础 (3题) =====
    {
        "id": "math_1",
        "chapter": "math_foundation",
        "knowledge_points": ["linear_algebra"],
        "difficulty": 2,
        "question": "在机器学习中，矩阵乘法的核心作用是什么？",
        "options": [
            "仅用于数据存储",
            "实现线性变换和特征空间映射",
            "替代向量加法",
            "用于数据归一化"
        ],
        "answer": 1,
        "explanation": "矩阵乘法对应线性变换，是神经网络前向传播和特征工程（如PCA）的数学基础。"
    },
    {
        "id": "math_2",
        "chapter": "math_foundation",
        "knowledge_points": ["probability_stats"],
        "difficulty": 3,
        "question": "朴素贝叶斯分类器的'朴素'假设是什么？",
        "options": [
            "假设所有样本数量相等",
            "假设特征之间相互独立",
            "假设先验概率为均匀分布",
            "假设所有特征服从正态分布"
        ],
        "answer": 1,
        "explanation": "朴素贝叶斯假设特征条件独立，即给定类别后各特征之间相互独立，大幅简化了计算。"
    },
    {
        "id": "math_3",
        "chapter": "math_foundation",
        "knowledge_points": ["calculus", "optimization"],
        "difficulty": 3,
        "question": "梯度下降算法中，梯度的方向代表什么？",
        "options": [
            "函数值下降最快的方向的反方向",
            "函数值上升最快的方向",
            "函数的等值线方向",
            "函数的二阶导数方向"
        ],
        "answer": 1,
        "explanation": "梯度指向函数值上升最快的方向，因此梯度下降中参数更新方向为负梯度方向，即下降最快的方向。"
    },

    # ===== 编程与数据处理 (2题) =====
    {
        "id": "code_1",
        "chapter": "programming",
        "knowledge_points": ["python"],
        "difficulty": 2,
        "question": "以下 Python 列表推导式的输出是什么？\n```python\n[x**2 for x in range(5) if x % 2 == 0]\n```",
        "options": ["[0, 4, 8, 12, 16]", "[0, 1, 4, 9, 16]", "[0, 4, 16]", "[0, 2, 4]"],
        "answer": 2,
        "explanation": "x 取 0,1,2,3,4，仅偶数 0,2,4 通过条件筛选，平方得 [0, 4, 16]。"
    },
    {
        "id": "code_2",
        "chapter": "programming",
        "knowledge_points": ["data_preprocessing", "sklearn"],
        "difficulty": 2,
        "question": "在scikit-learn中，对特征进行标准化（均值为0，标准差为1）应使用哪个类？",
        "options": [
            "sklearn.preprocessing.MinMaxScaler",
            "sklearn.preprocessing.StandardScaler",
            "sklearn.preprocessing.Normalizer",
            "sklearn.decomposition.PCA"
        ],
        "answer": 1,
        "explanation": "StandardScaler 对每个特征减去均值再除以标准差，实现Z-score标准化。MinMaxScaler做归一化到[0,1]，Normalizer按样本归一化。"
    },

    # ===== 监督学习 (5题) =====
    {
        "id": "sup_1",
        "chapter": "supervised_learning",
        "knowledge_points": ["linear_regression", "logistic_regression"],
        "difficulty": 2,
        "question": "线性回归和逻辑回归最主要的区别是什么？",
        "options": [
            "线性回归只能处理一维数据",
            "逻辑回归用于分类问题，线性回归用于回归问题",
            "逻辑回归不需要训练",
            "线性回归使用梯度下降，逻辑回归不使用"
        ],
        "answer": 1,
        "explanation": "线性回归预测连续值（回归），逻辑回归通过 sigmoid 函数输出概率用于分类。"
    },
    {
        "id": "sup_2",
        "chapter": "supervised_learning",
        "knowledge_points": ["naive_bayes"],
        "difficulty": 2,
        "question": "朴素贝叶斯分类器在文本分类中表现优秀的主要原因是什么？",
        "options": [
            "它能捕捉特征之间的复杂交互",
            "特征独立假设在文本场景下近似成立且计算高效",
            "它使用了深度神经网络",
            "它不需要任何训练数据"
        ],
        "answer": 1,
        "explanation": "文本分类中词的特征近似独立，朴素贝叶斯的条件独立假设恰好适用，且计算复杂度低，适合高维稀疏数据。"
    },
    {
        "id": "sup_3",
        "chapter": "supervised_learning",
        "knowledge_points": ["knn"],
        "difficulty": 2,
        "question": "KNN算法中，K值过小会导致什么问题？",
        "options": [
            "欠拟合",
            "过拟合，对噪声敏感",
            "计算速度变慢",
            "模型无法收敛"
        ],
        "answer": 1,
        "explanation": "K值过小时，模型只关注最近的少数样本，容易受噪声影响，导致过拟合。K值过大则可能导致欠拟合。"
    },
    {
        "id": "sup_4",
        "chapter": "supervised_learning",
        "knowledge_points": ["svm"],
        "difficulty": 4,
        "question": "SVM 中核函数的核心作用是什么？",
        "options": [
            "加速模型训练",
            "将数据映射到高维空间使线性可分",
            "减少模型参数数量",
            "替代损失函数"
        ],
        "answer": 1,
        "explanation": "核函数通过隐式映射将低维不可分数据投影到高维空间，在高维空间寻找最优分离超平面。"
    },
    {
        "id": "sup_5",
        "chapter": "supervised_learning",
        "knowledge_points": ["ensemble"],
        "difficulty": 3,
        "question": "随机森林和GBDT的主要区别是什么？",
        "options": [
            "随机森林只能分类，GBDT只能回归",
            "随机森林是Bagging（并行），GBDT是Boosting（串行）",
            "它们是完全相同的算法",
            "随机森林比GBDT总是更准确"
        ],
        "answer": 1,
        "explanation": "随机森林用 Bagging 并行训练多棵决策树后投票/平均；GBDT 用 Boosting 串行训练，每棵树拟合前一棵的残差。"
    },

    # ===== 无监督学习 (2题) =====
    {
        "id": "unsup_1",
        "chapter": "unsupervised_learning",
        "knowledge_points": ["kmeans", "pca"],
        "difficulty": 3,
        "question": "关于K-means和PCA，以下说法正确的是？",
        "options": [
            "两者都是分类算法",
            "K-means用于聚类，PCA用于降维",
            "PCA需要标签，K-means不需要",
            "两者都属于监督学习"
        ],
        "answer": 1,
        "explanation": "K-means 是无监督聚类算法，PCA 是无监督降维方法，两者都不需要标签数据。"
    },
    {
        "id": "unsup_2",
        "chapter": "unsupervised_learning",
        "knowledge_points": ["dbscan"],
        "difficulty": 3,
        "question": "相比K-Means，DBSCAN的主要优势是什么？",
        "options": [
            "计算速度更快",
            "能发现任意形状的簇且不需要预设簇数",
            "总是产生更好的聚类结果",
            "适用于所有类型的数据"
        ],
        "answer": 1,
        "explanation": "DBSCAN基于密度聚类，能发现任意形状的簇，且自动确定簇的数量，不需要像K-Means那样预设K值。"
    },

    # ===== 神经网络基础 (2题) =====
    {
        "id": "nn_1",
        "chapter": "neural_networks",
        "knowledge_points": ["perceptron", "backpropagation"],
        "difficulty": 3,
        "question": "反向传播算法的核心思想是什么？",
        "options": [
            "随机初始化参数",
            "利用链式法则从输出层逐层向前计算梯度",
            "直接计算每一层的独立梯度",
            "使用随机搜索优化参数"
        ],
        "answer": 1,
        "explanation": "反向传播利用链式法则，从输出层开始逐层反向计算损失函数对各层参数的梯度，是神经网络训练的核心算法。"
    },
    {
        "id": "nn_2",
        "chapter": "neural_networks",
        "knowledge_points": ["activation_optimizer"],
        "difficulty": 3,
        "question": "Adam优化器相比SGD的主要改进是什么？",
        "options": [
            "不需要学习率",
            "自适应调整每个参数的学习率，结合动量和RMSprop",
            "只使用一阶梯度信息",
            "训练速度一定更快"
        ],
        "answer": 1,
        "explanation": "Adam结合了动量法（Momentum）和RMSprop，为每个参数自适应地调整学习率，通常收敛更快更稳定。"
    },

    # ===== 深度学习 (3题) =====
    {
        "id": "dl_1",
        "chapter": "deep_learning",
        "knowledge_points": ["cnn"],
        "difficulty": 3,
        "question": "CNN中卷积层的主要作用是提取哪种特征？",
        "options": [
            "全局语义特征",
            "局部空间特征（边缘、纹理等）",
            "时间序列特征",
            "类别标签"
        ],
        "answer": 1,
        "explanation": "卷积核在图像上滑动，提取局部空间特征（边缘、角点、纹理），深层卷积可提取更抽象的高级特征。"
    },
    {
        "id": "dl_2",
        "chapter": "deep_learning",
        "knowledge_points": ["transformer"],
        "difficulty": 4,
        "question": "Transformer中的自注意力（Self-Attention）机制的核心思想是什么？",
        "options": [
            "只关注相邻的词",
            "通过Query-Key-Value计算序列中每个位置对所有位置的注意力权重",
            "使用卷积提取局部特征",
            "通过递归方式处理序列"
        ],
        "answer": 1,
        "explanation": "自注意力通过Q-K-V机制计算序列中每个位置对其他所有位置的关联度（注意力权重），实现全局依赖建模，是Transformer的核心。"
    },
    {
        "id": "dl_3",
        "chapter": "deep_learning",
        "knowledge_points": ["transfer_learning"],
        "difficulty": 3,
        "question": "迁移学习中'微调（Fine-tuning）'的含义是什么？",
        "options": [
            "从头开始训练模型",
            "在预训练模型基础上，用新数据继续训练部分或全部层",
            "只使用模型最后一层",
            "减小模型参数量"
        ],
        "answer": 1,
        "explanation": "微调是在预训练模型的基础上，用目标领域的数据继续训练，调整部分或全部层的参数，使模型适应新任务。"
    },

    # ===== 模型评估与调优 (2题) =====
    {
        "id": "eval_1",
        "chapter": "model_evaluation",
        "knowledge_points": ["overfitting", "regularization"],
        "difficulty": 3,
        "question": "当模型在训练集准确率很高但测试集准确率很低时，这种现象叫什么？如何缓解？",
        "options": [
            "欠拟合 — 增加模型复杂度",
            "过拟合 — 使用正则化、增加数据或简化模型",
            "正常现象 — 不需要处理",
            "数据泄露 — 重新划分数据集"
        ],
        "answer": 1,
        "explanation": "训练集高、测试集低是典型的过拟合。可通过 L1/L2 正则化、Dropout、数据增强、早停等策略缓解。"
    },
    {
        "id": "eval_2",
        "chapter": "model_evaluation",
        "knowledge_points": ["feature_engineering", "cross_validation"],
        "difficulty": 2,
        "question": "特征工程的核心目标是什么？",
        "options": [
            "增加特征数量",
            "从原始数据中提取更有信息量的特征以提升模型性能",
            "删除所有缺失值",
            "只使用数值型特征"
        ],
        "answer": 1,
        "explanation": "特征工程是从原始数据中构建更有表达力的特征，包括特征构造、选择、变换等，是提升模型性能的关键步骤。"
    },

    # ===== 进阶方向兴趣探测 (1题) =====
    {
        "id": "adv_1",
        "chapter": "advanced_interest",
        "knowledge_points": ["nlp", "cv", "llm", "reinforcement_learning", "recommendation", "mlops"],
        "difficulty": 1,
        "question": "以下哪个机器学习子方向你最感兴趣或有过接触？",
        "options": [
            "自然语言处理 (NLP) — 文本分类、GPT等",
            "计算机视觉 (CV) — 图像识别、目标检测等",
            "大语言模型 (LLM) — ChatGPT、微调、Prompt工程",
            "推荐系统 / MLOps — 个性化推荐、模型部署"
        ],
        "answer": -1,
        "explanation": "此题不计分，用于探测学习兴趣方向，帮助后续推荐学习内容。"
    }
]


# ===== 章节分配规则 =====
CHAPTER_ALLOCATION = {
    "ml_overview": {"total": 2, "required": 1},
    "math_foundation": {"total": 3, "required": 1},
    "programming": {"total": 2, "required": 1},
    "supervised_learning": {"total": 5, "required": 2},
    "unsupervised_learning": {"total": 2, "required": 1},
    "neural_networks": {"total": 2, "required": 1},
    "deep_learning": {"total": 3, "required": 2},
    "model_evaluation": {"total": 2, "required": 1},
    "advanced_interest": {"total": 1, "required": 1}
}

# 知识点→章节映射（用于score计算）
KNOWLEDGE_POINT_TO_CHAPTER = {}
for q in QUESTIONS:
    for kp in q["knowledge_points"]:
        KNOWLEDGE_POINT_TO_CHAPTER[kp] = q["chapter"]

# 所有45个知识点列表
ALL_KNOWLEDGE_POINTS = {
    "ml_overview": ["ml_definition", "supervised_unsupervised", "data_split", "bias_variance"],
    "math_foundation": ["linear_algebra", "probability_stats", "calculus", "optimization", "information_theory"],
    "programming": ["python", "numpy", "pandas", "data_preprocessing", "sklearn"],
    "supervised_learning": ["linear_regression", "logistic_regression", "naive_bayes", "knn", "decision_tree", "svm", "ensemble"],
    "unsupervised_learning": ["kmeans", "hierarchical_clustering", "dbscan", "pca", "tsne"],
    "neural_networks": ["perceptron", "backpropagation", "activation_optimizer", "dl_framework"],
    "deep_learning": ["cnn", "rnn_lstm", "transformer", "gan", "transfer_learning"],
    "model_evaluation": ["overfitting", "cross_validation", "metrics", "regularization", "feature_engineering", "hyperparameter_tuning"],
    "advanced_interest": ["nlp", "cv", "llm", "reinforcement_learning", "recommendation", "mlops"]
}


def get_questions_by_chapter(chapter: str) -> list:
    """获取某章节的所有题目"""
    return [q for q in QUESTIONS if q["chapter"] == chapter]


def get_question(question_id: str) -> dict | None:
    """根据 ID 获取题目详情"""
    for q in QUESTIONS:
        if q["id"] == question_id:
            return q
    return None


def get_all_chapters() -> list:
    """获取所有章节"""
    return list(CHAPTER_ALLOCATION.keys())


def get_chapter_names() -> dict:
    return {
        "ml_overview": "机器学习概述",
        "math_foundation": "数学基础",
        "programming": "编程与数据处理",
        "supervised_learning": "监督学习",
        "unsupervised_learning": "无监督学习",
        "neural_networks": "神经网络基础",
        "deep_learning": "深度学习",
        "model_evaluation": "模型评估与调优",
        "advanced_interest": "进阶方向"
    }
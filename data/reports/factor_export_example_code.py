"""自动生成的因子代码示例文件。

每个函数接收 df(DataFrame) 并返回因子序列。
"""

from backend.services.factor_service import factor_service

# ===== 因子 01: sqrt(low + close) =====
def calculate_factor_01(df):
    """计算因子：sqrt(low + close)"""
    return factor_service.calculator.calculate(df, 'sqrt(low + close)')

# ===== 因子 02: (open - low) =====
def calculate_factor_02(df):
    """计算因子：(open - low)"""
    return factor_service.calculator.calculate(df, '(open - low)')

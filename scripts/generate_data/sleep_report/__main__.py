"""直接运行生成睡眠报告: python -m scripts.generate_data.sleep_report"""

from .generate import generate_sleep_reports

if __name__ == "__main__":
    generate_sleep_reports()

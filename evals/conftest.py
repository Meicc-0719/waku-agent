import sys
from pathlib import Path

# evals/ 位于 waku/ 旁边，而不是在其中 - 使两者都可以导入
# 从存储库根目录运行“pytest evals”。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

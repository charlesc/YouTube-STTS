import os
import sys

# 確保無論從哪個目錄呼叫 pytest，都能以 repo 根目錄為基準匯入
# main.py / database.py / utils/*，並讓相對路徑（如 videos.db、static/screenshots）行為一致。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

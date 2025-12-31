import os, sys

def resource_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.abspath("."), filename)


def get_base_dir():
    """PyInstaller 환경에서도 실행 파일 기준 경로 찾기"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)  # exe 위치
    else:
        return os.path.dirname(os.path.abspath(__file__))  # 개발 환경
import subprocess
import sys

print(f'正在检查ffmpeg的状态...')
try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    print(f'ffmpeg状态正常...')
except Exception:
    print(f'ffmpeg无法被调用, 请检查环境变量或前往官网下载, 程序将正常退出...')
    sys.exit(0)


import subprocess
import sys
import os

def main():
    print("=== 開始執行宏觀經濟數據更新任務 ===")
    
    # 執行人民幣流動性板塊的腳本
    script_path = os.path.join("rmb_liquidity", "liquidity.py")
    print(f"正在執行: {script_path}")
    
    try:
        subprocess.run([sys.executable, script_path], check=True)
        print("人民幣流動性數據更新成功！")
    except subprocess.CalledProcessError as e:
        print(f"執行失敗，錯誤代碼: {e.returncode}")
        sys.exit(1)
        
    print("=== 所有任務執行完畢 ===")

if __name__ == "__main__":
    main()
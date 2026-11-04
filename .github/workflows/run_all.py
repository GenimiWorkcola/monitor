import subprocess
import sys
import os

def main():
    print("=== 開始執行宏觀經濟數據更新任務 ===")
    
    # 獲取專案根目錄路徑（即 workflows 的上兩層）
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # 定義要依序執行的腳本相對路徑
    scripts = [
        os.path.join(base_dir, "rmb_liquidity", "update_moneyindex.py"),
        os.path.join(base_dir, "rmb_liquidity", "liquidity.py"),
        os.path.join(base_dir, "usd_liquidity", "usd_liquidity.py")
    ]
    
    for script_path in scripts:
        print(f"正在執行: {script_path}")
        try:
            subprocess.run([sys.executable, script_path], check=True)
            print(f"{script_path} 執行成功！\n")
        except subprocess.CalledProcessError as e:
            print(f"執行失敗 ({script_path})，錯誤代碼: {e.returncode}")
            sys.exit(1)
            
    print("=== 所有任務執行完畢 ===")

if __name__ == "__main__":
    main()
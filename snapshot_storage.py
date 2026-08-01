import os
import json
import re
from datetime import datetime
import config

def get_snapshot_dir_for_date(date_str):
    """
    Returns the path to the daily snapshots directory.
    date_str: 'YYYYMMDD'
    """
    daily_dir = os.path.join(config.SNAPSHOT_DIR, date_str)
    if not os.path.exists(daily_dir):
        os.makedirs(daily_dir)
    return daily_dir

def clean_timestamp_for_filename(timestamp_str):
    """
    Converts a timestamp like '31-Jul-2026 15:30:11' to '20260731_153011'.
    """
    try:
        # Parse standard NSE format
        try:
            dt = datetime.strptime(timestamp_str.strip(), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            dt = datetime.strptime(timestamp_str.strip(), "%d-%b-%Y %H:%M")
        
        return dt.strftime("%Y%m%d_%H%M%S"), dt.strftime("%Y%m%d")
    except Exception as e:
        # Fallback to current time if parsing fails
        now = datetime.now()
        print(f"[-] Warning: Failed to parse timestamp '{timestamp_str}' for filename: {e}. Using current time.")
        return now.strftime("%Y%m%d_%H%M%S"), now.strftime("%Y%m%d")

def save_snapshot(data):
    """
    Saves the normalized snapshot JSON to the daily directory.
    Returns the absolute path of the saved file.
    """
    timestamp_str = data.get("timestamp")
    formatted_ts, date_folder = clean_timestamp_for_filename(timestamp_str)
    
    daily_dir = get_snapshot_dir_for_date(date_folder)
    filename = f"NIFTY_SNAPSHOT_{formatted_ts}.json"
    file_path = os.path.join(daily_dir, filename)
    
    # Write JSON data
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print(f"[+] Snapshot saved successfully to: {file_path}")
    
    # Run historical data pruning
    try:
        prune_old_snapshots()
    except Exception as e:
        print(f"[-] Warning: Failed to prune old snapshots: {e}")
        
    return file_path

def prune_old_snapshots():
    """
    Scans the snapshots/ directory, parses all YYYYMMDD date subdirectories,
    and deletes folders (with all snapshots inside) older than 30 days.
    """
    if not os.path.exists(config.SNAPSHOT_DIR):
        return
        
    import shutil
    from datetime import datetime, timedelta
    
    threshold_dt = datetime.now() - timedelta(days=30)
    print(f"[*] Running data retention check. Deleting snapshots older than: {threshold_dt.strftime('%d-%b-%Y')}")
    
    deleted_count = 0
    for folder_name in os.listdir(config.SNAPSHOT_DIR):
        folder_path = os.path.join(config.SNAPSHOT_DIR, folder_name)
        if os.path.isdir(folder_path) and re.match(r"^\d{8}$", folder_name):
            try:
                folder_dt = datetime.strptime(folder_name, "%Y%m%d")
                if folder_dt < threshold_dt:
                    print(f"[-] Pruning stale snapshot directory: {folder_path}")
                    shutil.rmtree(folder_path)
                    deleted_count += 1
            except ValueError:
                continue
                
    if deleted_count > 0:
        print(f"[+] Successfully pruned {deleted_count} daily directories.")
    else:
        print("[+] Retention check complete. No directories pruned.")

def get_all_snapshots_for_date(date_folder):
    """
    Retrieves a list of all snapshot file paths for a specific date folder (sorted chronologically).
    date_folder: e.g. '20260731' or '20260801'
    """
    daily_dir = os.path.join(config.SNAPSHOT_DIR, date_folder)
    if not os.path.exists(daily_dir):
        return []
        
    files = [os.path.join(daily_dir, f) for f in os.listdir(daily_dir) if f.startswith("NIFTY_SNAPSHOT_") and f.endswith(".json")]
    
    # Sort files chronologically based on their filename timestamp
    def extract_time(path):
        match = re.search(r"NIFTY_SNAPSHOT_\d{8}_(\d{6})", os.path.basename(path))
        return match.group(1) if match else ""
        
    files.sort(key=extract_time)
    return files

def get_latest_snapshot_before(current_timestamp_str):
    """
    Scans the snapshots/ directory, parses all filenames, and returns the path
    to the most recent snapshot file that is strictly older than the current timestamp.
    """
    try:
        try:
            current_dt = datetime.strptime(current_timestamp_str.strip(), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            current_dt = datetime.strptime(current_timestamp_str.strip(), "%d-%b-%Y %H:%M")
    except Exception as e:
        print(f"[-] Error parsing current timestamp '{current_timestamp_str}': {e}")
        return None

    if not os.path.exists(config.SNAPSHOT_DIR):
        return None

    all_snapshots = []
    # Scan all date subdirectories under snapshots/
    for folder_name in os.listdir(config.SNAPSHOT_DIR):
        folder_path = os.path.join(config.SNAPSHOT_DIR, folder_name)
        if os.path.isdir(folder_path) and re.match(r"^\d{8}$", folder_name):
            for file_name in os.listdir(folder_path):
                if file_name.startswith("NIFTY_SNAPSHOT_") and file_name.endswith(".json"):
                    match = re.search(r"NIFTY_SNAPSHOT_(\d{8})_(\d{6})", file_name)
                    if match:
                        file_dt_str = f"{match.group(1)}_{match.group(2)}"
                        try:
                            file_dt = datetime.strptime(file_dt_str, "%Y%m%d_%H%M%S")
                            all_snapshots.append((file_dt, os.path.join(folder_path, file_name)))
                        except ValueError:
                            continue

    # Sort snapshots chronologically
    all_snapshots.sort(key=lambda x: x[0])

    # Filter for snapshots strictly before the current datetime
    older_snapshots = [item for item in all_snapshots if item[0] < current_dt]

    if older_snapshots:
        # Return the path of the most recent one (last in the sorted list)
        return older_snapshots[-1][1]
    return None


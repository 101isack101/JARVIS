import os

def strip_bom_from_file(file_path):
    # Read the first few bytes to see if it starts with the UTF-8 BOM
    with open(file_path, 'rb') as f:
        first_bytes = f.read(3)
    
    if first_bytes == b'\xef\xbb\xbf':
        # File has BOM, read with utf-8-sig to auto-strip it and write back as utf-8
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        print(f"Stripped BOM from: {file_path}")
        return True
    return False

def main():
    target_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Scanning directory: {target_dir} for UTF-8 BOM...")
    
    fixed_count = 0
    total_py_files = 0
    
    for root, dirs, files in os.walk(target_dir):
        # Skip venv/git/pycache folders
        if any(x in root for x in [".venv", "venv", ".git", "__pycache__"]):
            continue
            
        for file in files:
            if file.endswith('.py'):
                total_py_files += 1
                file_path = os.path.join(root, file)
                try:
                    if strip_bom_from_file(file_path):
                        fixed_count += 1
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")
                    
    print(f"Finished. Scanned {total_py_files} files, stripped BOM from {fixed_count} files.")

if __name__ == '__main__':
    main()
